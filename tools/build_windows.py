"""Builds the Windows release without PyInstaller.

Output in dist/:
  bundle/                       what gets installed:
    Interceptium.exe            tiny launcher (windows/launcher.c)
    runtime/                    official Python embeddable runtime (signed by the PSF),
                                plus tkinter and the pinned `cryptography` wheel
    app/                        Interceptium and its setup, as Python source + bytecode
  Interceptium-Setup-<v>.exe    windows/setup_stub.c with bundle/ appended (tools/pack_setup.py)

Run on Windows with the same Python version as the runtime it downloads, and with
the Visual Studio Build Tools ("Desktop development with C++") installed.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import pack_setup  # noqa: E402
from interceptium import __version__  # noqa: E402

DIST = ROOT / "dist"
BUNDLE = DIST / "bundle"
BUILD = ROOT / "build" / "windows"
RUNTIME = BUNDLE / "runtime"
APP = BUNDLE / "app"
SKIP = {"__pycache__", "test", "tests", "idle_test"}


def step(text: str) -> None:
    print(f"== {text}", flush=True)


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=lambda _d, names: [n for n in names if n in SKIP or n.endswith(".pyc")],
                    dirs_exist_ok=True)


def run(args: list[str], **kw) -> None:
    print("  $", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kw)


# -- runtime ----------------------------------------------------------------------
def fetch_runtime(version: str) -> Path:
    name = f"python-{version}-embed-amd64.zip"
    cached = BUILD / name
    if not cached.exists():
        url = f"https://www.python.org/ftp/python/{version}/{name}"
        step(f"download {url}")
        BUILD.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response, open(cached, "wb") as out:
            shutil.copyfileobj(response, out)
    return cached


def check_signatures(files: list[Path]) -> None:
    """Refuse to ship a runtime whose executables aren't validly signed by the Python Software Foundation."""
    script = (
        "$ErrorActionPreference='Stop';"
        "foreach ($f in $env:CHECK_FILES.Split('|')) {"
        "  $s = Get-AuthenticodeSignature -LiteralPath $f;"
        "  if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Python Software Foundation') {"
        "    throw ('bad signature: ' + $f + ' ' + $s.Status + ' ' + $s.SignerCertificate.Subject) };"
        "  Write-Host ('  signed by PSF: ' + (Split-Path $f -Leaf)) }"
    )
    env = dict(os.environ, CHECK_FILES="|".join(str(f) for f in files))
    run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=env)


def build_runtime() -> None:
    version = platform.python_version()
    tag = f"python{sys.version_info.major}{sys.version_info.minor}"
    step(f"runtime: Python {version} embeddable")
    with zipfile.ZipFile(fetch_runtime(version)) as zf:
        zf.extractall(RUNTIME)
    check_signatures([RUNTIME / "python.exe", RUNTIME / "pythonw.exe", RUNTIME / f"{tag}.dll"])

    step("runtime: add tkinter (from this Python, same version)")
    base = Path(sys.base_prefix)
    copy_tree(base / "Lib" / "tkinter", RUNTIME / "Lib" / "tkinter")
    for dll in (base / "DLLs").iterdir():
        low = dll.name.lower()
        if low == "_tkinter.pyd" or (low.endswith(".dll") and low.startswith(("tcl", "tk", "zlib", "libtommath"))):
            shutil.copy2(dll, RUNTIME / dll.name)
    if (base / "tcl").is_dir():
        copy_tree(base / "tcl", RUNTIME / "tcl")

    step("runtime: path configuration")
    (RUNTIME / f"{tag}._pth").write_text(f"{tag}.zip\n.\nLib\nsite-packages\n", encoding="utf-8")

    step("runtime: pinned dependencies")
    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile", "--only-binary=:all:",
         "--target", RUNTIME / "site-packages", "-r", ROOT / "requirements.txt"])


# -- app ------------------------------------------------------------------------------
def build_app() -> None:
    step("app files")
    shutil.copy2(ROOT / "run_interceptium.py", APP / "run_interceptium.py")
    copy_tree(ROOT / "src" / "interceptium", APP / "src" / "interceptium")
    copy_tree(ROOT / "assets", APP / "assets")
    copy_tree(ROOT / "setup" / "interceptium_setup", APP / "setup" / "interceptium_setup")
    shutil.copy2(ROOT / "setup" / "run_setup.py", APP / "setup" / "run_setup.py")
    shutil.copy2(ROOT / "LICENSE", BUNDLE / "LICENSE.txt")
    step("precompile bytecode (the launcher runs Python with -B, so nothing is written later)")
    for folder in (APP, RUNTIME / "Lib", RUNTIME / "site-packages"):
        run([sys.executable, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash", folder])


# -- native programs -------------------------------------------------------------------
def vcvars() -> Path:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    out = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                          "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                         capture_output=True, text=True, check=True).stdout.strip().splitlines()
    if not out:
        raise SystemExit("Visual Studio C++ build tools not found")
    path = Path(out[0]) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not path.exists():
        raise SystemExit(f"missing {path}")
    return path


def compile_program(source: str, name: str, description: str) -> Path:
    step(f"compile {source} -> {name}.exe")
    work = BUILD / name
    work.mkdir(parents=True, exist_ok=True)
    major, minor, patch = (__version__.split(".") + ["0", "0"])[:3]

    def rc_path(p: Path) -> str:
        return str(p).replace("\\", "\\\\")

    rc = (ROOT / "windows" / "resources.rc.in").read_text(encoding="utf-8")
    for key, value in {"@ICON@": rc_path(ROOT / "assets" / "logo.ico"),
                       "@MANIFEST@": rc_path(ROOT / "windows" / "app.manifest"),
                       "@V1@": major, "@V2@": minor, "@V3@": patch, "@VERSION@": __version__,
                       "@NAME@": name, "@DESCRIPTION@": description}.items():
        rc = rc.replace(key, value)
    (work / f"{name}.rc").write_text(rc, encoding="utf-8")
    exe = work / f"{name}.exe"
    batch = work / "build.bat"
    batch.write_text(
        "@echo off\r\n"
        f'call "{vcvars()}" >nul || exit /b 1\r\n'
        f'rc /nologo /fo "{work / (name + ".res")}" "{work / (name + ".rc")}" || exit /b 1\r\n'
        f'cl /nologo /O2 /W3 /GS /sdl /DUNICODE /D_UNICODE /D_CRT_SECURE_NO_WARNINGS /I "{ROOT / "windows"}" '
        f'"{ROOT / "windows" / source}" "{work / (name + ".res")}" /Fo"{work}\\\\" /Fe"{exe}" '
        "/link /SUBSYSTEM:WINDOWS /MANIFEST:NO /DYNAMICBASE /NXCOMPAT user32.lib kernel32.lib || exit /b 1\r\n",
        encoding="utf-8")
    run(["cmd.exe", "/c", str(batch)])
    return exe


# -- checks ----------------------------------------------------------------------------
def smoke_test() -> None:
    step("smoke test the bundle")
    python = RUNTIME / "python.exe"
    run([python, "-I", "-B", "-c",
         "import tkinter, cryptography, ssl, asyncio, ctypes; r = tkinter.Tk(); r.destroy(); "
         "print('tk', tkinter.TkVersion, 'cryptography', cryptography.__version__)"])
    run([python, "-I", "-B", APP / "run_interceptium.py", "--version"])
    run([python, "-I", "-B", "-c",
         f"import sys; sys.path[:0] = [r'{APP / 'setup'}', r'{APP / 'src'}']; "
         "import interceptium_setup.ui, interceptium_setup.winops; print('setup imports ok')"])


def main() -> int:
    if sys.platform != "win32":
        print("build_windows.py must run on Windows", file=sys.stderr)
        return 1
    shutil.rmtree(BUNDLE, ignore_errors=True)
    BUNDLE.mkdir(parents=True)
    APP.mkdir()
    build_runtime()
    build_app()
    launcher = compile_program("launcher.c", "Interceptium", "Interceptium")
    shutil.copy2(launcher, BUNDLE / "Interceptium.exe")
    stub = compile_program("setup_stub.c", f"Interceptium-Setup-{__version__}", "Interceptium Setup")
    smoke_test()
    step("pack the setup")
    out = pack_setup.pack(stub, BUNDLE, DIST / f"Interceptium-Setup-{__version__}.exe")
    files = len(pack_setup.files_in(BUNDLE))
    print(f"  {out.name}: {out.stat().st_size / 1e6:.1f} MB, {files} files")
    with tempfile.TemporaryDirectory() as tmp:
        names = pack_setup.unpack(out, Path(tmp))
        if len(names) != files:
            raise SystemExit("packed file list does not round-trip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
