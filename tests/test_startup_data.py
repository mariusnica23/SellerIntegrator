from unittest import TestCase
from unittest.mock import patch

from integrator.config import data_root
from support import workspace_temp


class StartupDataTests(TestCase):
    def test_portable_data_is_independent_of_launch_working_directory_and_appdata(self):
        with workspace_temp() as folder:
            exe = folder / "Application" / "TrendyolFGO.exe"
            portable = exe.parent / "Date"
            portable.mkdir(parents=True)
            with patch("integrator.config.sys.frozen", True, create=True), patch("integrator.config.sys.executable", str(exe)):
                for local in (folder/"UserAppData", folder/"HelperAppData"):
                    with patch.dict("os.environ", {"LOCALAPPDATA":str(local)}):
                        self.assertEqual(data_root(), portable.resolve())
            with patch("integrator.config.sys.frozen", False, create=True), patch.dict("os.environ", {"LOCALAPPDATA":str(folder/"Normal")}):
                self.assertEqual(data_root(),folder/"Normal"/"TrendyolFGO")

    def test_standard_install_without_portable_folder_uses_appdata(self):
        with workspace_temp() as folder:
            with patch("integrator.config.sys.frozen", True, create=True), patch("integrator.config.sys.executable", str(folder/"App.exe")), patch.dict("os.environ", {"LOCALAPPDATA":str(folder/"Local")}):
                self.assertEqual(data_root(),folder/"Local"/"TrendyolFGO")
