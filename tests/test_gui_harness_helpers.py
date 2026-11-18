#!/usr/bin/env python3
"""Unit tests for the pure helpers of tools/gui_test_vm.py (no VM needed)."""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import gui_test_vm

SERIAL_WITH_WINDOWS = (
    b"[boot] x\n[wm] Created window id=1 'Welcome to LobsterOS'\n"
    b"app files\n[wm] Created window id=2 'File Manager'\napp: launched files\n")


class TestParseWindowTitles(unittest.TestCase):
    def test_returns_titles_in_creation_order(self):
        self.assertEqual(gui_test_vm.parse_window_titles(SERIAL_WITH_WINDOWS),
                         ["Welcome to LobsterOS", "File Manager"])

    def test_empty_serial_has_no_windows(self):
        self.assertEqual(gui_test_vm.parse_window_titles(b""), [])

    def test_ignores_unrelated_lines_and_bad_utf8(self):
        serial = b"[wm] Step 1\n[wm] Created window id=7 'Caf\xe9'\n"
        self.assertEqual(gui_test_vm.parse_window_titles(serial), ["Caf�"])


class TestAppWindowTitles(unittest.TestCase):
    def test_welcome_is_not_an_application(self):
        titles = ["Welcome to LobsterOS", "Calculator"]
        self.assertEqual(gui_test_vm.app_window_titles(titles), ["Calculator"])

    def test_only_welcome_leaves_nothing(self):
        self.assertEqual(gui_test_vm.app_window_titles(["Welcome to LobsterOS"]), [])


class TestCountTextLines(unittest.TestCase):
    def make_image(self, bright_rows):
        from PIL import Image
        img = Image.new("RGB", (20, 40), (0, 0, 0))
        for y in bright_rows:
            for x in range(20):
                img.putpixel((x, y), (255, 255, 255))
        return img

    def test_counts_separate_bands(self):
        img = self.make_image([2, 3, 4, 5, 6, 12, 13, 14, 15, 16])
        self.assertEqual(gui_test_vm.count_text_lines(img, (0, 20, 0, 40)), 2)

    def test_fragment_below_a_line_is_not_a_line(self):
        img = self.make_image([2, 3, 4, 5, 6, 7, 8, 11])
        self.assertEqual(gui_test_vm.count_text_lines(img, (0, 20, 0, 40)), 1)

    def test_lines_one_pixel_apart_are_two_lines(self):
        img = self.make_image([2, 3, 4, 5, 6, 8, 9, 10, 11, 12])
        self.assertEqual(gui_test_vm.count_text_lines(img, (0, 20, 0, 40)), 2)

    def test_blank_area_has_no_lines(self):
        img = self.make_image([])
        self.assertEqual(gui_test_vm.count_text_lines(img, (0, 20, 0, 40)), 0)

    def test_box_limits_the_scan(self):
        img = self.make_image([2, 3, 4, 5, 6, 30, 31, 32, 33, 34])
        self.assertEqual(gui_test_vm.count_text_lines(img, (0, 20, 20, 40)), 1)


if __name__ == "__main__":
    unittest.main()
