# device class
import os
import re
import time
import json
import subprocess
from pathlib import Path
from collections import defaultdict

FILE_SERVER = ""
ARKLIGHTS = "com.bilabila.arknightsspeedrun2"
ARKLIGHTS_HASH = "3205c0ded576131ea255ad2bd38b0fb2"
OFFICIAL_GAME = "com.hypergryph.arknights"
BILIBILI_GAME = "com.hypergryph.arknights.bilibili"

findNodeCache = None


def c(data, key, value):
    if data[key] == value:
        return
    # print(key, data[key], "=>", value)
    data[key] = value


class Device:

    def __init__(self, id, name, ip, token):
        self.id = id
        self.name = name
        self.ip = ip
        self.token = token
        self.path = Path("devices") / self.name
        self.path.mkdir(exist_ok=True, parents=True)

    def connect(self):
        os.system("adb connect " + self.ip)

    def push(self, src, dst):
        src = src.replace("\\", "/")
        push_process = subprocess.Popen(
            "adb -s " + self.ip + " push " + src + " " + dst,
            shell=True,
            stdout=subprocess.PIPE,
        )
        _, error = push_process.communicate()
        if push_process.returncode == 0:
            print("推送成功: " + self.name)
        else:
            print("推送失败: " + self.name)
            print(error)

    def load(self, name):
        self.adb(
            "pull",
            "/data/user/0/"
            + ARKLIGHTS
            + "/assistdir/"
            + ARKLIGHTS_HASH
            + "/root/"
            + name,
            self.path / name,
        )
        p = self.path / name
        if not p.exists():
            open(p, "w").write("{}")
        try:
            return defaultdict(str, json.load(open(self.path / name)))
        except:
            return defaultdict(str, {})

    def load_xml(self, name):
        self.adb(
            "pull",
            "/data/user/0/" + ARKLIGHTS + "/assistdir/" + ARKLIGHTS_HASH + "/" + name,
            self.path / name,
        )
        # p = self.path / name
        # if not p.exists():
        #     open(p, "w").write("")
        # return open(self.path / name).read()

    def save(self, name, data):
        json.dump(data, open(self.path / name, "w"), ensure_ascii=False)
        self.adb(
            "push",
            self.path / name,
            "/data/user/0/"
            + ARKLIGHTS
            + "/assistdir/"
            + ARKLIGHTS_HASH
            + "/root/"
            + name,
        )

    def save_xml(self, name):
        self.adb(
            "push",
            self.path / name,
            "/data/user/0/" + ARKLIGHTS + "/assistdir/" + ARKLIGHTS_HASH + "/" + name,
        )

    def install_apk(self, apk_path):
        # self.push(apk_path, "/sdcard/Download/")
        apk_name = os.path.basename(apk_path)
        self.download_file(FILE_SERVER + apk_name, "/sdcard/Download/")
        # print(
        #     "adb -s " + self.ip + " shell pm install -r /sdcard/Download/" + apk_name,
        # )
        process = subprocess.Popen(
            "adb -s " + self.ip + " shell pm install -r /sdcard/Download/" + apk_name,
            shell=True,
            stdout=subprocess.PIPE,
        )
        _, error = process.communicate()
        if process.returncode == 0:
            print("安装成功: " + self.name)
        else:
            print("安装失败: " + self.name)
            print(error)

    def uninstall_apk(self, package_name):
        os.system("adb -s " + self.ip + " uninstall " + package_name)
        # use pm check to check if the apk is uninstalled

    def disable_package(self, package_name):
        os.system("adb -s " + self.ip + " shell pm disable-user " + package_name)

    def show(self):
        # use scrcpy to show the device screen
        os.system("scrcpy -s " + self.ip)

    def sh(self, *args):
        command = ["adb", "-s", self.ip, "shell", *args]
        # print(f"Running command: {' '.join(command)}")
        out = subprocess.run(command, capture_output=True)
        # print(out.stdout.decode())
        return out.stdout.decode()

    def root_sh(self, *args):
        command = ["adb", "-s", self.ip, "shell", "su", "-c", *args]
        # print(f"Running command: {' '.join(command)}")
        out = subprocess.run(command, capture_output=True)
        # print(out.stdout.decode())
        return out.stdout.decode()

    def adb(self, *args):
        out = subprocess.run(["adb", "-s", self.ip, *args], capture_output=True)
        return out.stdout.decode()

    def reboot(self):
        os.system("adb -s " + self.ip + " reboot")

    def vm_set(self, W, H):
        os.system("adb -s " + self.ip + " shell wm size " + W + "x" + H)

    def vm_show(self):
        os.system("adb -s " + self.ip + " shell wm size")

    def density_set(self, density):
        os.system("adb -s " + self.ip + " shell wm density " + density)

    def density_show(self):
        os.system("adb -s " + self.ip + " shell wm density")

    def install_busybox(self):
        self.push("./res/busybox", "/sdcard/Download/")
        self.root_sh(
            "mount -o remount,rw /dev/block/platform/soc/1d84000.ufshc/by-name/system /system"
        )
        self.root_sh("cp /sdcard/Download/busybox /system/xbin/busybox")
        self.root_sh("chmod +x /system/xbin/busybox")
        self.root_sh("busybox --install /system/xbin")
        print("Busybox安装完成: " + self.name)

    def download_file(self, url, dst):
        # download file from url to dst
        print("开始下载: " + self.name)
        self.sh("wget -P " + dst + " " + url)
        print("下载完成: " + self.name)

    def foreground(self):
        x = self.sh("dumpsys", "activity", "recents")
        x = re.search("Recent #0.*(com[^\s]+)", x)
        if x:
            return x.group(1)

    def findNode(self, text="", id="", cache=False):
        import xml.etree.ElementTree as ET

        if cache:
            x = self.findNodeCache
        else:
            x = self.adb("exec-out", "uiautomator", "dump", "/dev/tty")
            x = re.search("(<.+>)", x)
        self.findNodeCache = x

        if not x:
            return
        x = x.group(1)
        tree = ET.XML(x)
        btn = None
        # ans = []
        for elem in tree.iter():
            elem = elem.attrib
            # print(elem)
            if (
                text
                and elem.get("text", None) == text
                or id
                and elem.get("resource-id", None) == id
            ):
                btn = elem.get("bounds", None)
                btn = re.search("(\d+)[^\d]+(\d+)[^\d]+(\d+)[^\d]+(\d+)", btn).groups()
                x = (int(btn[0]) + int(btn[2])) // 2
                y = (int(btn[1]) + int(btn[3])) // 2
                # print(text, x, y)
                return x, y
                # ans.append([x, y])

    def stop(self, app=ARKLIGHTS):
        self.adb("shell", "input", "keyevent", "KEYCODE_HOME")
        self.adb("shell", "am", "force-stop", app)

    def run_al(self, package=ARKLIGHTS):
        self.sh("input keyevent KEYCODE_HOME")
        self.sh("monkey -p " + package + " -c android.intent.category.LAUNCHER 1")
        see_package = False
        for i in range(50):
            time.sleep(1)
            # print("foreground", foreground())
            # print("package",package)
            # print("see_package",see_package)
            if self.foreground() == package:
                self.findNode()
                ok = self.findNode("确定", cache=True)
                cancel = self.findNode("取消", cache=True)
                if cancel:
                    x, y = cancel
                    self.sh("input", "tap", str(x), str(y))
                elif ok:
                    x, y = ok
                    self.sh("input", "tap", str(x), str(y))
                    see_package = True
            # snap = findNode(
            #     id="com.bilabila.arknightsspeedrun2:id/switch_snap", cache=True
            # )
            # if snap:
            #     x, y = snap
            #     adb( "input", "tap", str(x), str(y))
            if (
                self.foreground() == OFFICIAL_GAME
                or self.foreground() == BILIBILI_GAME
                and see_package
            ):
                break
            # elif see_package:
            #     break

    def restart_al(
        self, upload=False, account="", hide=True, rg=False, crontab=False, game=False
    ):
        x = None
        if not upload:
            x = self.load("config_debug.json")
        else:
            # 从本地读取覆写的配置 utf-8
            x = json.load(
                # open(self.path / "config_debug_saved.json", "r", encoding="utf-8")
                open("res\config_debug_upload.json", "r", encoding="utf-8")
            )
            x["cloud_device_token"] = self.token
            # 替换usercfg.xml中的字符串
            # xml = open(self.path / "usercfg.xml", "r", encoding="utf-8").read()
            # xml.replace(
            #     '<node key="hideUIOnce" val="false" type="4"/>',
            #     '<node key="hideUIOnce" val="true" type="4"/>',
            # )
            # xml.replace(
            #     '<node key="restart_mode_hook" val="" type="4"/>',
            #     '<node key="restart_mode_hook" val=";-- save_run_state_begin;;max_stone_times=0;max_drug_times=0;;-- save_run_state_end;" type="4"/>',
            # )

        # 固定无菜单启动hook
        c(
            x,
            "after_require_hook",
            (
                "saveConfig('restart_mode_hook','');saveConfig('hideUIOnce','true');"
                # "saveConfig('hideUIOnce','true');"
                if hide
                else ""
            ),
        )
        c(
            x,
            "before_account_hook",
            "clear_hook();",
            # + ("extra_mode=[[战略前瞻投资]];" if rg else "")
            # + ("crontab_enable_only=1;" if crontab else ""),
        )
        # self.save_xml("usercfg.xml")
        self.save("config_debug.json", x)

        if game:
            self.stop(OFFICIAL_GAME)
            self.stop(BILIBILI_GAME)
        self.stop()
        self.run_al()
        print("重启速通完成: " + self.name)
