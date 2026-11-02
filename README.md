# ALC

通过 ADB 管理多台 Android 设备，为 ArkLights（速通）提供应用、分辨率、配置和重启操作。

终端中运行可进入交互控制台：方向键选择菜单，空格勾选设备，彩色表格显示连接状态，批量操作显示进度及每台设备的结果。也可使用子命令完成自动化。

## 开始使用

需要 Python 3.10+、已加入 `PATH` 的 `adb`。屏幕投屏另外需要 `scrcpy`。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r src/requirements.txt

# 已有 res/devices.json 可以继续使用；首次使用时复制示例后填写设备信息
cp -n res/devices.example.json res/devices.json
python src/main.py
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活环境。虚拟环境不能跨系统复用，迁移项目后请重新创建。

设备配置格式：

```json
[
  {"id": 1, "name": "device-01", "ip": "192.168.1.101", "token": "device-token-01"},
  {"id": 2, "name": "device-02", "ip": "192.168.1.102:5555", "token": "device-token-02"}
]
```

`id` 为唯一正整数，`name` 为唯一设备名称，同时用于本地配置目录。`ip` 可以包含端口，省略时使用 `5555`。设备需要启用网络 ADB 并完成授权；设备列表显示的是已有 ADB 连接的状态，执行操作时会尝试连接尚未在线的设备。`token` 在推送模板和覆写重启时必填，普通设备管理可省略。

实际设备配置、下载的二进制和设备数据均被 Git 忽略，仓库只保留脱敏示例。

## 交互控制台

```bash
python src/main.py
python src/main.py menu
python src/main.py --plain  # 数字菜单，适合基础终端
```

- 方向键移动、Enter 确认；设备复选框使用空格勾选、`a` 全选。
- 可以用 ID 输入选择设备：`1`、`1 3 5`、`1,3,5-8`；`0` 或 `all` 表示全部。
- 记住上一次的设备选择；投屏和配置来源要求单选。
- 子菜单有返回选项；文本输入 `q` 返回，`Ctrl+C` 中断当前流程，`Ctrl+D` 退出。
- 安装、卸载、重启、配置覆盖等操作会先显示目标设备并确认。
- 默认最多并发操作 4 台设备，可在主菜单调整；并发设为 `1` 即串行执行。

## 命令行

用 `python src/main.py --help` 查看命令，子命令也支持 `--help`。公共参数可放在子命令前后。

```bash
# 设备状态、连接和投屏
python src/main.py list
python src/main.py connect -d "1-5"
python src/main.py show 1

# 默认直接安装本地 APK，路径支持空格
python src/main.py install "/path/to/my app.apk" -d "1,3,5-8" --yes
python src/main.py uninstall com.example.app -d 1 --yes
python src/main.py disable com.example.app -d 1 --yes

# 不带设置参数时，只查看当前分辨率和 DPI
python src/main.py resolution -d 0
python src/main.py resolution -d "1-5" --size 1280x720 --dpi 240 --yes
python src/main.py resolution -d 1 --reset --yes

python src/main.py reboot -d "1,2" --yes
python src/main.py shell -d 1 --yes -- "getprop ro.product.model"
python src/main.py shell -d "1-3" --root --yes -- "id"

# 重启速通：默认使用各设备配置，等待进入游戏后才报告完成
python src/main.py restart -d "1-5" --yes
python src/main.py restart -d "1-5" --upload --config res/config_debug_upload.json --yes

# 拉取、推送和从设备同步配置
python src/main.py config pull -d "1,3"
python src/main.py config push -d "1-5" --file res/config_debug_upload.json --yes
python src/main.py sync-config --template-device 5 --targets "1-20" --yes
```

`sync-config` 默认向全部设备同步，排除模板设备；用 `--include-template` 可将模板设备也包含在推送范围内。`config push` 和 `sync-config` 可用 `--cloud-server` 覆盖云端地址。推送模板时自动替换每台设备自己的 `cloud_device_token`，其他字段保留模板值。

配置拉取会先验证 JSON，再替换本地文件；拉取失败会报告错误并保留旧文件。拉取、推送和保存同步模板前会备份已有本地文件，依次使用 `.bak`、`.bak.1` 等后缀。批量任务会继续处理其他设备，并汇总失败结果。

有交互终端时可逐次确认；脚本执行写入操作需要显式指定 `--yes`。没有终端且不带子命令时显示帮助，避免等待输入。

| 公共参数 | 用途 |
| --- | --- |
| `--devices-file PATH` | 指定设备配置，默认 `res/devices.json` |
| `--jobs N` / `-j N` | 最大并发数，默认 `4` |
| `--timeout N` | 普通 ADB 操作超时，默认 `60` 秒；安装和大文件传输至少使用 `300` 秒 |
| `--plain` | 数字菜单，关闭动态进度 |
| `--no-color` | 关闭颜色，也支持 `NO_COLOR` 环境变量 |
| `--yes` / `-y` | 确认执行写入操作 |

退出码：`0` 成功或用户取消确认，`1` 操作失败（包含部分设备失败），`2` 参数格式错误，`130` 用户中断。

## 可选：文件服务器安装

默认安装无需文件服务器。原有的“先 SCP 上传、设备再通过 HTTP 下载”的方式仍然可用：

```bash
# 使用已在服务器上的 APK
python src/main.py install app.apk -d "1-5" --via-server \
  --file-server http://192.168.1.10:5000/app/ --yes

# 上传本地 APK 后安装
python src/main.py install "/path/to/my app.apk" -d "1-5" --via-server --upload \
  --file-server http://192.168.1.10:5000/app/ \
  --scp-server user@server:/path/to/app --yes
```

SCP 目标目录必须对应 HTTP 服务的文件目录，设备需要有可用的 `wget`。`--upload` 要求本机安装 `scp`。

环境变量 `ALC_FILE_SERVER`、`ALC_SCP_SERVER` 可设置文件服务器默认值；`ALC_DEVICES_FILE` 可设置设备配置路径；`ALC_DATA_DIR` 可设置本地设备数据根目录（默认项目下的 `devices/`）。所有资源默认相对于项目目录查找，可以从其他工作目录启动脚本。

## BusyBox

```bash
python src/busybox_installer.py  # 下载到 res/busybox；不会自动安装
python src/main.py busybox -d 1 --yes
```

下载器保留项目原有的 BusyBox 1.21.1 ARMv7 地址，支持 `--url` / `--output`，会检查 HTTP 错误及 ELF 文件头。安装流程需要 root，沿用原有设备专用 `/system` 分区挂载路径；其他架构或分区布局需先适配安装逻辑。

## 开发验证

安装依赖后运行：

```bash
python -m unittest discover -s tests -v
```

测试使用模拟 ADB，覆盖设备选择、参数解析、批量失败、配置保护、命令引用和启动超时，不会连接真实设备。
