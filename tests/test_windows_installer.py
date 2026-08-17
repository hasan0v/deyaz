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
        version_info = (ROOT / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn('version = "1.0.13"', spec)
        self.assertIn('#define MyAppVersion "1.0.13"', installer)
        self.assertIn("DeYaz-Setup-1.0.13-x64.exe", workflow)
        self.assertIn("StringStruct(u'CompanyName', u'Ali Hasanov')", version_info)

    def test_installer_uses_deyaz_brand_assets_and_creator_metadata(self):
        installer = (ROOT / "installer" / "DeYaz.iss").read_text(encoding="utf-8")
        self.assertIn("WizardImageFile=..\\assets\\installer-wizard.bmp", installer)
        self.assertIn("WizardSmallImageFile=..\\assets\\installer-small.bmp", installer)
        self.assertIn("AppPublisher={#MyAppPublisher}", installer)
        self.assertIn("Created by Ali Hasanov", installer)

    def test_installed_runtime_uses_one_folder_not_one_file(self):
        spec = (ROOT / "DeYaz.spec").read_text(encoding="utf-8")
        installer = (ROOT / "installer" / "DeYaz.iss").read_text(encoding="utf-8")
        self.assertIn("exclude_binaries=True", spec)
        self.assertIn("distribution = COLLECT(", spec)
        self.assertIn('Source: "..\\dist\\DeYaz\\*"', installer)

    def test_update_closes_background_instance_before_replacing_files(self):
        app_source = (ROOT / "deyaz_app.py").read_text(encoding="utf-8")
        installer = (ROOT / "installer" / "DeYaz.iss").read_text(encoding="utf-8")
        self.assertIn('"--shutdown-for-update"', app_source)
        self.assertIn("function PrepareToInstall", installer)
        self.assertIn('taskkill.exe', installer)
        self.assertIn('/IM "{#MyAppExeName}"', installer)
        self.assertNotIn("Exec(AppExe", installer)

    def test_surface_switch_uses_short_opacity_transition(self):
        source = inspect.getsource(DeYazWindow._animate_page_entry)
        self.assertIn("setDuration(240)", source)
        self.assertIn('b"opacity"', source)

    def test_surface_tabs_have_a_lightweight_activation_animation(self):
        source = inspect.getsource(DeYazWindow._animate_surface_tab)
        self.assertIn("setDuration(320)", source)
        self.assertIn('b"blurRadius"', source)


if __name__ == "__main__":
    unittest.main()
