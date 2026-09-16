from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from integrator.demo import demo_orders
from integrator.config import load_settings
from integrator.ui import App
from support import workspace_temp


class UiTests(unittest.TestCase):
    def test_save_button_persists_settings_after_reopening(self):
        with workspace_temp() as folder:
            app = App(Path(folder))
            values = {"mode": "production", "cui": "10000000", "seller_id": "1234", "fgo_key": "fgo-example-key", "api_key": "example-key", "api_secret": "example-secret", "series_ro": "RO", "series_bg": "BG", "series_gr": "GR", "platform_url": "https://example.test", "eur_ron": "5.09", "previous_sales_ron": "100", "opening_sales_ron": "200"}
            values["eur_direct_fgo"] = True
            values.update(emag_user_ro="api-example@example.test",emag_password_ro="example-emag-password",emag_series_ro="EM",huf_ron="0.012")
            try:
                app.update()
                app.notebook.select(app.params_tab)
                app.geometry("1180x800")
                app.update()
                self.assertTrue(app.save_button.winfo_viewable())
                self.assertLess(app.save_button.winfo_rooty() + app.save_button.winfo_height(), app.winfo_rooty() + app.winfo_height())
                app.param_notebook.select(app.settings_tab)
                app.connection_notebook.select(app.fiscal_tab)
                app.update()
                self.assertTrue(app.eur_direct_check.winfo_viewable())
                self.assertLess(app.eur_direct_check.winfo_rooty() + app.eur_direct_check.winfo_height(), app.winfo_rooty() + app.winfo_height())
                for key, value in values.items():
                    app.fields[key].set(value)
                self.assertIn("nesalvate", app.config_message.get())
                app.save_button.invoke()
                self.assertIn("Parametrizări salvate", app.config_message.get())
                self.assertEqual(load_settings(app.settings_path), app.settings)
                protected = app.settings_path.read_text(encoding="utf-8")
                for key in ("api_key", "api_secret", "fgo_key", "emag_password_ro"):
                    self.assertNotIn(values[key], protected)
            finally:
                app.destroy()
            reopened = App(Path(folder))
            try:
                reopened.withdraw()
                for key, value in values.items():
                    self.assertEqual(reopened.fields[key].get(), value)
                self.assertFalse(reopened.load_error)
            finally:
                reopened.destroy()

    def test_pending_selection_whole_package_and_removes_issued(self):
        with workspace_temp() as folder:
            app = App(Path(folder))
            try:
                app.withdraw()
                raw = demo_orders()[0]
                raw["lines"].append({**raw["lines"][0], "lineId": 880000, "barcode": "5940000000022", "quantity": 1})
                raw["packageTotalPrice"] = "363"
                raw["packageGrossAmount"] = "363"
                app.store.put_orders([raw])
                app.refresh()
                app.notebook.select(app.pending_tab)
                app.pending_tree.selection_set("990001:0")
                app.pending_selection_changed()
                self.assertEqual(set(app.pending_tree.selection()), {"990001:0", "990001:1"})
                self.assertEqual(app.selected(), ["990001"])
                service = app.service()
                service.issue(service.draft(raw))
                app.refresh()
                self.assertNotIn("990001", app.pending_packages.values())
                self.assertIn("990002", app.pending_packages.values())
                self.assertEqual(app.notebook.tab(app.params_tab, "text"), "Parametrizare")
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
