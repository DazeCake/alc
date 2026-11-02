import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selection import parse_device_ids


class SelectionTests(unittest.TestCase):
    def test_all_aliases(self):
        for spec in ("0", "all", " ALL ", "全部"):
            with self.subTest(spec=spec):
                self.assertEqual(parse_device_ids(spec, [3, 1, 2]), [1, 2, 3])

    def test_combines_ranges_commas_spaces_and_removes_duplicates(self):
        self.assertEqual(parse_device_ids("3,1-3 5，5", [1, 2, 3, 5]), [3, 1, 2, 5])

    def test_rejects_invalid_inputs(self):
        for spec in ("", "-1", "1-", "2-1", "abc", "1,0", "1,,2", "1-2-3", "9", "1-3"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_device_ids(spec, [1, 3])

    def test_missing_ids_in_range_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_device_ids("1-5", [1, 3, 5])

    def test_huge_range_is_validated_without_expanding(self):
        with self.assertRaises(ValueError):
            parse_device_ids("1-99999999999999999", [1])

    def test_empty_device_list_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_device_ids("all", [])


if __name__ == "__main__":
    unittest.main()
