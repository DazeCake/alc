"""Checked ADB operations. Device methods return data and never print UI output."""

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FILE_SERVER = os.environ.get("ALC_FILE_SERVER", "http://192.168.31.125:5000/app/")
ARKLIGHTS = "com.bilabila.arknightsspeedrun2"
ARKLIGHTS_HASH = "3205c0ded576131ea255ad2bd38b0fb2"
ARKLIGHTS_DIR = f"/data/user/0/{ARKLIGHTS}/assistdir/{ARKLIGHTS_HASH}/"
ARKLIGHTS_ROOT = ARKLIGHTS_DIR + "root/"
OFFICIAL_GAME = "com.hypergryph.arknights"
BILIBILI_GAME = "com.hypergryph.arknights.bilibili"


class DeviceError(RuntimeError):
    """An external command failed or did not produce valid device data."""


_command_context = threading.local()


@contextmanager
def cancellable_commands(event):
    """Bind batch cancellation to commands in the current worker thread."""
    previous = getattr(_command_context, "cancelled", None)
    _command_context.cancelled = event
    try:
        yield
    finally:
        _command_context.cancelled = previous


def _run_cancellable(argv, *, timeout, capture, cancelled):
    if cancelled.is_set():
        raise DeviceError("操作已中断")
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                if cancelled.is_set():
                    raise DeviceError("操作已中断")
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            process.kill()
            process.communicate()
            raise


def run_command(args, *, timeout=60, capture=True):
    """Run argv directly; paths and user input never enter a local shell."""
    argv = [str(arg) for arg in args]
    try:
        cancelled = getattr(_command_context, "cancelled", None)
        if cancelled is not None:
            result = _run_cancellable(argv, timeout=timeout, capture=capture, cancelled=cancelled)
        else:
            result = subprocess.run(
                argv,
                capture_output=capture,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
    except FileNotFoundError as exc:
        raise DeviceError(f"找不到 {argv[0]}，请先安装并加入 PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeviceError(f"{argv[0]} 执行超过 {timeout} 秒，请检查设备连接") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise DeviceError(detail or f"{argv[0]} 退出码：{result.returncode}")
    output = (result.stdout or "").strip()
    # Some Android package manager commands report failure with exit code zero.
    if re.search(r"(?:^|\n)(?:Failure\b|Error:)", output):
        raise DeviceError(output)
    return result


def load_json(path):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return json.loads(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别 JSON 文件编码：{path}")


def save_json(path, data):
    """Replace a JSON file atomically to avoid half-written configurations."""
    path = Path(path)
    path.parent.mkdir(exist_ok=True, parents=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def backup_file(path):
    path = Path(path)
    if not path.exists():
        return None
    backup = path.with_name(path.name + ".bak")
    index = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak.{index}")
        index += 1
    shutil.copy2(path, backup)
    return backup


def read_config(path):
    data = load_json(path)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"配置必须是非空 JSON 对象：{path}")
    return data


def validate_package(package):
    if not re.fullmatch(r"[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+", package, re.ASCII):
        raise ValueError("包名格式不正确，示例：com.example.app")
    return package


class Device:
    def __init__(self, id, name, ip, token, *, data_dir=None, timeout=60):
        self.id = id
        self.name = name
        self.ip = ip
        self.token = token
        self.path = (
            Path(data_dir or os.environ.get("ALC_DATA_DIR", PROJECT_ROOT / "devices")).expanduser()
            / name
        )
        self.timeout = timeout
        self._node_cache = None

    @property
    def serial(self):
        return self.ip if ":" in self.ip else self.ip + ":5555"

    def connect(self):
        result = run_command(["adb", "connect", self.serial], timeout=self.timeout)
        output = (result.stdout or "").strip()
        if re.search(r"unable|failed|cannot|refused|error:", output, re.IGNORECASE):
            raise DeviceError(output)
        # `adb connect` can return success before a usable transport is available.
        state = self.adb("get-state").strip()
        if state != "device":
            raise DeviceError(f"设备尚未就绪：{state or '未知状态'}")
        return output or "已连接"

    def adb_result(self, *args, timeout=None):
        return run_command(
            ["adb", "-s", self.serial, *args],
            timeout=self.timeout if timeout is None else timeout,
        )

    def adb(self, *args, timeout=None):
        return self.adb_result(*args, timeout=timeout).stdout.strip()

    def sh(self, *args, timeout=None):
        # A single string is an intentional remote shell program. Multiple
        # arguments are quoted because adb shell joins its arguments remotely.
        command = str(args[0]) if len(args) == 1 else shlex.join(map(str, args))
        return self.adb("shell", command, timeout=timeout)

    def root_sh(self, *args):
        command = str(args[0]) if len(args) == 1 else shlex.join(map(str, args))
        return self.sh("su -c " + shlex.quote(command))

    def push(self, src, dst):
        path = Path(src).expanduser()
        if not path.is_file():
            raise ValueError(f"文件不存在：{path}")
        return self.adb("push", path, dst, timeout=max(300, self.timeout))

    def _pull(self, name, remote_dir):
        if Path(name).name != name:
            raise ValueError("配置文件名不能包含目录")
        self.path.mkdir(exist_ok=True, parents=True)
        # Pull to a temporary file: a failed pull must never read stale data.
        with tempfile.TemporaryDirectory(dir=self.path) as temporary:
            fetched = Path(temporary) / name
            self.adb("pull", remote_dir + name, fetched)
            if not fetched.is_file():
                raise DeviceError(f"设备未返回文件：{name}")
            if name.endswith(".json"):
                read_config(fetched)
            destination = self.path / name
            backup_file(destination)
            fetched.replace(destination)
        return destination

    def load(self, name):
        return read_config(self._pull(name, ARKLIGHTS_ROOT))

    def load_xml(self, name):
        return self._pull(name, ARKLIGHTS_DIR).read_text(encoding="utf-8")

    def save(self, name, data):
        if Path(name).name != name:
            raise ValueError("配置文件名不能包含目录")
        if not isinstance(data, dict) or not data:
            raise ValueError("拒绝推送空配置")
        destination = self.path / name
        backup_file(destination)
        save_json(destination, data)
        return self.adb("push", destination, ARKLIGHTS_ROOT + name)

    def save_xml(self, name):
        if Path(name).name != name:
            raise ValueError("配置文件名不能包含目录")
        return self.push(self.path / name, ARKLIGHTS_DIR + name)

    def install_apk(self, apk_path, *, via_server=False, file_server=None):
        path = Path(apk_path).expanduser()
        if not via_server:
            if not path.is_file() or path.suffix.lower() != ".apk":
                raise ValueError(f"请选择存在的 APK 文件：{path}")
            return self.adb("install", "-r", path, timeout=max(300, self.timeout))
        server = file_server or FILE_SERVER
        if not server.startswith(("http://", "https://")):
            raise ValueError("文件服务器地址必须以 http:// 或 https:// 开头")
        if path.suffix.lower() != ".apk":
            raise ValueError("文件名必须以 .apk 结尾")
        remote = "/sdcard/Download/" + path.name
        self.download_file(server.rstrip("/") + "/" + quote(path.name), remote)
        return self.sh("pm", "install", "-r", remote, timeout=max(300, self.timeout))

    def uninstall_apk(self, package_name):
        return self.adb("uninstall", validate_package(package_name), timeout=120)

    def disable_package(self, package_name):
        return self.sh("pm", "disable-user", "--user", "0", validate_package(package_name))

    def show(self):
        run_command(["scrcpy", "-s", self.serial], timeout=None, capture=False)
        return "投屏已结束"

    def reboot(self):
        return self.adb("reboot") or "已发送重启指令"

    def vm_set(self, width, height):
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise ValueError("宽度和高度必须大于 0")
        return self.sh("wm", "size", f"{width}x{height}")

    def vm_show(self):
        return self.sh("wm", "size")

    def density_set(self, density):
        density = int(density)
        if density <= 0:
            raise ValueError("DPI 必须大于 0")
        return self.sh("wm", "density", density)

    def density_show(self):
        return self.sh("wm", "density")

    def resolution_reset(self):
        self.sh("wm", "size", "reset")
        self.sh("wm", "density", "reset")
        return "已恢复系统默认分辨率和 DPI"

    def install_busybox(self, binary=None):
        path = Path(binary or PROJECT_ROOT / "res" / "busybox")
        self.push(path, "/sdcard/Download/busybox")
        self.root_sh(
            "mount -o remount,rw /dev/block/platform/soc/1d84000.ufshc/by-name/system /system"
        )
        self.root_sh("cp /sdcard/Download/busybox /system/xbin/busybox")
        self.root_sh("chmod 755 /system/xbin/busybox")
        self.root_sh("/system/xbin/busybox --install /system/xbin")
        return self.root_sh("/system/xbin/busybox | head -n 1")

    def download_file(self, url, destination):
        return self.sh("wget", "-O", destination, url, timeout=max(300, self.timeout))

    def foreground(self):
        output = self.sh("dumpsys", "activity", "activities")
        match = re.search(
            r"(?:mResumedActivity|topResumedActivity|mFocusedActivity)[^\n]*?"
            r"\b([A-Za-z][\w]*(?:\.[\w]+)+)/[\w.$]+",
            output,
        )
        if match:
            return match[1]
        # Older Android versions expose only the task affinity in recents.
        output = self.sh("dumpsys", "activity", "recents")
        match = re.search(
            r"Recent #0[^\n]*?\b(?:A=)?(?:\d+:)?([A-Za-z][\w]*(?:\.[\w]+)+)",
            output,
        )
        return match[1] if match else None

    def find_node(self, text="", resource_id="", cache=False):
        if not cache or self._node_cache is None:
            output = self.adb("exec-out", "uiautomator", "dump", "/dev/tty")
            match = re.search(r"<hierarchy\b.*?</hierarchy>", output, re.DOTALL)
            if not match:
                self._node_cache = None
                return None
            try:
                self._node_cache = ET.fromstring(match[0])
            except ET.ParseError as exc:
                raise DeviceError("无法解析设备 UI，请确认屏幕已解锁") from exc
        for node in self._node_cache.iter():
            if (text and node.get("text") == text) or (
                resource_id and node.get("resource-id") == resource_id
            ):
                bounds = re.fullmatch(
                    r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", node.get("bounds", "")
                )
                if bounds:
                    left, top, right, bottom = map(int, bounds.groups())
                    return (left + right) // 2, (top + bottom) // 2
        return None

    def findNode(self, text="", id="", cache=False):
        """Compatibility for existing scripts importing the old method name."""
        return self.find_node(text=text, resource_id=id, cache=cache)

    def stop(self, app=ARKLIGHTS):
        self.sh("input", "keyevent", "KEYCODE_HOME")
        return self.sh("am", "force-stop", validate_package(app))

    def run_al(self, package=ARKLIGHTS, *, startup_timeout=50):
        self.sh("input", "keyevent", "KEYCODE_HOME")
        self.sh(
            "monkey", "-p", validate_package(package), "-c", "android.intent.category.LAUNCHER", "1"
        )
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            time.sleep(1)
            foreground = self.foreground()
            if foreground in {OFFICIAL_GAME, BILIBILI_GAME}:
                return "已进入游戏"
            if foreground == package:
                self.find_node()
                position = self.find_node("取消", cache=True) or self.find_node("确定", cache=True)
                if position:
                    self.sh("input", "tap", *position)
        raise DeviceError(f"{startup_timeout} 秒内未检测到游戏启动，请检查设备屏幕")

    def restart_al(self, upload=False, *, hide=True, game=False, template=None):
        if upload:
            if not self.token:
                raise ValueError(f"设备 {self.id} 未配置 token，不能覆写配置")
            config = (
                dict(template)
                if template is not None
                else read_config(PROJECT_ROOT / "res" / "config_debug_upload.json")
            )
            config["cloud_device_token"] = self.token
        else:
            config = self.load("config_debug.json")
        config["after_require_hook"] = (
            "saveConfig('restart_mode_hook','');saveConfig('hideUIOnce','true');" if hide else ""
        )
        config["before_account_hook"] = "clear_hook();"
        self.save("config_debug.json", config)
        if game:
            self.stop(OFFICIAL_GAME)
            self.stop(BILIBILI_GAME)
        self.stop()
        return self.run_al()
