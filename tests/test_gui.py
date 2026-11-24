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
class GuiTests(unittest.TestCase):
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
            time.sleep(0.005)

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

    def test_rule_dialog_validation_and_save(self) -> None:
        dlg = self.gui.RuleDialog(self.root, None, self.win.store.folder)
        dlg.targets.var.set("12")  # too small to be an asset ID
        dlg._save()
        self.assertTrue(dlg.error.cget("text"))
        pack = self.win.store.folder / "Pack"
        pack.mkdir()
        (pack / "sky.png").write_bytes(b"png")
        dlg.name.var.set("Local sky")
        dlg.targets.var.set("123456 Image")
        dlg.mode.set("local")
        dlg._mode_changed("local")
        dlg.value.var.set(str(pack / "sky.png"))
        dlg._save()
        self.assertEqual(dlg.result["local_path"], "/Pack/sky.png")
        self.assertEqual(dlg.result["replace_ids"], ["123456", "Image"])

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
        self.gui.ConfirmDialog.show = lambda self: setattr(self, "result", True)  # type: ignore[method-assign]
        self.win.delete_config()
        self.assertEqual(self.win.store.names(), ["Default"])
        self.assertEqual(self.win.selected, "Default")


if __name__ == "__main__":
    unittest.main()
