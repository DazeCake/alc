import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import devices


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.device = devices.Device(1, "test", "192.0.2.1", "token", data_dir=self.root)

    def test_ip_port_is_normalized_in_all_adb_commands(self):
        with patch("devices.run_command") as run:
            self.device.adb("get-state")
        self.assertEqual(run.call_args.args[0], ["adb", "-s", "192.0.2.1:5555", "get-state"])
        self.device.ip = "192.0.2.1:1234"
        self.assertEqual(self.device.serial, "192.0.2.1:1234")

    def test_constructor_does_not_create_device_directories(self):
        self.assertFalse(self.device.path.exists())

    def test_apk_path_is_a_single_local_argument(self):
        path = self.root / "my app $(touch injected).apk"
        path.write_bytes(b"apk")
        with patch("devices.run_command") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "Success", "")
            self.device.install_apk(path)
        self.assertEqual(run.call_args.args[0][-1], path)
        self.assertNotIn("shell", run.call_args.args[0])

    def test_remote_arguments_and_root_command_are_quoted(self):
        with patch.object(self.device, "adb", return_value="") as adb:
            self.device.sh("echo", "hello world", "$(bad)")
            self.assertEqual(adb.call_args.args, ("shell", "echo 'hello world' '$(bad)'"))
            self.device.root_sh("echo 'hello' && id")
            self.assertEqual(
                adb.call_args.args, ("shell", "su -c 'echo '\"'\"'hello'\"'\"' && id'")
            )

    def test_failed_pull_preserves_local_config_and_raises(self):
        path = self.device.path / "config_debug.json"
        devices.save_json(path, {"old": True})
        with patch.object(self.device, "adb", side_effect=devices.DeviceError("pull failed")):
            with self.assertRaises(devices.DeviceError):
                self.device.load("config_debug.json")
        self.assertEqual(devices.load_json(path), {"old": True})
        self.assertEqual(list(self.device.path.iterdir()), [path])

    def test_invalid_download_preserves_local_config(self):
        path = self.device.path / "config_debug.json"
        devices.save_json(path, {"old": True})

        def pull(*args):
            Path(args[-1]).write_text("not json")

        with patch.object(self.device, "adb", side_effect=pull), self.assertRaises(ValueError):
            self.device.load("config_debug.json")
        self.assertEqual(devices.load_json(path), {"old": True})

    def test_successful_pull_validates_and_backs_up_local_file(self):
        path = self.device.path / "config_debug.json"
        devices.save_json(path, {"old": True})

        def pull(*args):
            devices.save_json(args[-1], {"new": True})

        with patch.object(self.device, "adb", side_effect=pull):
            self.assertEqual(self.device.load("config_debug.json"), {"new": True})
        self.assertEqual(devices.load_json(path.with_name(path.name + ".bak")), {"old": True})

    def test_push_backs_up_existing_file(self):
        path = self.device.path / "config_debug.json"
        devices.save_json(path, {"old": True})
        with patch.object(self.device, "adb", return_value="pushed"):
            self.device.save("config_debug.json", {"new": True})
        self.assertEqual(devices.load_json(path), {"new": True})
        self.assertEqual(devices.load_json(path.with_name(path.name + ".bak")), {"old": True})

    def test_connect_rejects_failure_with_success_exit_code(self):
        result = subprocess.CompletedProcess([], 0, "failed to connect", "")
        with (
            patch("devices.run_command", return_value=result),
            self.assertRaises(devices.DeviceError),
        ):
            self.device.connect()

    def test_connect_checks_actual_transport_state(self):
        result = subprocess.CompletedProcess([], 0, "connected to 192.0.2.1:5555", "")
        with (
            patch("devices.run_command", return_value=result),
            patch.object(self.device, "adb", return_value="offline"),
        ):
            with self.assertRaises(devices.DeviceError):
                self.device.connect()

    def test_failures_timeouts_and_missing_binaries_are_readable(self):
        outcomes = [
            subprocess.CompletedProcess([], 1, "", "device offline"),
            subprocess.CompletedProcess([], 0, "Failure [INSTALL_FAILED]", ""),
            subprocess.TimeoutExpired(["adb"], 1),
            FileNotFoundError("adb"),
        ]
        for outcome in outcomes:
            kwargs = (
                {"side_effect": outcome}
                if isinstance(outcome, Exception)
                else {"return_value": outcome}
            )
            with self.subTest(outcome=outcome), patch("devices.subprocess.run", **kwargs):
                with self.assertRaises(devices.DeviceError):
                    devices.run_command(["adb"], timeout=1)

    def test_multiline_ui_dump_and_cached_nodes(self):
        xml = '<hierarchy>\n<node text="确定" bounds="[10,20][30,40]"/>\n</hierarchy>'
        with patch.object(self.device, "adb", return_value=xml) as adb:
            self.device.find_node()
            self.assertEqual(self.device.find_node("确定", cache=True), (20, 30))
            self.assertIsNone(self.device.find_node("取消", cache=True))
        self.assertEqual(adb.call_count, 1)

    def test_foreground_uses_resumed_activity_over_previous_tasks(self):
        output = (
            "mResumedActivity: ActivityRecord{abc u0 com.hypergryph.arknights/.MainActivity t1}"
        )
        with patch.object(self.device, "sh", return_value=output):
            self.assertEqual(self.device.foreground(), devices.OFFICIAL_GAME)

    def test_foreground_supports_legacy_task_affinity(self):
        for recent in (
            "Recent #0: TaskRecord{abc #1 A=com.hypergryph.arknights U=0}",
            "Recent #0: Task{abc #1 A=10084:com.hypergryph.arknights U=0}",
            "Recent #0: com.hypergryph.arknights/.MainActivity",
        ):
            with (
                self.subTest(recent=recent),
                patch.object(self.device, "sh", side_effect=["", recent]),
            ):
                self.assertEqual(self.device.foreground(), devices.OFFICIAL_GAME)

    def test_startup_timeout_is_reported_as_failure(self):
        with (
            patch.object(self.device, "sh", return_value=""),
            self.assertRaises(devices.DeviceError),
        ):
            self.device.run_al(startup_timeout=0)

    def test_cancelled_batch_does_not_start_another_command(self):
        cancelled = threading.Event()
        cancelled.set()
        with devices.cancellable_commands(cancelled), patch("devices.subprocess.Popen") as run:
            with self.assertRaises(devices.DeviceError):
                devices.run_command([sys.executable, "-c", "pass"])
            run.assert_not_called()

    def test_cancelled_batch_stops_running_local_process(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set)
        started = time.monotonic()
        timer.start()
        try:
            with devices.cancellable_commands(cancelled), self.assertRaises(devices.DeviceError):
                devices.run_command([sys.executable, "-c", "import time; time.sleep(30)"])
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 3)

    def test_bom_and_legacy_json_encodings(self):
        for encoding in ("utf-8-sig", "gb18030"):
            with self.subTest(encoding=encoding):
                path = self.root / "config.json"
                path.write_bytes(json.dumps({"名称": "测试"}, ensure_ascii=False).encode(encoding))
                self.assertEqual(devices.load_json(path), {"名称": "测试"})

    def test_missing_token_prevents_template_restart(self):
        self.device.token = ""
        with self.assertRaises(ValueError):
            self.device.restart_al(upload=True, template={"value": 1})


if __name__ == "__main__":
    unittest.main()
