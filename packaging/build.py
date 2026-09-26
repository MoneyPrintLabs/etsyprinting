"""Build the downloadable desktop app for the platform this runs on.

    pip install -e . "pyinstaller>=6.10"
    python packaging/build.py

Windows produces one self-contained `stallkit-<version>-windows.exe`; macOS produces
`stallkit-<version>-macos.zip` holding `stallkit.app`. Both land in `dist/release/`.
Neither needs Python on the machine it runs on.

Releases are built by .github/workflows/release.yml on GitHub's runners, not on a
developer's machine, so no local path or user name ends up inside a published binary.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "app"
DIST = ROOT / "dist"
RELEASE = DIST / "release"

sys.path.insert(0, str(ROOT))
from stallkit import __version__  # noqa: E402
from stallkit.desktop import icon  # noqa: E402

WINDOWS_VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple}, prodvers={tuple}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'stallkit contributors'),
      StringStruct('FileDescription', 'stallkit - Etsy seller automation'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'stallkit'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'stallkit.exe'),
      StringStruct('ProductName', 'stallkit'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def _version_tuple() -> tuple[int, int, int, int]:
    parts = [int(p) for p in __version__.split(".")[:3] if p.isdigit()]
    return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]


def main() -> int:
    import PyInstaller.__main__

    system = platform.system()
    if system not in ("Windows", "Darwin"):
        print(f"Desktop builds are made for Windows and macOS, not {system}.")
        return 1

    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    RELEASE.mkdir(parents=True, exist_ok=True)

    args = [
        str(ROOT / "packaging" / "stallkit_app.py"),
        "--name", "stallkit",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        # Commands are imported lazily and Rich loads its Unicode tables by name;
        # collect both whole rather than trust static analysis to find them.
        "--collect-submodules", "stallkit",
        "--collect-submodules", "rich",
    ]

    if system == "Windows":
        ico = BUILD / "stallkit.ico"
        icon.write_ico(str(ico))
        version_file = BUILD / "version.txt"
        version_file.write_text(
            WINDOWS_VERSION_INFO.format(tuple=_version_tuple(), version=__version__), encoding="utf-8"
        )
        args += ["--onefile", "--icon", str(ico), "--version-file", str(version_file)]
    else:
        png = BUILD / "stallkit.png"
        icon.render(512).save(png)
        args += ["--icon", str(png), "--osx-bundle-identifier", "io.github.moneyprintlabs.stallkit"]

    PyInstaller.__main__.run(args)

    if system == "Windows":
        target = RELEASE / f"stallkit-{__version__}-windows.exe"
        shutil.copy2(DIST / "stallkit.exe", target)
    else:
        target = RELEASE / f"stallkit-{__version__}-macos.zip"
        target.unlink(missing_ok=True)
        # ditto keeps the bundle's symlinks and permissions; zipfile would not.
        subprocess.run(
            ["ditto", "-c", "-k", "--keepParent", str(DIST / "stallkit.app"), str(target)], check=True
        )
    print(f"Built {target} ({target.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
