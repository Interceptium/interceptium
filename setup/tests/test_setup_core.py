"""Installer logic tests (run anywhere; Windows calls are faked)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "setup"))
sys.path.insert(0, str(ROOT / "src"))

from interceptium_setup import __main__ as entry  # noqa: E402
from interceptium_setup.core import (  # noqa: E402
    APP_EXE, MANIFEST, SETUP_EXE, SHORTCUT, Installer, Manifest, Options, SetupError,
)
from interceptium_setup.fakeops import FakeOps  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ops = FakeOps(self.root / "sandbox")
        self.payload = self.root / "payload" / APP_EXE
        self.payload.parent.mkdir()
        self.payload.write_bytes(b"APP" * 400_000)
        self.setup_exe = self.root / "download" / "Interceptium-Setup-1.0.0.exe"
        self.setup_exe.parent.mkdir()
        self.setup_exe.write_bytes(b"SETUP" * 1000)
        self.installer = Installer(self.ops, self.payload, self.setup_exe, "1.0.0")
        self.dest = self.root / "Programs" / "Interceptium"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def install(self, **kw) -> Manifest:
        return self.installer.install(Options(self.dest, **kw))


class InstallTests(Fixture):
    def test_install_writes_files_shortcuts_and_entry(self) -> None:
        seen: list[float] = []
        manifest = self.installer.install(Options(self.dest), lambda v, _t: seen.append(v))
        self.assertEqual((self.dest / APP_EXE).read_bytes(), self.payload.read_bytes())
        self.assertEqual((self.dest / SETUP_EXE).read_bytes(), self.setup_exe.read_bytes())
        self.assertTrue((self.dest / MANIFEST).is_file())
        self.assertTrue((self.ops.start_menu_dir() / SHORTCUT).is_file())
        self.assertTrue((self.ops.desktop_dir() / SHORTCUT).is_file())
        entry = self.ops.read_uninstall_entry()
        self.assertEqual(entry["DisplayName"], "Interceptium")
        self.assertEqual(entry["DisplayVersion"], "1.0.0")
        self.assertEqual(entry["InstallLocation"], str(self.dest))
        self.assertIn(f'"{self.dest / SETUP_EXE}" --uninstall', entry["UninstallString"])
        self.assertIn("--maintenance", entry["ModifyPath"])
        self.assertIsInstance(entry["EstimatedSize"], int)
        self.assertEqual(seen, sorted(seen))  # progress only moves forward
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(self.installer.installed().directory, manifest.directory)

    def test_no_desktop_shortcut_option(self) -> None:
        self.install(desktop_shortcut=False)
        self.assertFalse((self.ops.desktop_dir() / SHORTCUT).exists())
        self.assertTrue((self.ops.start_menu_dir() / SHORTCUT).exists())

    def test_missing_payload_is_a_clear_error(self) -> None:
        installer = Installer(self.ops, None, None, "1.0.0")
        with self.assertRaises(SetupError):
            installer.install(Options(self.dest))

    def test_relative_folder_rejected(self) -> None:
        with self.assertRaises(SetupError):
            self.installer.install(Options(Path("relative/folder")))

    def test_install_over_existing_replaces_files(self) -> None:
        self.install()
        self.payload.write_bytes(b"NEW")
        self.install()
        self.assertEqual((self.dest / APP_EXE).read_bytes(), b"NEW")
        self.assertFalse(list(self.dest.glob("*.partial")))


class UninstallTests(Fixture):
    def test_uninstall_removes_everything_it_made(self) -> None:
        self.install()
        data = self.ops.user_data_dir()
        data.mkdir(parents=True)
        (data / "settings.json").write_text("{}")
        self.installer.uninstall(self.dest)
        self.assertFalse(self.dest.exists())
        self.assertFalse((self.ops.start_menu_dir() / SHORTCUT).exists())
        self.assertFalse((self.ops.desktop_dir() / SHORTCUT).exists())
        self.assertIsNone(self.ops.read_uninstall_entry())
        self.assertIsNone(self.installer.installed())
        self.assertIn("remove_roblox_changes:Interceptium.exe", self.ops.calls)
        self.assertTrue(data.exists(), "configs are kept unless asked")

    def test_uninstall_can_remove_user_data(self) -> None:
        self.install()
        data = self.ops.user_data_dir()
        (data / "configs").mkdir(parents=True)
        self.installer.uninstall(self.dest, remove_data=True)
        self.assertFalse(data.exists())

    def test_uninstall_never_deletes_files_it_did_not_create(self) -> None:
        self.install()
        mine = self.dest / "my-notes.txt"
        mine.write_text("keep me")
        self.installer.uninstall(self.dest)
        self.assertTrue(mine.exists())
        self.assertFalse((self.dest / APP_EXE).exists())

    def test_tampered_manifest_cannot_escape_install_folder(self) -> None:
        self.install()
        victim = self.root / "victim.txt"
        victim.write_text("do not delete")
        manifest = json.loads((self.dest / MANIFEST).read_text())
        manifest["files"] += ["../victim.txt", str(victim), "..\\victim.txt"]
        other_link = self.root / "Elsewhere" / "Other.lnk"
        other_link.parent.mkdir()
        other_link.write_text("not ours")
        manifest["shortcuts"] += [str(victim), str(other_link)]
        (self.dest / MANIFEST).write_text(json.dumps(manifest))
        self.installer.uninstall(self.dest)
        self.assertTrue(victim.exists())
        self.assertTrue(other_link.exists())


class MaintenanceTests(Fixture):
    def test_repair_restores_missing_and_damaged_files(self) -> None:
        self.install(desktop_shortcut=False)
        (self.dest / APP_EXE).write_bytes(b"damaged")
        (self.ops.start_menu_dir() / SHORTCUT).unlink()
        self.installer.repair()
        self.assertEqual((self.dest / APP_EXE).read_bytes(), self.payload.read_bytes())
        self.assertTrue((self.ops.start_menu_dir() / SHORTCUT).exists())
        self.assertFalse((self.ops.desktop_dir() / SHORTCUT).exists(), "repair keeps the original choices")

    def test_reinstall_keeps_user_data_and_options(self) -> None:
        self.install(desktop_shortcut=False)
        data = self.ops.user_data_dir()
        data.mkdir(parents=True)
        (data / "settings.json").write_text("{}")
        extra = self.dest / "stale.partial"
        steps: list[float] = []
        self.installer.reinstall(lambda v, _t: steps.append(v))
        self.assertTrue((self.dest / APP_EXE).is_file())
        self.assertTrue((data / "settings.json").exists())
        self.assertFalse((self.ops.desktop_dir() / SHORTCUT).exists())
        self.assertEqual(steps, sorted(steps))
        self.assertFalse(extra.exists())

    def test_repair_requires_installation(self) -> None:
        with self.assertRaises(SetupError):
            self.installer.repair()


class EntryPointTests(Fixture):
    def test_quiet_install_and_uninstall(self) -> None:
        installer = self.installer
        args = entry.parse(["--quiet", "--dir", str(self.dest), "--no-desktop"])
        self.assertEqual(entry.run_quiet(installer, args), 0)
        self.assertTrue((self.dest / APP_EXE).is_file())
        self.assertFalse((self.ops.desktop_dir() / SHORTCUT).exists())
        args = entry.parse(["--quiet", "--uninstall"])
        self.assertEqual(entry.run_quiet(installer, args), 0)
        self.assertFalse(self.dest.exists())

    def test_quiet_failure_returns_nonzero(self) -> None:
        installer = Installer(self.ops, None, None, "1.0.0")
        args = entry.parse(["--quiet", "--dir", str(self.dest)])
        self.assertEqual(entry.run_quiet(installer, args), 1)


    def test_quiet_mode_writes_log(self) -> None:
        log = self.root / "setup.log"
        args = entry.parse(["--quiet", "--dir", str(self.dest), "--log", str(log)])
        self.assertEqual(entry.run_quiet(self.installer, args), 0)
        text = log.read_text()
        self.assertIn("Copying Interceptium", text)
        self.assertIn("finished", text)
        bad = Installer(self.ops, None, None, "1.0.0")
        self.assertEqual(entry.run_quiet(bad, entry.parse(["--quiet", "--log", str(log)])), 1)
        self.assertIn("failed: This setup file is incomplete", log.read_text())



class FolderPayloadTests(unittest.TestCase):
    """The Windows build installs a folder: launcher, signed Python runtime and the app."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ops = FakeOps(self.root / "sandbox")
        self.bundle = self.root / "bundle"
        for name, data in {
            "Interceptium.exe": b"launcher",
            "runtime/pythonw.exe": b"py",
            "runtime/Lib/tkinter/__init__.py": b"tk",
            "app/run_interceptium.py": b"run",
            "app/src/interceptium/__init__.py": b"init",
        }.items():
            path = self.bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.installer = Installer(self.ops, self.bundle, None, "1.0.0")
        self.dest = self.root / "Programs" / "Interceptium"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_installs_and_removes_the_whole_tree(self) -> None:
        manifest = self.installer.install(Options(self.dest))
        self.assertEqual((self.dest / "runtime" / "Lib" / "tkinter" / "__init__.py").read_bytes(), b"tk")
        self.assertIn("app/src/interceptium/__init__.py", manifest.files)
        self.installer.uninstall(self.dest)
        self.assertFalse(self.dest.exists())

    def test_user_files_and_their_folders_survive_uninstall(self) -> None:
        self.installer.install(Options(self.dest))
        mine = self.dest / "runtime" / "notes.txt"
        mine.write_text("mine")
        self.installer.uninstall(self.dest)
        self.assertTrue(mine.exists())
        self.assertFalse((self.dest / "runtime" / "pythonw.exe").exists())
        self.assertFalse((self.dest / "app").exists())

    def test_upgrade_removes_files_the_new_version_dropped(self) -> None:
        self.installer.install(Options(self.dest))
        (self.bundle / "app" / "src" / "interceptium" / "__init__.py").unlink()
        self.installer.install(Options(self.dest))
        self.assertFalse((self.dest / "app" / "src").exists())
        self.assertTrue((self.dest / "app" / "run_interceptium.py").exists())

    def test_manifest_paths_cannot_escape(self) -> None:
        self.installer.install(Options(self.dest))
        victim = self.root / "victim.txt"
        victim.write_text("keep")
        data = json.loads((self.dest / MANIFEST).read_text())
        data["files"] += ["runtime/../../../victim.txt", "/victim.txt", "C:/victim.txt", "a\\..\\victim.txt"]
        (self.dest / MANIFEST).write_text(json.dumps(data))
        self.installer.uninstall(self.dest)
        self.assertTrue(victim.exists())

class TempCopyCleanupTests(unittest.TestCase):
    def test_old_temp_copies_are_removed_but_not_the_running_one(self) -> None:
        import tempfile as tf
        from unittest import mock

        from interceptium_setup import winops

        with tf.TemporaryDirectory() as tmp, mock.patch.object(tf, "gettempdir", return_value=tmp):
            old = Path(tmp) / "interceptium-setup-111.exe"
            current = Path(tmp) / "interceptium-setup-222.exe"
            other = Path(tmp) / "something-else.exe"
            for f in (old, current, other):
                f.write_bytes(b"x")
            winops.clean_old_temp_copies(current)
            self.assertFalse(old.exists())
            self.assertTrue(current.exists())
            self.assertTrue(other.exists())

if __name__ == "__main__":
    unittest.main()
