"""ALC: interactive and scriptable Android device management."""

import argparse
import os
import shlex
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import devices
from selection import parse_device_ids

try:
    from ui import UI, CancelAction, OperationResult
except ModuleNotFoundError as exc:
    if exc.name not in {"rich", "questionary", "prompt_toolkit"}:
        raise
    print(
        "缺少界面依赖。请执行：python3 -m pip install -r "
        + str(Path(__file__).with_name("requirements.txt")),
        file=sys.stderr,
    )
    raise SystemExit(1) from None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEVICES_PATH = PROJECT_ROOT / "res" / "devices.json"
CONFIG_UPLOAD_PATH = PROJECT_ROOT / "res" / "config_debug_upload.json"
SCP_SERVER = os.environ.get("ALC_SCP_SERVER", "dsm:/volume1/main/app")


def positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("请输入大于 0 的整数") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("请输入大于 0 的整数")
    return number


def resolution(value):
    parts = value.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("分辨率格式：宽x高，例如 1280x720")
    return tuple(positive_int(part) for part in parts)


def existing_apk(value):
    # Strip the quotes commonly included when pasting a dragged file path.
    path = Path(value.strip().strip("\"'")).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".apk":
        raise ValueError(f"请选择存在的 .apk 文件：{path}")
    return path


def prepare_config(template, device, cloud_server=None):
    if not device.token:
        raise ValueError(f"设备 {device.id} 未配置 token，不能推送配置")
    data = dict(template)
    data["cloud_device_token"] = device.token
    if cloud_server is not None:
        data["cloud_server"] = cloud_server
    return data


def load_devices(path, *, timeout=60):
    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError(f"找不到设备配置：{path}；请参照 res/devices.example.json 创建配置")
    data = devices.load_json(path)
    if not isinstance(data, list):
        raise ValueError("设备配置必须是 JSON 数组")
    result = {}
    names = set()
    addresses = set()
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条设备配置必须是对象")
        device_id = item.get("id")
        if isinstance(device_id, bool) or not isinstance(device_id, int) or device_id <= 0:
            raise ValueError(f"第 {index} 条设备配置的 id 必须是大于 0 的整数")
        if device_id in result:
            raise ValueError(f"设备 ID 重复：{device_id}")
        name, address, token = item.get("name"), item.get("ip"), item.get("token", "")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name in {".", ".."}
            or any(char in name for char in "/\\\0")
        ):
            raise ValueError(f"设备 {device_id} 的 name 必须是有效的目录名")
        if name in names:
            raise ValueError(f"设备名称重复：{name}，本地配置目录不能共用")
        if (
            not isinstance(address, str)
            or not address.strip()
            or any(char.isspace() for char in address)
            or address.startswith("-")
        ):
            raise ValueError(f"设备 {device_id} 的 ip 必须是有效的 ADB 网络地址")
        if not isinstance(token, str):
            raise ValueError(f"设备 {device_id} 的 token 必须是字符串")
        device = devices.Device(device_id, name, address, token, timeout=timeout)
        if device.serial in addresses:
            raise ValueError(f"设备 {device_id} 的 ADB 地址重复：{device.serial}")
        result[device_id] = device
        names.add(name)
        addresses.add(device.serial)
    if not result:
        raise ValueError("设备列表为空，请在设备配置中至少添加一台设备")
    return dict(sorted(result.items()))


class Application:
    def __init__(self, args, ui):
        self.args = args
        self.ui = ui
        self.device_map = load_devices(args.devices_file, timeout=args.timeout)
        self.selected = []
        self.states = {}
        self.status_error = None

    def refresh_states(self):
        self.status_error = None
        try:
            output = devices.run_command(["adb", "devices"], timeout=10).stdout
            self.states = {}
            for line in output.splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 2 and not line.startswith("*"):
                    self.states[fields[0]] = fields[1]
        except devices.DeviceError as exc:
            self.status_error = str(exc)
            self.states = {device.serial: "unknown" for device in self.device_map.values()}
            self.ui.info(f"无法读取设备状态：{exc}", style="warning")
        return self.states

    def targets(self, spec):
        return [self.device_map[number] for number in parse_device_ids(spec, self.device_map)]

    def pick(self, *, single=False):
        if self.ui.arrows:
            ids = self.ui.pick_devices(list(self.device_map.values()), self.selected, single=single)
            if ids is not None:
                self.selected = ids
                return [self.device_map[number] for number in ids]
        while True:
            default = ",".join(map(str, self.selected)) if self.selected else None
            spec = self.ui.text("设备 ID（1,3,5-8 / 0 全部 / q 返回）", default=default)
            try:
                targets = self.targets(spec)
                if single and len(targets) != 1:
                    raise ValueError("此操作需要选择一台设备")
                self.selected = [device.id for device in targets]
                return targets
            except ValueError as exc:
                self.ui.error(exc)

    def confirm(self, title, targets, *, required=False, detail=None):
        self.ui.target_summary(title, targets)
        if detail:
            self.ui.info(detail, style="warning")
        if not required or self.args.yes:
            return True
        if not sys.stdin.isatty():
            raise ValueError("此操作需要确认；在脚本中执行请显式添加 --yes")
        if not self.ui.confirm("确认执行？", default=False):
            self.ui.info("已取消", style="muted")
            return False
        return True

    def batch(self, title, targets, action, *, connect=True):
        if not targets:
            raise ValueError("没有可操作的目标设备")
        jobs = min(self.args.jobs, len(targets))
        self.ui.info(f"{title} · {len(targets)} 台 · 并发 {jobs}", style="accent")
        # Avoid reconnecting authorized live transports on every operation.
        if connect:
            self.refresh_states()
        states = dict(self.states)
        cancelled = threading.Event()

        def perform(device):
            started = time.monotonic()
            try:
                with devices.cancellable_commands(cancelled):
                    if cancelled.is_set():
                        raise devices.DeviceError("操作已中断")
                    if connect and states.get(device.serial) != "device":
                        device.connect()
                    output = action(device)
                return OperationResult(
                    device, output=str(output or "完成"), elapsed=time.monotonic() - started
                )
            except Exception as exc:
                return OperationResult(
                    device, error=str(exc) or type(exc).__name__, elapsed=time.monotonic() - started
                )

        results = []
        executor = ThreadPoolExecutor(max_workers=jobs)
        try:
            with self.ui.progress() as progress:
                task = progress.add_task(title, total=len(targets))
                futures = [executor.submit(perform, device) for device in targets]
                for future in as_completed(futures):
                    results.append(future.result())
                    progress.advance(task)
        except BaseException:
            cancelled.set()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
        order = {device.id: index for index, device in enumerate(targets)}
        results.sort(key=lambda result: order[result.device.id])
        self.ui.results(title, results)
        return 1 if any(result.error for result in results) else 0

    def upload_config(self, path, targets, cloud_server=None):
        template = devices.read_config(path)
        if not self.confirm(
            "推送配置",
            targets,
            required=True,
            detail=f"模板：{path}；每台设备保留自己的 cloud_device_token",
        ):
            return 0
        return self.batch(
            "推送配置",
            targets,
            lambda device: device.save(
                "config_debug.json", prepare_config(template, device, cloud_server)
            ),
        )

    def sync_config(self, template_id, targets, *, include_template=False, cloud_server=None):
        if template_id not in self.device_map:
            raise ValueError(f"模板设备 {template_id} 不在配置中")
        source = self.device_map[template_id]
        if not include_template:
            targets = [device for device in targets if device.id != template_id]
        if not targets:
            raise ValueError("排除模板设备后没有推送目标，请重新选择目标或添加 --include-template")
        if not self.confirm(
            "同步配置",
            targets,
            required=True,
            detail=f"模板设备：{source.id} · {source.name}；将覆盖目标设备配置",
        ):
            return 0
        self.ui.info(f"拉取模板：{source.name}", style="accent")
        source.connect()
        template = source.load("config_debug.json")
        upload_template = dict(template)
        if cloud_server is not None:
            upload_template["cloud_server"] = cloud_server
        devices.backup_file(CONFIG_UPLOAD_PATH)
        devices.save_json(CONFIG_UPLOAD_PATH, upload_template)
        self.ui.info(f"模板已保存：{CONFIG_UPLOAD_PATH}")
        return self.batch(
            "同步配置",
            targets,
            lambda device: device.save(
                "config_debug.json", prepare_config(template, device, cloud_server)
            ),
        )

    def install(
        self, path, targets, *, via_server=False, upload=False, file_server=None, scp_server=None
    ):
        path = Path(path).expanduser()
        if upload and not via_server:
            raise ValueError("--upload 需要同时使用 --via-server")
        if not via_server or upload:
            path = existing_apk(str(path))
        elif path.suffix.lower() != ".apk":
            raise ValueError("文件名必须以 .apk 结尾")
        if not self.confirm(
            "安装 APK",
            targets,
            required=True,
            detail=f"文件：{path}；方式：{'设备从文件服务器下载' if via_server else 'ADB 直接安装'}",
        ):
            return 0
        if upload:
            destination = scp_server or SCP_SERVER
            self.ui.info(f"上传到文件服务器：{destination}")
            devices.run_command(["scp", str(path), destination], timeout=300, capture=False)
        return self.batch(
            "安装 APK",
            targets,
            lambda device: device.install_apk(path, via_server=via_server, file_server=file_server),
        )

    def package_action(self, action, package, targets):
        package = devices.validate_package(package)
        title = "卸载应用" if action == "uninstall" else "禁用应用"
        if not self.confirm(title, targets, required=True, detail=f"应用包名：{package}"):
            return 0
        return self.batch(
            title,
            targets,
            lambda device: (
                device.uninstall_apk(package)
                if action == "uninstall"
                else device.disable_package(package)
            ),
        )

    def change_resolution(self, targets, *, size=None, dpi=None, reset=False):
        if reset and (size or dpi is not None):
            raise ValueError("--reset 不能与 --size / --dpi 同时使用")
        changing = reset or size is not None or dpi is not None
        title = "恢复分辨率" if reset else "设置分辨率" if changing else "查看分辨率"
        detail = (
            "恢复系统默认值"
            if reset
            else f"分辨率：{size[0]}x{size[1]}"
            if size
            else "分辨率保持当前值"
        )
        if dpi is not None:
            detail += f" · DPI：{dpi}"
        if not self.confirm(title, targets, required=changing, detail=detail if changing else None):
            return 0

        def action(device):
            if reset:
                return device.resolution_reset()
            if size:
                device.vm_set(*size)
            if dpi is not None:
                device.density_set(dpi)
            return device.vm_show() + "\n" + device.density_show()

        return self.batch(title, targets, action)

    def reboot(self, targets):
        if not self.confirm("重启设备", targets, required=True):
            return 0
        return self.batch("重启设备", targets, lambda device: device.reboot())

    def busybox(self, targets, binary=None):
        path = Path(binary or PROJECT_ROOT / "res" / "busybox").expanduser()
        if not path.is_file():
            raise ValueError(
                f"找不到 BusyBox：{path}；可执行 python3 src/busybox_installer.py 下载"
            )
        if not self.confirm(
            "安装 BusyBox",
            targets,
            required=True,
            detail="需要设备 root 权限；使用现有 ARMv7 二进制和设备专用 system 挂载路径",
        ):
            return 0
        return self.batch("安装 BusyBox", targets, lambda device: device.install_busybox(path))

    def shell(self, targets, command, *, root=False):
        if not command.strip():
            raise ValueError("命令不能为空")
        title = "执行 root 命令" if root else "执行命令"
        if not self.confirm(title, targets, required=True, detail=f"命令：{command}"):
            return 0
        return self.batch(
            title, targets, lambda device: device.root_sh(command) if root else device.sh(command)
        )

    def restart(self, targets, *, upload=False, config=None, show_ui=False, stop_game=False):
        if config is not None and not upload:
            raise ValueError("--config 需要同时使用 --upload")
        template = devices.read_config(config or CONFIG_UPLOAD_PATH) if upload else None
        title = "覆写配置并重启速通" if upload else "重启速通"
        if not self.confirm(
            title,
            targets,
            required=True,
            detail=f"模板：{config or CONFIG_UPLOAD_PATH}" if upload else None,
        ):
            return 0
        return self.batch(
            title,
            targets,
            lambda device: device.restart_al(
                upload=upload, template=template, hide=not show_ui, game=stop_game
            ),
        )

    def show(self, target):
        if shutil.which("scrcpy") is None:
            raise devices.DeviceError("找不到 scrcpy，请先安装并加入 PATH")
        self.ui.info(f"投屏：{target.name} · {target.serial}", style="accent")
        if self.refresh_states().get(target.serial) != "device":
            target.connect()
        target.show()
        return 0

    def dispatch(self):
        args = self.args
        if args.command == "list":
            self.ui.device_table(self.device_map.values(), self.refresh_states())
            return 1 if self.status_error else 0
        if args.command == "menu":
            return self.menu()
        if args.command == "show":
            if args.device_id not in self.device_map:
                raise ValueError(f"设备 {args.device_id} 不在配置中")
            return self.show(self.device_map[args.device_id])
        targets = self.targets(args.targets)
        if args.command == "connect":
            return self.batch("连接设备", targets, lambda device: device.connect(), connect=False)
        if args.command == "install":
            return self.install(
                args.apk,
                targets,
                via_server=args.via_server,
                upload=args.upload,
                file_server=args.file_server,
                scp_server=args.scp_server,
            )
        if args.command in {"uninstall", "disable"}:
            return self.package_action(args.command, args.package, targets)
        if args.command == "resolution":
            return self.change_resolution(targets, size=args.size, dpi=args.dpi, reset=args.reset)
        if args.command == "reboot":
            return self.reboot(targets)
        if args.command == "busybox":
            return self.busybox(targets, args.binary)
        if args.command == "shell":
            parts = args.shell_command
            if parts and parts[0] == "--":
                parts = parts[1:]
            command = parts[0] if len(parts) == 1 else shlex.join(parts)
            return self.shell(targets, command, root=args.root)
        if args.command == "restart":
            return self.restart(
                targets,
                upload=args.upload,
                config=args.config,
                show_ui=args.show_ui,
                stop_game=args.stop_game,
            )
        if args.command == "config":
            if args.config_action == "push":
                return self.upload_config(args.file, targets, args.cloud_server)
            return self.batch("拉取配置", targets, lambda device: self.pull_config(device))
        if args.command == "sync-config":
            return self.sync_config(
                args.template_device,
                targets,
                include_template=args.include_template,
                cloud_server=args.cloud_server,
            )
        raise ValueError(f"未知命令：{args.command}")

    @staticmethod
    def pull_config(device):
        device.load("config_debug.json")
        return str(device.path / "config_debug.json")

    def apk_menu(self):
        choice = self.ui.choose(
            "应用管理", [("1", "安装 APK"), ("2", "卸载应用"), ("3", "禁用应用"), ("0", "返回")]
        )
        if choice == "0":
            return
        if choice == "1":
            mode = self.ui.choose(
                "安装来源",
                [
                    ("1", "本地 APK · ADB 直接安装（推荐）"),
                    ("2", "文件服务器 · 设备自行下载"),
                    ("0", "返回"),
                ],
            )
            if mode == "0":
                return
            if mode == "1":
                path = self.ui.text("APK 路径（支持空格；q 返回）", validator=existing_apk)
                return self.install(path, self.pick())
            path = self.ui.text("服务器 APK 文件名或待上传的本地路径")
            upload = self.ui.confirm("先通过 SCP 上传此文件？", default=False)
            if upload:
                path = existing_apk(path)
            server = self.ui.text("HTTP 文件服务器", default=devices.FILE_SERVER)
            return self.install(
                path, self.pick(), via_server=True, upload=upload, file_server=server
            )
        package = self.ui.text("应用包名（q 返回）", validator=devices.validate_package)
        return self.package_action(
            "uninstall" if choice == "2" else "disable", package, self.pick()
        )

    def resolution_menu(self):
        choice = self.ui.choose(
            "分辨率管理",
            [
                ("1", "查看分辨率和 DPI"),
                ("2", "设置分辨率和 DPI"),
                ("3", "恢复系统默认值"),
                ("0", "返回"),
            ],
        )
        if choice == "0":
            return
        size = dpi = None
        if choice == "2":
            size = self.ui.text(
                "分辨率（例如 1280x720；输入 - 保持当前值）",
                default="-",
                validator=lambda value: None if value == "-" else resolution(value),
            )
            dpi = self.ui.text(
                "DPI（输入 - 保持当前值）",
                default="-",
                validator=lambda value: None if value == "-" else positive_int(value),
            )
        return self.change_resolution(self.pick(), size=size, dpi=dpi, reset=choice == "3")

    def shell_menu(self):
        choice = self.ui.choose(
            "批量命令",
            [
                ("1", "自定义 Shell 命令"),
                ("2", "启用 root shell"),
                ("3", "修复反复下载（删除 usercfg.xml）"),
                ("4", "修复反复启停（删除 account_data.json）"),
                ("5", "清空速通应用数据"),
                ("0", "返回"),
            ],
        )
        if choice == "0":
            return
        presets = {
            "2": "setprop persist.sys.root_access 3",
            "3": "rm " + devices.ARKLIGHTS_DIR + "usercfg.xml",
            "4": "rm " + devices.ARKLIGHTS_ROOT + "account_data.json",
            "5": "pm clear " + devices.ARKLIGHTS,
        }
        command = self.ui.text("远程 Shell 命令（q 返回）") if choice == "1" else presets[choice]
        root = self.ui.confirm("使用 root 执行？", default=choice != "1")
        return self.shell(self.pick(), command, root=root)

    def config_menu(self):
        choice = self.ui.choose(
            "配置管理",
            [
                ("1", "推送本地模板到设备"),
                ("2", "拉取设备配置到本地"),
                ("3", "从一台设备同步到其他设备"),
                ("0", "返回"),
            ],
        )
        if choice == "0":
            return
        if choice == "1":
            path = self.ui.text("模板 JSON 路径", default=str(CONFIG_UPLOAD_PATH))
            devices.read_config(Path(path).expanduser())
            return self.upload_config(Path(path).expanduser(), self.pick())
        if choice == "2":
            return self.batch("拉取配置", self.pick(), self.pull_config)
        source = self.pick(single=True)[0]
        self.ui.info(f"模板设备：{source.id} · {source.name}；接下来选择推送目标", style="accent")
        self.selected = [number for number in self.selected if number != source.id]
        return self.sync_config(source.id, self.pick())

    def restart_menu(self):
        choice = self.ui.choose(
            "重启速通",
            [("1", "使用各设备当前配置"), ("2", "使用本地模板覆写配置后启动"), ("0", "返回")],
        )
        if choice == "0":
            return
        config = None
        if choice == "2":
            config = Path(
                self.ui.text("模板 JSON 路径", default=str(CONFIG_UPLOAD_PATH))
            ).expanduser()
            devices.read_config(config)
        return self.restart(self.pick(), upload=choice == "2", config=config)

    def menu(self):
        if not sys.stdin.isatty():
            raise ValueError("交互菜单需要终端；自动化操作请使用 --help 中的子命令")
        self.ui.banner()
        options = [
            ("1", "屏幕投屏 · scrcpy"),
            ("2", "应用管理 · 安装 / 卸载 / 禁用"),
            ("3", "分辨率 · 查看 / 设置 / 恢复"),
            ("4", "重启设备"),
            ("5", "安装 BusyBox"),
            ("6", "批量 Shell 命令"),
            ("7", "重启速通"),
            ("8", "配置管理 · 推送 / 拉取 / 同步"),
            ("9", "连接设备 / 刷新状态"),
            ("s", "设置并发数"),
            ("0", "退出"),
        ]
        while True:
            self.ui.device_table(self.device_map.values(), self.refresh_states(), self.selected)
            self.ui.info(
                f"并发 {self.args.jobs} · 操作超时 {self.args.timeout}s · Ctrl+C 返回 · Ctrl+D 退出",
                style="muted",
            )
            try:
                choice = self.ui.choose("选择操作", options)
            except (KeyboardInterrupt, CancelAction):
                self.ui.info("已退出", style="muted")
                return 0
            try:
                if choice == "0":
                    return 0
                if choice == "1":
                    self.show(self.pick(single=True)[0])
                elif choice == "2":
                    self.apk_menu()
                elif choice == "3":
                    self.resolution_menu()
                elif choice == "4":
                    self.reboot(self.pick())
                elif choice == "5":
                    self.busybox(self.pick())
                elif choice == "6":
                    self.shell_menu()
                elif choice == "7":
                    self.restart_menu()
                elif choice == "8":
                    self.config_menu()
                elif choice == "9":
                    self.batch(
                        "连接设备", self.pick(), lambda device: device.connect(), connect=False
                    )
                elif choice == "s":
                    self.args.jobs = self.ui.text(
                        "并发数（1 为串行）", default=str(self.args.jobs), validator=positive_int
                    )
            except (KeyboardInterrupt, CancelAction):
                self.ui.info("已返回主菜单", style="muted")
            except (ValueError, OSError, devices.DeviceError, argparse.ArgumentTypeError) as exc:
                self.ui.error(exc)


def build_parser():
    def add_common(parser, *, root=False):
        def default(value):
            return value if root else argparse.SUPPRESS

        parser.add_argument(
            "--devices-file",
            type=Path,
            default=default(Path(os.environ.get("ALC_DEVICES_FILE", str(DEVICES_PATH)))),
            help="设备 JSON 路径（也可设置 ALC_DEVICES_FILE）",
        )
        parser.add_argument(
            "--jobs",
            "-j",
            type=positive_int,
            default=default(4),
            help="最大并发数，默认 4；1 为串行",
        )
        parser.add_argument(
            "--timeout",
            type=positive_int,
            default=default(60),
            help="普通 ADB 操作超时秒数，默认 60；安装/传输至少 300 秒",
        )
        parser.add_argument(
            "--plain",
            action="store_true",
            default=default(False),
            help="使用数字菜单，关闭动态进度",
        )
        parser.add_argument(
            "--no-color",
            action="store_true",
            default=default(False),
            help="关闭颜色（支持 NO_COLOR 环境变量）",
        )
        parser.add_argument(
            "--yes",
            "-y",
            action="store_true",
            default=default(False),
            help="确认执行写入、重启等操作，适用于自动化",
        )

    shared = argparse.ArgumentParser(add_help=False)
    add_common(shared)
    parser = argparse.ArgumentParser(
        description="ALC · Android 设备控制台；不带参数进入交互菜单",
        epilog='示例：python3 src/main.py list\n      python3 src/main.py install app.apk -d "1,3,5-8" --yes\n      python3 src/main.py shell -d 1 --yes -- "getprop ro.product.model"',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common(parser, root=True)
    commands = parser.add_subparsers(dest="command", metavar="命令")

    def command(name, help, *, targets=False, default_targets=None):
        sub = commands.add_parser(name, help=help, description=help, parents=[shared])
        if targets:
            sub.add_argument(
                "--targets",
                "-d",
                required=default_targets is None,
                default=default_targets,
                help='设备 ID，支持 "1,3,5-8"，"0" / "all" 表示全部',
            )
        return sub

    command("menu", "进入交互菜单")
    command("list", "列出设备及 ADB 连接状态")
    command("connect", "连接选定设备", targets=True)
    show = command("show", "使用 scrcpy 投屏一台设备")
    show.add_argument("device_id", type=positive_int, help="设备 ID")
    install = command("install", "安装 APK（默认通过 ADB 直接安装）", targets=True)
    install.add_argument("apk", help="本地 APK 路径；服务器模式下也可填写文件名")
    install.add_argument(
        "--via-server", action="store_true", help="让设备从 HTTP 文件服务器下载 APK"
    )
    install.add_argument(
        "--upload", action="store_true", help="先通过 SCP 上传 APK，需配合 --via-server"
    )
    install.add_argument(
        "--file-server", default=devices.FILE_SERVER, help="HTTP 文件服务器（ALC_FILE_SERVER）"
    )
    install.add_argument("--scp-server", default=SCP_SERVER, help="SCP 目标（ALC_SCP_SERVER）")
    for name, help in [("uninstall", "卸载应用"), ("disable", "禁用应用")]:
        sub = command(name, help, targets=True)
        sub.add_argument("package", help="Android 应用包名")
    screen = command("resolution", "查看、设置或恢复分辨率及 DPI", targets=True)
    screen.add_argument("--size", type=resolution, metavar="宽x高", help="例如 1280x720")
    screen.add_argument("--dpi", type=positive_int, help="屏幕 DPI")
    screen.add_argument("--reset", action="store_true", help="恢复系统默认值")
    command("reboot", "重启选定设备", targets=True)
    busybox = command("busybox", "安装 BusyBox（需要 root，使用设备专用挂载路径）", targets=True)
    busybox.add_argument("--binary", type=Path, help="本地 ARMv7 BusyBox 路径")
    shell = command("shell", "执行远程命令；参数放在 -- 之后", targets=True)
    shell.add_argument("--root", action="store_true", help="通过 su -c 执行")
    shell.add_argument("shell_command", nargs=argparse.REMAINDER, help="命令字符串或命令及参数")
    restart = command("restart", "重启速通并等待游戏启动", targets=True)
    restart.add_argument("--upload", action="store_true", help="使用本地模板覆写配置")
    restart.add_argument("--config", type=Path, help="覆写模板路径，需配合 --upload")
    restart.add_argument("--show-ui", action="store_true", help="启动时显示速通界面")
    restart.add_argument("--stop-game", action="store_true", help="同时关闭游戏后重新启动")
    config = command("config", "拉取或推送设备配置")
    actions = config.add_subparsers(dest="config_action", required=True)
    for name, help in [
        ("pull", "拉取设备配置到本地，保留旧文件备份"),
        ("push", "推送本地模板，每台设备使用自己的 token"),
    ]:
        action = actions.add_parser(name, help=help, parents=[shared])
        action.add_argument("--targets", "-d", required=True, help="目标设备 ID，0 表示全部")
        if name == "push":
            action.add_argument(
                "--file", type=Path, default=CONFIG_UPLOAD_PATH, help="本地模板 JSON 路径"
            )
            action.add_argument("--cloud-server", help="覆盖 cloud_server")
    sync = command(
        "sync-config", "从一台设备拉取配置并同步到其他设备", targets=True, default_targets="0"
    )
    sync.add_argument("--template-device", type=positive_int, required=True, help="模板设备 ID")
    sync.add_argument("--include-template", action="store_true", help="也向模板设备推送配置")
    sync.add_argument("--cloud-server", help="覆盖 cloud_server")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        if not sys.stdin.isatty():
            parser.print_help()
            return 0
        args.command = "menu"
    ui = UI(plain=args.plain, no_color=args.no_color)
    try:
        return Application(args, ui).dispatch()
    except EOFError:
        ui.info("已退出", style="muted")
        return 0
    except (KeyboardInterrupt, CancelAction):
        ui.info("操作已中断", style="warning")
        return 130
    except (ValueError, OSError, devices.DeviceError) as exc:
        ui.error(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
