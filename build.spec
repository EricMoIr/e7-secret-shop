# PyInstaller spec for E7SecretShop.exe (PLAN.md §3.7, phase 4). Build with scripts\build.ps1, or:
#
#     python -m PyInstaller --noconfirm build.spec
#
# The exe is a single windowed file whose manifest requires administrator rights (uac_admin), so
# Windows shows the UAC prompt on every start and a non-elevated launch is impossible.
#
# Tesseract OCR is bundled from vendor/tesseract/ (made by scripts/fetch_tesseract.py) and unpacked
# with the rest of the app at start-up; ocr.bundled_tesseract() finds it there.
#
# With E7SHOP_RELEASE=1 (scripts/build.ps1 -Release) it's a release build: no debug captures by
# default. The flag goes into a generated `e7shop_build_flags` module (see e7shop/build_flags.py).
#
# With E7SHOP_SELFTEST_BUILD=1 it builds E7SecretShop-selftest.exe instead: the same bundle, but with
# a console and no admin manifest, so the build script can run `--self-test` without a UAC prompt.

import os
from pathlib import Path

selftest = os.environ.get("E7SHOP_SELFTEST_BUILD") == "1"

release = os.environ.get("E7SHOP_RELEASE") == "1"
flags_dir = Path(SPECPATH) / "build" / "flags"
flags_dir.mkdir(parents=True, exist_ok=True)
(flags_dir / "e7shop_build_flags.py").write_text(f"RELEASE = {release}\n", encoding="utf-8")

tesseract = Path(SPECPATH) / "vendor" / "tesseract"
if not (tesseract / "tesseract.exe").is_file():
    raise SystemExit("vendor/tesseract is missing: run `python scripts/fetch_tesseract.py` first")

a = Analysis(
    ["packaging/launcher.py"],
    pathex=["src", str(flags_dir)],
    hiddenimports=["e7shop_build_flags"],
    datas=[(str(tesseract), "tesseract")],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="E7SecretShop-selftest" if selftest else "E7SecretShop",
    console=selftest,
    uac_admin=not selftest,
    upx=False,
    debug=False,
    strip=False,
    runtime_tmpdir=None,
)
