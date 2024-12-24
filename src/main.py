import os
import json
import devices
import threading

DEVICES_PATH = "./res/devices.json"
FILE_SERVER = "http://192.168.31.125:5000/app/"
SCP_SERVER = "dsm:/volume1/main/app"

device_map = {}
selected_devices = []
devices.FILE_SERVER = FILE_SERVER


def device_select(device_ids):
    selected_devices.clear()
    # 特殊情况，直接返回
    if device_ids == "-1" or device_ids == "0":
        return device_ids
    # 根据空格切分成数组
    device_ids = device_ids.split(" ")
    for device_id in device_ids:
        # 如果存在1-10这种范围，将其展开
        if "-" in device_id:
            start, end = device_id.split("-")
            for i in range(int(start), int(end) + 1):
                selected_devices.append(i)
        else:
            selected_devices.append(int(device_id))
    print("已选择设备: ", selected_devices)
    if len(selected_devices) == 1:
        return selected_devices[0]
    else:
        return selected_devices


def init():
    with open(DEVICES_PATH) as f:
        data = json.load(f)
        for device in data:
            device_map[device["id"]] = devices.Device(
                device["id"], device["name"], device["ip"], device["token"]
            )


def connect_all():
    for device in device_map.values():
        device.connect()


def scp_push(file_path):
    file_path = file_path.replace("\\", "/")
    os.system("scp " + file_path + " " + SCP_SERVER)


def install_apk_all(apk_path):

    use_multithreading = input("是否使用多线程安装(y/N):") or "N"

    if use_multithreading == "Y" or use_multithreading == "y":
        # 多线程安装
        print("正在使用多线程安装,这将大幅度占用路由器带宽")
        threads = []
        for device in device_map.values():
            print("安装到设备: ", device.name)
            threads.append(
                threading.Thread(target=device.install_apk, args=(apk_path,))
            )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    else:
        for device in device_map.values():
            print("安装到设备: ", device.name)
            device.install_apk(apk_path)


def uninstall_apk_all(package_name):
    for device in device_map.values():
        device.uninstall_apk(package_name)


def restart_al_all(upload=False):
    print(selected_devices)
    use_multithreading = input("是否使用多线程重启(Y/n):") or "Y"
    if use_multithreading == "Y" or use_multithreading == "y":
        # 多线程重启
        print("正在使用多线程重启")
        threads = []
        for device_id in selected_devices:
            device = device_map[device_id]
            print("重启设备: ", device.name)
            threads.append(threading.Thread(target=device.restart_al, args=(upload,)))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    else:
        for device_id in selected_devices:
            device = device_map[device_id]
            print("重启设备: ", device.name)
            device.restart_al(upload)


def main_menu():
    print("==================")
    print("1: Scrcpy连接")
    print("2: APK管理")
    print("3: 分辨率管理")
    print("4: 重启设备")
    print("5: 安装busybox")
    print("6: 批量命令执行")
    print("7: 重启速通")
    print("8: 配置管理")
    print("0: 退出")
    print("==================")
    option = input("请输入选项: ")
    if option == "1":
        print("======Scrcpy连接======")
        id = input("请输入设备ID: ")
        device_map[int(id)].show()
    elif option == "2":
        print("======APK管理======")
        id = input("请输入设备ID, 0为全量操作: ")
        print("1: 安装APK")
        print("2: 卸载APK")
        print("3: 禁用应用")
        option = input("请输入选项: ")
        if option == "1":
            apk_path = input("请输入APK路径: ")
            jump = input("是否跳过上传(y/N):") or "N"
            if jump == "N" or jump == "n":
                scp_push(apk_path)
            if id == "0":
                install_apk_all(apk_path)
            else:
                device_map[int(id)].install_apk(apk_path)
        elif option == "2":
            package_name = input("请输入包名: ")
            if id == "0":
                uninstall_apk_all(package_name)
            else:
                device_map[int(id)].uninstall_apk(package_name)
        elif option == "3":
            package_name = input("请输入包名: ")
            if id == "0":
                for device in device_map.values():
                    device.disable_package(package_name)
            else:
                device_map[int(id)].disable_package(package_name)
    elif option == "3":
        print("======分辨率管理======")
        id = input("请输入设备ID, 0为全量操作: ")
        wm_option = input("1: 设置分辨率\n2: 查看分辨率\n请输入选项: ")
        if wm_option == "1":
            W = input("请输入宽度: ")
            H = input("请输入高度: ")
            dpi = input("请输入DPI: ")
            if id == "0":
                for device in device_map.values():
                    device.vm_set(W, H)
                    device.density_set(dpi)
            else:
                device_map[int(id)].vm_set(W, H)
                device_map[int(id)].density_set(dpi)
        elif wm_option == "2":
            if id == "0":
                for device in device_map.values():
                    device.vm_show()
                    device.density_show()
            else:
                device_map[int(id)].vm_show()
                device_map[int(id)].density_show()
    elif option == "4":
        print("======重启设备======")
        id = input("请输入设备ID, 0为全量操作: ")
        if id == "0":
            for device in device_map.values():
                device.reboot()
        else:
            device_map[int(id)].reboot()
    elif option == "5":
        print("======安装busybox======")
        id = input("请输入设备ID, 0为全量操作: ")
        if id == "0":
            for device in device_map.values():
                device.install_busybox()
        else:
            device_map[int(id)].install_busybox()
    elif option == "6":
        print("======批量命令执行======")
        print("tips: 启用root shell --> setprop persist.sys.root_access 3")
        print(
            "tips: 解决反复下载 --> rm /data/user/0/com.bilabila.arknightsspeedrun2/assistdir/3205c0ded576131ea255ad2bd38b0fb2/usercfg.xml"
        )
        cmd = input("请输入命令: ")
        cmd = '"' + cmd + '"'
        id = device_select(input("请输入设备ID, 0为全量操作: "))
        if id == "0":
            for device in device_map.values():
                device.root_sh(cmd)
        else:
            for i in selected_devices:
                device_map[int(i)].root_sh(cmd)
    elif option == "7":
        print("======重启速通======")
        id = device_select(input("请输入设备ID, 0为全量操作, -1进入覆写模式: "))
        if id == "-1":
            id = device_select(input("[警告]正在覆写配置, 请输入设备ID, 0为全量操作: "))
            restart_al_all(upload=True)
        else:
            restart_al_all()
    elif option == "8":
        print("======配置管理======")
        print("1: 上传配置")
        print("2: 下载配置")
        cfg_option = input("请输入选项: ")
        if cfg_option == "1":
            x = json.load(
                # open(self.path / "config_debug_saved.json", "r", encoding="utf-8")
                open("res\config_debug_upload.json", "r", encoding="utf-8")
            )
            id = device_select(input("请输入设备ID, 0为全量操作: "))
            if id == "0":
                for device in device_map.values():
                    x["cloud_device_token"] = device.token
                    device.save("config_debug.json", x)
            else:
                x["cloud_device_token"] = device_map[int(id)].token
                device_map[int(id)].save("config_debug.json", x)
        elif cfg_option == "2":
            id = device_select(input("请输入设备ID, 0为全量操作: "))
            if id == "0":
                for device in device_map.values():
                    device.load("config_debug.json")
            else:
                device_map[int(id)].load("config_debug.json")
    elif option == "0":
        exit(0)


def run():
    init()
    # 是否连接所有设备:Y/n
    load_all = input("是否连接所有设备(y/N):") or "N"
    if load_all == "Y" or load_all == "y":
        connect_all()
        print("已连接所有设备")
    while True:
        main_menu()


if __name__ == "__main__":
    run()
    # print(device_select("1"))
