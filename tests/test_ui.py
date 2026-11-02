import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main
from ui import UI, CancelAction


class UITests(unittest.TestCase):
    def test_back_from_single_device_selection_cancels(self):
        ui = UI(plain=True, no_color=True)

        def select(*args, **kwargs):
            return SimpleNamespace(unsafe_ask=lambda: kwargs["choices"][-1].value)

        with patch("ui.questionary.select", side_effect=select), self.assertRaises(CancelAction):
            ui.pick_devices([], single=True)

    def test_numeric_input_validation_retries_without_leaving_menu(self):
        ui = UI(plain=True, no_color=True)
        ui.console.file = io.StringIO()
        ui.error_console.file = io.StringIO()
        with patch("ui.Prompt.ask", side_effect=["bad", "0", "2"]):
            self.assertEqual(ui.text("并发数", validator=main.positive_int), 2)
        self.assertEqual(ui.error_console.file.getvalue().count("错误"), 2)


if __name__ == "__main__":
    unittest.main()
