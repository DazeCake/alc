"""End-to-end checks use a fake adb; no live devices are contacted."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import devices
import main

ROOT = Path(__file__).resolve().parents[1]
FAKE_ADB = r"""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_ADB_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
mode = os.environ.get("FAKE_ADB_MODE", "")
if args == ["devices"]:
    print("List of devices attached\n192.0.2.1:5555\tdevice\n192.0.2.2:5555\toffline\n")
elif args[0] == "connect":
    print("failed to connect" if mode == "fail-connect" else "connected to " + args[1])
else:
    serial = args[1]
    command = args[2:]
    if command == ["get-state"]:
        print("device")
    elif command[0] == "pull":
        if mode == "fail-pull":
            print("remote file missing", file=sys.stderr)
            sys.exit(1)
        Path(command[-1]).write_text(json.dumps({"source": serial, "cloud_device_token": "template-token"}))
        print("1 file pulled")
    elif command[0] == "push":
        if mode == "fail-second" and serial == "192.0.2.2:5555":
            print("device offline", file=sys.stderr)
            sys.exit(1)
        print("1 file pushed")
    elif command[0] == "install":
        print("Failure [INSTALL_FAILED_TEST]" if mode == "fail-install" else "Success")
    elif command[0] == "shell":
        print("Physical size: 1920x1080" if command[1] == "wm size" else "ok [literal]")
    else:
        print("Success")
"""


class ParserTests(unittest.TestCase):
    def test_common_options_work_before_and_after_nested_commands(self):
        for argv in [
            ["--jobs", "2", "--yes", "config", "push", "-d", "1"],
            ["config", "--jobs", "2", "push", "-d", "1", "--yes"],
            ["config", "push", "--jobs", "2", "-d", "1", "--yes"],
        ]:
            with self.subTest(argv=argv):
                args = main.build_parser().parse_args(argv)
                self.assertEqual(args.jobs, 2)
                self.assertTrue(args.yes)
                self.assertEqual(args.timeout, 60)

    def test_shell_command_is_preserved_after_separator(self):
        args = main.build_parser().parse_args(
            ["shell", "-d", "1", "--yes", "--", "echo 'a b' && id"]
        )
        self.assertEqual(args.shell_command, ["--", "echo 'a b' && id"])

    def test_invalid_numbers_and_sizes_are_parser_errors(self):
        for argv in [
            ["--jobs", "0", "list"],
            ["resolution", "-d", "1", "--size", "0x100"],
            ["resolution", "-d", "1", "--dpi", "abc"],
        ]:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main.build_parser().parse_args(argv)
                self.assertEqual(error.exception.code, 2)

    def test_template_token_is_replaced_without_mutating_template(self):
        template = {"cloud_device_token": "source", "cloud_captcha_token": "original"}
        device = devices.Device(1, "name", "192.0.2.1", "target")
        prepared = main.prepare_config(template, device, "https://example.com")
        self.assertEqual(prepared["cloud_device_token"], "target")
        self.assertEqual(prepared["cloud_captcha_token"], "original")
        self.assertEqual(template["cloud_device_token"], "source")


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "devices.json"
        self.items = [
            {"id": 1, "name": "alpha", "ip": "192.0.2.1", "token": "token-one"},
            {"id": 2, "name": "beta", "ip": "192.0.2.2", "token": "token-two"},
        ]
        self.config.write_text(json.dumps(self.items))
        self.log = self.root / "adb.log"
        binary = self.root / "adb"
        binary.write_text(f"#!{sys.executable}\n" + FAKE_ADB)
        binary.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.root) + os.pathsep + os.environ.get("PATH", ""),
            "ALC_DEVICES_FILE": str(self.config),
            "ALC_DATA_DIR": str(self.root / "data"),
            "FAKE_ADB_LOG": str(self.log),
            "NO_COLOR": "1",
        }

    def run_cli(self, *args, mode=""):
        return subprocess.run(
            [sys.executable, str(ROOT / "src" / "main.py"), *args],
            capture_output=True,
            text=True,
            input="",
            timeout=15,
            check=False,
            env={**self.env, "FAKE_ADB_MODE": mode},
            cwd=self.root,
        )

    def commands(self):
        return (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists()
            else []
        )

    def test_list_displays_actual_statuses(self):
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("alpha", "beta", "在线", "离线"):
            self.assertIn(text, result.stdout)
        self.assertNotIn("\x1b[", result.stdout)

    def test_no_arguments_without_tty_prints_help(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        self.assertFalse(self.commands())

    def test_noninteractive_write_needs_explicit_confirmation(self):
        result = self.run_cli("reboot", "-d", "0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--yes", result.stderr)
        self.assertFalse(self.commands())

    def test_unknown_device_is_friendly_error(self):
        result = self.run_cli("connect", "-d", "9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("不在配置中", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.commands())

    def test_batch_reconnects_only_offline_devices_and_deduplicates(self):
        result = self.run_cli("reboot", "-d", "1,2,1", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.commands()
        self.assertIn(["connect", "192.0.2.2:5555"], commands)
        self.assertNotIn(["connect", "192.0.2.1:5555"], commands)
        self.assertEqual(sum(command[-1] == "reboot" for command in commands), 2)

    def test_apk_install_with_spaces(self):
        path = self.root / "my app.apk"
        path.write_bytes(b"apk")
        result = self.run_cli("install", str(path), "-d", "1", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            ["-s", "192.0.2.1:5555", "install", "-r", str(path.resolve())], self.commands()
        )

    def test_install_failure_is_not_reported_as_success(self):
        path = self.root / "app.apk"
        path.write_bytes(b"apk")
        result = self.run_cli("install", str(path), "-d", "1", "--yes", mode="fail-install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("INSTALL_FAILED_TEST", result.stdout)
        self.assertIn("失败 1", result.stdout)

    def test_connect_failure_with_zero_exit_is_detected(self):
        result = self.run_cli("connect", "-d", "2", mode="fail-connect")
        self.assertEqual(result.returncode, 1)
        self.assertIn("failed to connect", result.stdout)

    def test_shell_output_is_literal_and_command_is_not_double_quoted(self):
        result = self.run_cli("shell", "-d", "1", "--yes", "--", "echo '[literal]'")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("ok [literal]", result.stdout)
        self.assertIn(["-s", "192.0.2.1:5555", "shell", "echo '[literal]'"], self.commands())

    def test_config_push_uses_each_device_token_and_summarizes_partial_failure(self):
        template = self.root / "template.json"
        template.write_text('{"cloud_device_token": "source", "value": 1}')
        result = self.run_cli(
            "config", "push", "--file", str(template), "-d", "1-2", "--yes", mode="fail-second"
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("成功 1", result.stdout)
        self.assertIn("失败 1", result.stdout)
        for name, token in [("alpha", "token-one"), ("beta", "token-two")]:
            saved = json.loads((self.root / "data" / name / "config_debug.json").read_text())
            self.assertEqual(saved["cloud_device_token"], token)

    def test_failed_pull_does_not_silently_use_stale_file(self):
        path = self.root / "data" / "alpha" / "config_debug.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"old":true}')
        result = self.run_cli("config", "pull", "-d", "1", mode="fail-pull")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(path.read_text()), {"old": True})

    def test_no_sync_targets_is_reported_before_any_adb_operation(self):
        result = self.run_cli("sync-config", "--template-device", "1", "-d", "1", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("没有推送目标", result.stderr)
        self.assertFalse(self.commands())

    def test_invalid_device_configuration_is_reported_without_traceback(self):
        for items in [
            [self.items[0], self.items[0]],
            [{**self.items[0], "name": "../bad"}],
            [{**self.items[0], "id": True}],
            [],
        ]:
            self.config.write_text(json.dumps(items))
            with self.subTest(items=items):
                result = self.run_cli("list")
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)

    def test_duplicate_serial_with_implicit_port_is_rejected(self):
        self.items[1]["ip"] = "192.0.2.1:5555"
        self.config.write_text(json.dumps(self.items))
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ADB 地址重复", result.stderr)
        self.assertFalse(self.commands())

    def test_reset_cannot_be_combined_with_size(self):
        result = self.run_cli("resolution", "-d", "1", "--reset", "--size", "1280x720", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.commands())

    def test_noninteractive_menu_exits_without_waiting_for_input(self):
        result = self.run_cli("menu")
        self.assertEqual(result.returncode, 1)
        self.assertIn("交互菜单需要终端", result.stderr)

    def test_sync_template_is_saved_after_successful_pull(self):
        # Exercise the source -> template -> targets pipeline without touching res/.
        from ui import UI

        args = main.build_parser().parse_args(
            [
                "--devices-file",
                str(self.config),
                "sync-config",
                "--template-device",
                "1",
                "-d",
                "2",
                "--yes",
            ]
        )
        ui = UI(plain=True, no_color=True)
        ui.console.file = io.StringIO()
        output = self.root / "upload.json"
        with patch.dict(os.environ, self.env), patch.object(main, "CONFIG_UPLOAD_PATH", output):
            app = main.Application(args, ui)
            self.assertEqual(app.dispatch(), 0)
        self.assertEqual(json.loads(output.read_text())["source"], "192.0.2.1:5555")
        target = self.root / "data" / "beta" / "config_debug.json"
        self.assertEqual(json.loads(target.read_text())["cloud_device_token"], "token-two")


if __name__ == "__main__":
    unittest.main()
