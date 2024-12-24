# 自动化安装busybox
import requests

URL = "https://www.busybox.net/downloads/binaries/1.21.1/busybox-armv7l"


def download_busybox():
    print("下载busybox")
    # 下载busybox到/res/busybox下
    r = requests.get(URL)
    with open("./res/busybox", "wb") as f:
        f.write(r.content)
    print("下载完成")


# download_busybox()
