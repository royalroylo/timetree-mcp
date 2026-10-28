import unittest

import timetree_mcp.operations as operations
from timetree_mcp.operations import calendar_id_value, to_epoch_millis


class ReadHelperTests(unittest.TestCase):
    def test_calendar_id_value_accepts_strings_and_integers(self):
        self.assertEqual(calendar_id_value(" 57571629 "), "57571629")
        self.assertEqual(calendar_id_value(57571629), "57571629")
        for bad in (True, None, "", 1.5):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                calendar_id_value(bad)

    def test_to_epoch_millis_parses_iso_and_passes_through_numbers(self):
        self.assertEqual(to_epoch_millis("2026-08-25T01:00:00Z"), 1787619600000)
        self.assertEqual(to_epoch_millis(1787619600000), 1787619600000)
        with self.assertRaises(ValueError):
            to_epoch_millis("not-a-time")

    def test_write_payload_builders_are_removed(self):
        for name in (
            "require_confirmation", "build_create_event_payload", "build_update_event_payload",
            "build_memo_payload", "build_memo_update_payload",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(operations, name))


if __name__ == "__main__":
    unittest.main()
