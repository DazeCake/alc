"""Download the legacy ARMv7 BusyBox binary used by these devices."""

import argparse
import tempfile
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.busybox.net/downloads/binaries/1.21.1/busybox-armv7l"


def download_busybox(destination=None, *, url=URL):
    destination = Path(destination or PROJECT_ROOT / "res" / "busybox").expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with requests.get(url, stream=True, timeout=(10, 120)) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
                temporary = Path(file.name)
                for chunk in response.iter_content(chunk_size=65536):
                    file.write(chunk)
        with temporary.open("rb") as file:
            header = file.read(4)
        if header != b"\x7fELF":
            raise ValueError("下载内容不是有效的 ELF 可执行文件")
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="下载 ARMv7 BusyBox（不安装到设备）")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "res" / "busybox")
    parser.add_argument("--url", default=URL)
    args = parser.parse_args()
    try:
        path = download_busybox(args.output, url=args.url)
    except (requests.RequestException, OSError, ValueError) as exc:
        parser.exit(1, f"下载失败：{exc}\n")
    print(f"已下载：{path}")


if __name__ == "__main__":
    main()
