"""Windows global-hotkey parsing regression tests."""

import unittest

from deyaz_app import (
    GlobalHotkey, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT,
)


class GlobalHotkeyTests(unittest.TestCase):
    class FakeUser32:
        def __init__(self, pressed=()):
            self.pressed = set(pressed)

        def GetAsyncKeyState(self, virtual_key):
            return 0x8000 if virtual_key in self.pressed else 0

    def test_ctrl_alt_r_uses_norepeat_and_expected_virtual_key(self):
        modifiers, key = GlobalHotkey.parse_shortcut("Ctrl+Alt+R")
        self.assertEqual(key, ord("R"))
        self.assertTrue(modifiers & MOD_CONTROL)
        self.assertTrue(modifiers & MOD_ALT)
        self.assertTrue(modifiers & MOD_NOREPEAT)
        self.assertFalse(modifiers & MOD_SHIFT)

    def test_space_and_shift_are_parsed(self):
        modifiers, key = GlobalHotkey.parse_shortcut("Ctrl+Shift+Space")
        self.assertEqual(key, 0x20)
        self.assertTrue(modifiers & MOD_CONTROL)
        self.assertTrue(modifiers & MOD_SHIFT)

    def test_polling_observes_shortcut_without_keyboard_hook(self):
        modifiers, key = GlobalHotkey.parse_shortcut("Ctrl+Alt+R")
        user32 = self.FakeUser32({0x11, 0x12, ord("R")})
        self.assertTrue(GlobalHotkey.shortcut_is_down(user32, modifiers, key))

        user32.pressed.remove(0x12)
        self.assertFalse(GlobalHotkey.shortcut_is_down(user32, modifiers, key))


if __name__ == "__main__":
    unittest.main()
