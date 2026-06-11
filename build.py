"""
Build script for Music Librarian.

Builds the app for the current platform and packages it into a distributable:
  - macOS  → dist/Music-Librarian-<version>-macOS.dmg
  - Windows → dist/Music-Librarian-<version>-Windows.zip
  - Linux   → dist/Music-Librarian-<version>-Linux.zip

Usage:
    python build.py
"""
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "Music Librarian"
VERSION = "1.0.0"

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"


def run(cmd: list, **kwargs):
    print(f"» {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def clean():
    for d in (DIST, BUILD):
        if d.exists():
            shutil.rmtree(d)
            print(f"removed {d}")


def build():
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "music_librarian.spec"])


def make_dmg():
    app_path = DIST / f"{APP_NAME}.app"
    if not app_path.exists():
        print(f"ERROR: {app_path} not found", file=sys.stderr)
        sys.exit(1)

    dmg_path = DIST / f"{APP_NAME.replace(' ', '-')}-{VERSION}-macOS.dmg"
    dmg_path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copytree(app_path, tmp_path / app_path.name)
        (tmp_path / "Applications").symlink_to("/Applications")

        run([
            "hdiutil", "create",
            "-volname", APP_NAME,
            "-srcfolder", str(tmp_path),
            "-ov", "-format", "UDZO",
            str(dmg_path),
        ])

    print(f"\n✓ {dmg_path.name}  ({dmg_path.stat().st_size / 1024 / 1024:.1f} MB)")


def make_zip():
    src = DIST / APP_NAME
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        sys.exit(1)

    system = "Windows" if IS_WIN else platform.system()
    zip_base = DIST / f"{APP_NAME.replace(' ', '-')}-{VERSION}-{system}"
    shutil.make_archive(str(zip_base), "zip", root_dir=DIST, base_dir=APP_NAME)

    final = Path(str(zip_base) + ".zip")
    print(f"\n✓ {final.name}  ({final.stat().st_size / 1024 / 1024:.1f} MB)")


def main():
    clean()
    build()

    if IS_MAC:
        make_dmg()
    else:
        make_zip()


if __name__ == "__main__":
    main()
