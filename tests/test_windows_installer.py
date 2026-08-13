import inspect
from pathlib import Path
import unittest

from deyaz_app import DeYazWindow


ROOT = Path(__file__).resolve().parents[1]


class WindowsInstallerTests(unittest.TestCase):
    def test_installer_registers_standard_uninstall_metadata(self):
        source = (ROOT / "installer" / "DeYaz.iss").read_text(encoding="utf-8")
        self.assertIn("AppId={{C3DDB819-3838-4A02-9DD3-BD16C82C0279}", source)
        self.assertIn("UninstallDisplayIcon={app}\\{#MyAppExeName}", source)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\{#MyAppName}", source)

    def test_release_binary_is_not_upx_packed(self):
        source = (ROOT / "DeYaz.spec").read_text(encoding="utf-8")
        self.assertIn("upx=False", source)

    def test_installer_and_binary_versions_stay_aligned(self):
        spec = (ROOT / "DeYaz.spec").read_text(encoding="utf-8")
        installer = (ROOT / "installer" / "DeYaz.iss").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "build-desktop.yml").read_text(encoding="utf-8")
        self.assertIn('version = "1.0.2"', spec)
        self.assertIn('#define MyAppVersion "1.0.2"', installer)
        self.assertIn("DeYaz-Setup-1.0.2-x64.exe", workflow)

    def test_surface_switch_uses_short_opacity_transition(self):
        source = inspect.getsource(DeYazWindow._animate_page_entry)
        self.assertIn("setDuration(180)", source)
        self.assertIn('b"opacity"', source)


if __name__ == "__main__":
    unittest.main()
