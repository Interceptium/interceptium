"""GUI tests: drive the real window with synthetic mouse events.

Enabled with INTERCEPTIUM_GUI_TESTS=1 (needs a display; CI uses Xvfb).
Any exception raised inside a Tk callback fails the test.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

ENABLED = os.environ.get("INTERCEPTIUM_GUI_TESTS") == "1"

SAMPLE = {"replacement_rules": [
    {"name": "Sky", "enabled": True, "replace_ids": ["6444884337", "6444884785"], "mode": "id", "with_id": 1012890},
    {"name": "No grass", "replace_ids": ["Image"], "mode": "remove"},
    {"type": "group", "name": "Sounds", "children": [
        {"name": "Shot", "replace_ids": ["130113322"], "mode": "local", "local_path": "/Sounds/shot.ogg"},
        {"name": "Reload", "enabled": False, "replace_ids": ["130113370"], "mode": "id", "with_id": 9120386436},
    ]},
    {"name": "Old CDN", "replace_ids": ["987654321"], "mode": "cdn", "cdn_url": "https://example.com/x.png"},
]}


@unittest.skipUnless(ENABLED, "set INTERCEPTIUM_GUI_TESTS=1 to run GUI tests")
class GuiBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        os.environ["INTERCEPTIUM_HOME"] = str(self.home / "data")
        from interceptium import gui
        from interceptium.ui import anim

        self.gui, self.anim = gui, anim
        self.root = gui.create_root()
        self.errors: list[str] = []
        self.root.report_callback_exception = lambda *exc: self.errors.append(f"{exc[0].__name__}: {exc[1]}")
        self.win = gui.MainWindow(self.root)
        sample = self.home / "Pack.json"
        sample.write_text(json.dumps(SAMPLE))
        self.name = self.win.store.import_file(sample)
        self.win.store.set_enabled(self.name, True)
        self.win.selected = ""
        self.win.select_config(self.name)
        self.root.deiconify()
        self.pump(0.3)

    def tearDown(self) -> None:
        try:
            self.win.on_close()
        except Exception:
            pass
        os.environ.pop("INTERCEPTIUM_HOME", None)
        self.tmp.cleanup()
        self.assertEqual(self.errors, [], "exceptions inside Tk callbacks")

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.001)  # short, so frame timers aren't delayed by the test itself

    def click(self, widget, x: int, y: int) -> None:
        widget.event_generate("<Motion>", x=x, y=y)
        self.root.update()
        widget.event_generate("<ButtonPress-1>", x=x, y=y)
        widget.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.pump(0.3)

    def cards(self):
        return [c for c in self.win.rule_list.inner.winfo_children() if isinstance(c, self.gui.RuleCard)]

    def saved(self) -> dict:
        return json.loads(self.win.store.path_for(self.name).read_text(encoding="utf-8"))


class GuiTests(GuiBase):
    def test_rule_toggle_by_click_saves(self) -> None:
        card = self.cards()[0]
        t = card.toggle
        self.click(card, t.x + t.w // 2, t.y + t.h // 2)
        self.assertFalse(self.saved()["replacement_rules"][0]["enabled"])
        self.assertIsNone(self.win.store.active_rules().lookup(6444884337))

    def test_config_toggle_by_click(self) -> None:
        rows = [r for r in self.win.config_list.inner.winfo_children() if r.name == self.name]
        row = rows[0]
        t = row.toggle
        self.click(row, t.x + t.w // 2, t.y + t.h // 2)
        self.assertFalse(self.win.store.is_enabled(self.name))
        self.assertEqual(self.win.selected, self.name)

    def test_select_config_by_click(self) -> None:
        self.win.store.create("Other")
        self.win.refresh_configs()
        self.pump(0.1)
        row = [r for r in self.win.config_list.inner.winfo_children() if r.name == "Other"][0]
        self.click(row, 20, row.h // 2)
        self.assertEqual(self.win.selected, "Other")
        self.assertEqual(self.win.config_title.cget("text"), "Other")

    def test_delete_rule_in_group_by_click(self) -> None:
        shot = [c for c in self.cards() if c.rule.get("name") == "Shot"][0]
        b = shot.del_btn
        self.click(shot, b.x + b.d // 2, b.y + b.d // 2)
        names = [r["name"] for r in self.saved()["replacement_rules"][2]["children"]]
        self.assertEqual(names, ["Reload"])

    def test_group_collapse(self) -> None:
        before = len(self.cards())
        group = [g for g in self.win.rule_list.inner.winfo_children() if isinstance(g, self.gui.GroupRow)][0]
        self.click(group, 20, 10)
        self.assertEqual(len(self.cards()), before - 2)

    def test_search_filters(self) -> None:
        self.win.search.var.set("reload")
        self.pump(0.4)
        self.assertEqual([c.rule["name"] for c in self.cards()], ["Reload"])
        self.win.search.var.set("zzzz")
        self.pump(0.4)
        self.assertEqual(self.cards(), [])

    def test_proxy_start_stop_and_animation_rate(self) -> None:
        self.win.toggle_proxy()
        deadline = time.time() + 5
        while not self.win.ctl.running and time.time() < deadline:
            self.pump(0.05)
        self.assertTrue(self.win.ctl.running)
        self.pump(0.3)
        a = self.anim.get()
        f0, t0 = a.frames, time.perf_counter()
        self.pump(2.0)
        fps = (a.frames - f0) / (time.perf_counter() - t0)
        self.assertGreater(fps, 50, f"animation ran at {fps:.1f} fps")
        self.assertEqual(self.win.status_text(), "Running")
        self.win.toggle_proxy()
        deadline = time.time() + 5
        while self.win.ctl.running and time.time() < deadline:
            self.pump(0.05)
        self.pump(0.6)
        self.assertFalse(self.win.ctl.running)
        self.assertFalse(a.active((self.win.power, "loop")), "animation loop should stop when idle")

    def wait_slide(self) -> None:
        from interceptium.ui import overlay
        self.pump(overlay.OPEN_TIME + 0.3)
        self.assertFalse(any(self.anim.get().active((p, "show")) for p in self.win.pages))

    def test_popups_animate_open_and_close(self) -> None:
        from interceptium.ui import overlay
        self.win.add_rule()
        page = self.win.pages[-1]
        self.assertIsInstance(page, self.gui.RulePage)
        a = self.anim.get()
        f0, t0 = a.frames, time.perf_counter()
        self.pump(0.05)
        self.assertGreater(page.opacity, 0.0)
        self.assertLess(page.opacity, 1.0)  # mid fade-in
        # rate while the open animation runs (the ticker rightly stops once it is finished)
        while a.active((page, "show")) and time.perf_counter() - t0 < 2:
            self.root.update()
            time.sleep(0.001)
        fps = (a.frames - f0) / (time.perf_counter() - t0)
        self.assertGreater(fps, 50, f"open animation ran at {fps:.1f} fps")
        self.wait_slide()
        self.assertAlmostEqual(page.opacity, 1.0)
        self.assertAlmostEqual(page.offset, 0.0)
        self.assertAlmostEqual(self.win.overlay.backdrop.level, overlay.DIM, places=3)
        cx = page.winfo_rootx() + page.winfo_width() / 2
        rx = self.root.winfo_rootx() + self.root.winfo_width() / 2
        self.assertLess(abs(cx - rx), 3)
        # nested pop-up, then Escape twice
        self.win.push_page(self.gui.ConfirmPage(self.win, "Sure?", "Nested", lambda: None))
        self.wait_slide()
        self.assertEqual(len(self.win.pages), 2)
        self.win.pages[-1].event_generate("<Escape>")
        self.pump(overlay.CLOSE_TIME + 0.2)
        self.assertEqual(len(self.win.pages), 1)
        self.assertAlmostEqual(self.win.overlay.backdrop.level, overlay.DIM, places=3)
        self.win.pages[-1].event_generate("<Escape>")
        self.pump(overlay.CLOSE_TIME + 0.3)
        self.assertEqual(self.win.pages, [])
        self.assertAlmostEqual(self.win.overlay.backdrop.level, 0.0, places=3)
        self.assertEqual(self.win.overlay.backdrop.state(), "withdrawn")

    def test_backdrop_click_closes(self) -> None:
        from interceptium.ui import overlay
        self.win.new_config()
        self.wait_slide()
        bd = self.win.overlay.backdrop
        bd.event_generate("<ButtonRelease-1>", x=5, y=5)
        self.pump(overlay.CLOSE_TIME + 0.3)
        self.assertEqual(self.win.pages, [])

    def test_popup_grows_smoothly_when_asset_added(self) -> None:
        from interceptium.ui import overlay
        self.win.add_rule()
        self.wait_slide()
        page = self.win.pages[-1]
        h0 = page.h
        page.assets.add()
        self.pump(0.06)
        mid = page.h
        self.pump(overlay.RESIZE_TIME + 0.2)
        self.assertGreater(page.h, h0)
        self.assertTrue(h0 < mid < page.h or mid == page.h, (h0, mid, page.h))
        self.assertEqual(int(page.h), page.winfo_height())

    def test_rule_page_assets_add_remove_and_save(self) -> None:
        before = len(self.saved()["replacement_rules"])
        self.win.add_rule()
        self.wait_slide()
        page = self.win.pages[-1]
        assets = page.assets
        self.assertEqual(len(assets.rows), 1)
        self.assertFalse(assets.rows[0][3].winfo_manager())  # no remove button with one row
        assets.rows[0][2].var.set("123456")
        assets.add()
        assets.add()
        self.pump(0.1)
        self.assertEqual([r[1].cget("text") for r in assets.rows], ["Asset 1", "Asset 2", "Asset 3"])
        assets.rows[1][2].var.set("Image")
        assets.rows[2][2].var.set("12")  # invalid
        page.name.var.set("Multi")
        page.primary()
        self.assertTrue(page.error.cget("text").startswith("Asset 3:"))
        remove = assets.rows[2][3]
        self.click(remove, remove.c.d // 2, remove.c.d // 2)
        self.assertEqual([r[1].cget("text") for r in assets.rows], ["Asset 1", "Asset 2"])
        page.value.var.set("999999")
        page.primary()
        self.pump(0.4)
        rules = self.saved()["replacement_rules"]
        self.assertEqual(len(rules), before + 1)
        self.assertEqual(rules[-1]["replace_ids"], ["123456", "Image"])
        self.assertEqual(rules[-1]["with_id"], 999999)
        self.assertEqual(self.win.pages, [])

    def test_edit_rule_page_shows_one_row_per_asset(self) -> None:
        self.win.edit_rule(self.win.data["replacement_rules"][0])
        self.wait_slide()
        page = self.win.pages[-1]
        self.assertEqual([r[2].get() for r in page.assets.rows], ["6444884337", "6444884785"])

    def test_rule_page_local_file_portable(self) -> None:
        pack = self.win.store.folder / "Pack"
        pack.mkdir()
        (pack / "sky.png").write_bytes(b"png")
        self.win.add_rule()
        self.wait_slide()
        page = self.win.pages[-1]
        page.name.var.set("Local sky")
        page.assets.rows[0][2].var.set("123456")
        page.mode.set("local")
        page._mode_changed("local")
        page.value.var.set(str(pack / "sky.png"))
        page.primary()
        self.pump(0.4)
        self.assertEqual(self.saved()["replacement_rules"][-1]["local_path"], "/Pack/sky.png")

    def test_resize_and_rapid_hover(self) -> None:
        for size in ("1000x700", "1400x900", "960x640", "1140x800"):
            self.root.geometry(size)
            self.pump(0.1)
        for card in self.cards():
            card.event_generate("<Enter>")
            card.event_generate("<Leave>")
        self.win.refresh_rules()  # destroys cards mid-animation
        self.pump(0.4)

    def test_delete_last_config_recreates_default(self) -> None:
        for name in list(self.win.store.names()):
            self.win.store.delete(name)
        self.win.store.create(self.name)
        self.win.selected = ""
        self.win.select_config(self.name)
        self.win.delete_config()
        self.wait_slide()
        self.win.pages[-1].primary()
        self.pump(0.4)
        self.assertEqual(self.win.store.names(), ["Default"])
        self.assertEqual(self.win.selected, "Default")

    def test_new_config_page(self) -> None:
        self.win.new_config()
        self.wait_slide()
        page = self.win.pages[-1]
        page.field.var.set("Skies")
        page.event_generate("<Return>")
        self.pump(0.4)
        self.assertIn("Skies", self.win.store.names())
        self.assertEqual(self.win.selected, "Skies")


class ConfigSmoothness(GuiBase):
    """Config sidebar updates in place with animations instead of rebuilding."""

    def test_select_keeps_rows_and_crossfades_highlight(self) -> None:
        self.win.store.create("Other")
        self.win.refresh_configs()
        self.pump(0.4)
        rows_before = dict(self.win.config_rows)
        old, new = rows_before[self.name], rows_before["Other"]
        self.win.select_config("Other")
        self.assertIs(self.win.config_rows["Other"], new, "rows must be reused, not rebuilt")
        self.pump(0.08)
        self.assertTrue(0.0 < new.sel < 1.0 and 0.0 < old.sel < 1.0, (old.sel, new.sel))  # mid cross-fade
        self.pump(0.4)
        self.assertEqual((old.sel, new.sel), (0.0, 1.0))
        self.assertTrue(all(r.winfo_exists() for r in rows_before.values()))

    def test_switch_animates_header_and_rule_list(self) -> None:
        self.win.store.create("Other")
        self.win.refresh_configs()
        self.pump(0.3)
        self.win.select_config("Other")
        self.pump(0.05)
        _x, y = self.win.rule_list.canvas.coords(self.win.rule_list._win)
        self.assertGreater(y, 0)  # still rising
        self.assertNotEqual(self.win.config_title.cget("fg"), "#eceff5")  # still fading in
        self.pump(0.5)
        _x, y = self.win.rule_list.canvas.coords(self.win.rule_list._win)
        self.assertEqual(y, 0)
        self.assertEqual(self.win.config_title.cget("fg"), "#eceff5")

    def test_new_row_grows_and_deleted_row_shrinks(self) -> None:
        self.win.store.create("Grow")
        self.win.refresh_configs()
        row = self.win.config_rows["Grow"]
        self.pump(0.08)
        self.assertLess(row.winfo_height(), row.h)  # still growing
        self.pump(0.4)
        self.assertEqual(row.winfo_height(), row.h)
        names = [r.name for r in self.win.config_list.inner.pack_slaves()]
        self.assertEqual(names, sorted(names, key=str.lower))  # inserted in order
        self.win.store.delete("Grow")
        self.win.refresh_configs()
        self.pump(0.08)
        self.assertTrue(row.winfo_exists() and row.winfo_height() < row.h)  # shrinking
        self.pump(0.4)
        self.assertFalse(row.winfo_exists())
        self.assertNotIn("Grow", self.win.config_rows)


class SettingsPageTests(GuiBase):
    def open(self):
        self.win.open_settings()
        self.pump(0.6)
        self.assertFalse(self.win.stage.sliding)
        return self.win.settings

    def test_settings_slides_in_and_out(self) -> None:
        self.win.open_settings()
        self.pump(0.1)
        self.assertTrue(self.win.stage.sliding)
        self.assertGreater(self.win.settings.winfo_x(), 0)  # still coming in
        self.pump(0.5)
        self.assertEqual(self.win.settings.winfo_x(), 0)
        self.assertFalse(self.win.shell.winfo_ismapped())
        view = self.win.settings
        self.root.event_generate("<Escape>")
        self.pump(0.6)
        self.assertIsNone(self.win.settings)
        self.assertFalse(view.winfo_exists())
        self.assertEqual(self.win.shell.winfo_x(), 0)

    def test_tabs_slide_and_settings_save(self) -> None:
        view = self.open()
        view.tabs.set("roblox")
        view.show_tab("roblox")
        self.pump(0.1)
        self.assertTrue(view.tab_stage.sliding)
        self.pump(0.4)
        self.assertEqual(view.current_tab, "roblox")
        view.tabs.set("general")
        view.show_tab("general")
        self.pump(0.45)
        view.port.var.set("60123")
        self.assertEqual(self.win.store.settings.port, 60123)

    def test_installation_tab_without_setup(self) -> None:
        view = self.open()
        view.show_tab("install")
        self.pump(0.45)
        self.assertIsNone(view.setup_exe)
        page = view.tab_stage.current
        buttons = [w for w in page.winfo_children()[0].body.winfo_children()[-1].winfo_children()]
        self.assertEqual([b.text for b in buttons], ["Repair", "Reinstall", "Uninstall"])
        self.assertTrue(all(not b.enabled for b in buttons))

    def test_installation_actions_launch_setup(self) -> None:
        launched = []
        installation = self.gui.installation
        orig_find, orig_launch = installation.find_setup, installation.launch_setup
        installation.find_setup = lambda: Path("C:/Interceptium/Interceptium Setup.exe")
        installation.launch_setup = lambda action, setup=None: launched.append((action, str(setup))) or True
        closed = []
        self.win.on_close = lambda: closed.append(True)  # keep the window for the test
        try:
            view = self.open()
            view.show_tab("install")
            self.pump(0.45)
            page = view.tab_stage.current
            buttons = page.winfo_children()[0].body.winfo_children()[-1].winfo_children()
            self.assertTrue(all(b.enabled for b in buttons))
            view._maintenance("uninstall")
            self.wait_popup()
            self.win.pages[-1].primary()
            self.pump(0.3)
            self.assertEqual(launched, [("uninstall", str(Path("C:/Interceptium/Interceptium Setup.exe")))])
            self.assertEqual(closed, [True])
        finally:
            installation.find_setup, installation.launch_setup = orig_find, orig_launch
            del self.win.on_close  # restore the real close so tearDown destroys the window

    def wait_popup(self) -> None:
        from interceptium.ui import overlay
        self.pump(overlay.OPEN_TIME + 0.3)


if __name__ == "__main__":
    unittest.main()
