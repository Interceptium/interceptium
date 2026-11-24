"""Config library and Fleasion-format rule tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from interceptium.config import ConfigStore  # noqa: E402
from interceptium.router import AssetRouter  # noqa: E402
from interceptium.rules import compile_rules, parse_target_text  # noqa: E402


class Rules(unittest.TestCase):
    def test_modes_types_and_legacy(self) -> None:
        rs = compile_rules([
            {"replace_ids": ["1000", "Image"], "mode": "id", "with_id": "2000"},
            {"replace_ids": [3000], "remove": True},  # legacy boolean
            {"replace_ids": ["4000"], "mode": "id", "with_id": None},  # empty -> remove
            {"replace_ids": ["5000"], "mode": "cdn", "cdn_url": "https://example.com/x.png"},
            {"replace_ids": ["6000"], "mode": "id", "with_id": 1},  # dummy target ignored
            {"replace_ids": ["13"], "mode": "remove"},  # bare small number is not an asset ID
        ])
        self.assertEqual(rs.lookup(1000), ("replace", 2000))
        self.assertEqual(rs.lookup(77777, 1), ("replace", 2000))  # any Image
        self.assertEqual(rs.lookup(3000), ("remove", None))
        self.assertEqual(rs.lookup(4000), ("remove", None))
        self.assertIsNone(rs.lookup(5000))
        self.assertEqual(rs.skipped_cdn, 1)
        self.assertIsNone(rs.lookup(6000))
        self.assertIsNone(rs.lookup(13))

    def test_later_rules_win(self) -> None:
        rs = compile_rules([
            {"replace_ids": ["1000"], "mode": "id", "with_id": 2000},
            {"replace_ids": ["1000"], "mode": "id", "with_id": 3000},
        ])
        self.assertEqual(rs.lookup(1000), ("replace", 3000))

    def test_batch_by_type_name(self) -> None:
        rs = compile_rules([{"replace_ids": ["Audio"], "mode": "remove"}])
        body = json.dumps([
            {"requestId": "a", "assetId": 900, "assetType": "Audio"},
            {"requestId": "b", "assetId": 901, "assetTypeId": 1},
        ]).encode()
        out = json.loads(AssetRouter().rewrite_batch_request(body, rs))
        self.assertEqual([e["assetId"] for e in out], [901])

    def test_parse_target_text(self) -> None:
        self.assertEqual(parse_target_text("123456, Image 789012"), ["123456", "Image", "789012"])
        with self.assertRaises(ValueError):
            parse_target_text("12")
        with self.assertRaises(ValueError):
            parse_target_text("hello")


class Library(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ConfigStore(self.root / "data")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_config_and_toggle(self) -> None:
        self.assertEqual(self.store.names(), ["Default"])
        self.assertTrue(self.store.is_enabled("Default"))
        self.store.save_config("Default", {"replacement_rules": [{"replace_ids": ["1000"], "mode": "remove"}]})
        self.assertTrue(self.store.active_rules())
        self.store.set_enabled("Default", False)
        self.assertFalse(self.store.active_rules())
        reopened = ConfigStore(self.root / "data")
        self.assertFalse(reopened.is_enabled("Default"))

    def test_multiple_enabled_configs_combine(self) -> None:
        self.store.create("Skies")
        self.store.save_config("Skies", [{"replace_ids": ["1000"], "mode": "id", "with_id": 2000}])
        self.store.create("Sounds")
        self.store.save_config("Sounds", {"replacement_rules": [{"replace_ids": ["3000"], "mode": "remove"}]})
        self.store.set_enabled("Skies", True)
        rs = self.store.active_rules()
        self.assertIsNotNone(rs.lookup(1000))
        self.assertIsNone(rs.lookup(3000))
        self.store.set_enabled("Sounds", True)
        self.assertIsNotNone(self.store.active_rules().lookup(3000))

    def test_import_fleasion_files_any_encoding(self) -> None:
        payload = {"replacement_rules": [{"name": "тест", "replace_ids": ["1000"], "mode": "remove", "extra": 1}]}
        bom = self.root / "Fleasion Pack.json"
        bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode())
        utf16 = self.root / "Other.json"
        utf16.write_bytes(json.dumps(payload).encode("utf-16"))
        a = self.store.import_file(bom)
        b = self.store.import_file(utf16)
        self.assertEqual(a, "Fleasion Pack")
        self.assertEqual(b, "Other")
        again = self.store.import_file(bom)
        self.assertEqual(again, "Fleasion Pack (2)")
        data = self.store.load_config(a)
        self.assertEqual(data["replacement_rules"][0]["name"], "тест")
        self.assertEqual(data["replacement_rules"][0]["extra"], 1)  # unknown keys kept

    def test_import_rejects_garbage(self) -> None:
        bad = self.root / "bad.json"
        bad.write_bytes(b"\x00\x01\x02 not json")
        with self.assertRaises(ValueError):
            self.store.import_file(bad)

    def test_portable_local_paths(self) -> None:
        pack = self.store.folder / "Pack"
        pack.mkdir()
        (pack / "sky.png").write_bytes(b"x")
        self.assertEqual(Path(self.store.resolve_local("/Pack/sky.png")), pack / "sky.png")
        self.assertEqual(Path(self.store.resolve_local("/../etc/passwd")), Path("/../etc/passwd"))

    def test_names_are_sanitized(self) -> None:
        name = self.store.create("../evil/name")
        self.assertNotIn("/", name)
        self.assertTrue(self.store.path_for(name).parent == self.store.folder)

    def test_rename_and_delete_keep_enabled_list(self) -> None:
        self.store.rename("Default", "Main")
        self.assertEqual(self.store.settings.enabled_configs, ["Main"])
        self.store.delete("Main")
        self.assertEqual(self.store.settings.enabled_configs, [])


if __name__ == "__main__":
    unittest.main()
