"""Mit Windows starten: Run-Eintrag in einem Test-Schlüssel (nie der echte Run-Schlüssel)."""

import sys
import unittest
import winreg
from unittest import mock

import autostart

TEST_KEY = r"Software\MD_Importer_Test\Run"


def delete_test_key() -> None:
    for path in (TEST_KEY, r"Software\MD_Importer_Test"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


class AutostartTest(unittest.TestCase):
    def setUp(self):
        delete_test_key()
        patcher = mock.patch.object(autostart, "RUN_KEY", TEST_KEY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(delete_test_key)

    def _value(self):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            return winreg.QueryValueEx(key, autostart.VALUE_NAME)[0]

    def test_default_off(self):
        self.assertFalse(autostart.is_enabled())

    def test_on_writes_command_and_off_removes_it(self):
        self.assertIsNone(autostart.set_enabled(True))
        self.assertEqual(self._value(), autostart.command())
        self.assertTrue(autostart.is_enabled())
        self.assertIsNone(autostart.set_enabled(False))
        self.assertFalse(autostart.is_enabled())
        with self.assertRaises(OSError):
            self._value()
        self.assertIsNone(autostart.set_enabled(False))  # schon aus → kein Fehler

    def test_command_source_and_exe(self):
        source = autostart.command()
        self.assertRegex(source, r'^"[^"]*pythonw\.exe" "[^"]*MD_Importer\.pyw"$')
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\Apps\MD_Importer.exe"):
            self.assertEqual(autostart.command(), '"C:\\Apps\\MD_Importer.exe"')

    def test_entry_of_other_installation_is_not_checked_and_other_values_stay(self):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_SZ, r'"D:\Alt\MD_Importer.exe"')
            winreg.SetValueEx(key, "Fremd", 0, winreg.REG_SZ, "x")
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(False)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            self.assertEqual(winreg.QueryValueEx(key, "Fremd")[0], "x")

    def test_registry_error_returns_message_and_logs(self):
        with mock.patch.object(autostart.winreg, "CreateKey", side_effect=PermissionError("gesperrt")), \
                mock.patch.object(autostart, "dlog") as log:
            error = autostart.set_enabled(True)
        self.assertIn("gesperrt", error)
        log.assert_called_once()
        self.assertFalse(autostart.is_enabled())


if __name__ == "__main__":
    unittest.main()
