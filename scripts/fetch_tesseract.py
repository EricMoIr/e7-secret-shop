"""Prepare vendor/tesseract/: the Tesseract OCR files bundled into E7SecretShop.exe.

    python scripts/fetch_tesseract.py [--force]

Runs on Windows or WSL, with the standard library only:

1. Downloads the pinned UB Mannheim Windows installer to vendor/downloads/ and checks its SHA-256.
2. Unpacks it with 7-Zip (the installer is never run).
3. Copies tesseract.exe, only the DLLs it loads (followed through PE imports), English language
   data and the licence files.
4. Strips debug info when a `strip` that understands PE files is available (binutils in WSL/Linux,
   MSYS2 or LLVM). Without one the files still work, they're just ~75 MB bigger.
5. Runs `tesseract --version` and `--list-langs` to check the result.
"""

import argparse
import hashlib
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

VERSION = "5.4.0.20240606"
URL = f"https://github.com/UB-Mannheim/tesseract/releases/download/v{VERSION}/tesseract-ocr-w64-setup-{VERSION}.exe"
SHA256 = "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
LANGUAGES = ["eng"]
LICENSE_FILES = ["doc/LICENSE", "doc/AUTHORS"]

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
TARGET = VENDOR / "tesseract"
IN_WSL = sys.platform == "linux" and "microsoft" in platform.release().lower()


def log(message: str) -> None:
    print(f"[tesseract] {message}", flush=True)


# Paths for Windows programs run from WSL


def win_path(path: Path) -> str:
    if not IN_WSL:
        return str(path)
    return subprocess.run(["wslpath", "-w", str(path)], check=True, capture_output=True, text=True).stdout.strip()


# Download


def download() -> Path:
    installer = VENDOR / "downloads" / URL.rsplit("/", 1)[1]
    if not installer.exists() or sha256(installer) != SHA256:
        installer.parent.mkdir(parents=True, exist_ok=True)
        log(f"Downloading {URL}")
        partial = installer.with_suffix(".part")
        with urllib.request.urlopen(URL) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(installer)
    actual = sha256(installer)
    if actual != SHA256:
        installer.unlink()
        sys.exit(f"Checksum mismatch for {installer.name}: expected {SHA256}, got {actual}")
    return installer


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Unpacking


def find_7zip() -> str:
    for name in ("7z", "7z.exe", "7zz"):
        if found := shutil.which(name):
            return found
    candidates = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"]
    if IN_WSL:
        candidates.append(Path("/mnt/c/Program Files/7-Zip/7z.exe"))
    for path in candidates:
        if path.is_file():
            return str(path)
    sys.exit("7-Zip is needed to unpack the Tesseract installer: install it from https://www.7-zip.org/")


def unpack(installer: Path, out_dir: Path) -> None:
    seven_zip = find_7zip()
    windows_tool = seven_zip.lower().endswith(".exe")
    to_arg = win_path if windows_tool else str
    log(f"Unpacking with {seven_zip}")
    subprocess.run(
        [seven_zip, "x", "-y", f"-o{to_arg(out_dir)}", to_arg(installer)],
        check=True,
        stdout=subprocess.DEVNULL,
    )


# PE imports: which DLLs does tesseract.exe load?


def pe_imports(path: Path) -> list[str]:
    """Names of the DLLs a PE file imports, directly or delay-loaded."""
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        raise ValueError(f"{path.name} isn't a PE file")
    sections_count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + (112 if magic == 0x20B else 96)  # PE32+ or PE32
    sections = []
    for i in range(sections_count):
        s = optional + optional_size + 40 * i
        vsize, vaddr, rsize, raw = struct.unpack_from("<IIII", data, s + 8)
        sections.append((vaddr, max(vsize, rsize), raw))

    def offset(rva: int) -> int:
        for vaddr, size, raw in sections:
            if vaddr <= rva < vaddr + size:
                return rva - vaddr + raw
        raise ValueError(f"RVA {rva:#x} is outside every section of {path.name}")

    def c_string(at: int) -> str:
        return data[at : data.index(b"\0", at)].decode("ascii")

    names = []
    # Directory 1: imports (20-byte descriptors, name RVA at +12). Directory 13: delay imports
    # (32-byte descriptors, name RVA at +4).
    for index, size, name_at in ((1, 20, 12), (13, 32, 4)):
        rva = struct.unpack_from("<I", data, directories + 8 * index)[0]
        if not rva:
            continue
        at = offset(rva)
        while any(data[at : at + size]):
            names.append(c_string(offset(struct.unpack_from("<I", data, at + name_at)[0])))
            at += size
    return names


def dll_closure(folder: Path, exe: str) -> list[str]:
    """The DLLs in `folder` that `exe` needs, following imports recursively."""
    local = {p.name.lower(): p.name for p in folder.glob("*.dll")}
    needed: set[str] = set()
    todo = [exe]
    while todo:
        for name in pe_imports(folder / todo.pop()):
            dll = local.get(name.lower())
            if dll and dll not in needed:
                needed.add(dll)
                todo.append(dll)
    return sorted(needed)


# Stripping and checking


def find_strip() -> str | None:
    for name in ("x86_64-w64-mingw32-strip", "strip", "llvm-strip"):
        tool = shutil.which(name)
        if not tool:
            continue
        # Linux binutils only handles PE files when built with that target.
        probe = subprocess.run([tool, "--info"], capture_output=True, text=True)
        if "llvm" in name or "pe-x86-64" in probe.stdout + probe.stderr or sys.platform == "win32":
            return tool
    return None


def strip(files: list[Path]) -> None:
    tool = find_strip()
    if not tool:
        log("No PE-capable strip found: keeping debug info (the exe will be about 75 MB bigger)")
        return
    before = sum(f.stat().st_size for f in files)
    for f in files:
        subprocess.run([tool, "--strip-debug", str(f)], check=True)
    after = sum(f.stat().st_size for f in files)
    log(f"Stripped debug info with {tool}: {before / 1e6:.0f} MB → {after / 1e6:.0f} MB")


def check(target: Path) -> None:
    if sys.platform != "win32" and not IN_WSL:
        log("Not on Windows: skipping the run check")
        return
    exe = target / "tesseract.exe"
    version = subprocess.run([str(exe), "--version"], check=True, capture_output=True, text=True).stdout
    langs = subprocess.run([str(exe), "--list-langs"], check=True, capture_output=True, text=True).stdout
    log(f"Check: {version.splitlines()[0].strip()}; languages: {', '.join(langs.split()[-len(LANGUAGES):])}")
    missing = [lang for lang in LANGUAGES if lang not in langs.split()]
    if missing:
        sys.exit(f"tesseract doesn't list {missing}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true", help="rebuild vendor/tesseract even if it's up to date")
    args = parser.parse_args()

    stamp = TARGET / "VERSION"
    if not args.force and stamp.exists() and stamp.read_text().strip() == VERSION:
        log(f"vendor/tesseract is already {VERSION}")
        return

    installer = download()
    VENDOR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=VENDOR, prefix="unpack-") as tmp:
        unpacked = Path(tmp)
        unpack(installer, unpacked)
        dlls = dll_closure(unpacked, "tesseract.exe")
        if TARGET.exists():
            shutil.rmtree(TARGET)
        (TARGET / "tessdata").mkdir(parents=True)
        binaries = []
        for name in ["tesseract.exe", *dlls]:
            copied = Path(shutil.copy2(unpacked / name, TARGET / name))
            # On a Linux filesystem (WSL), Windows programs still need the executable bit to run.
            copied.chmod(copied.stat().st_mode | 0o111)
            binaries.append(copied)
        for lang in LANGUAGES:
            shutil.copy2(unpacked / "tessdata" / f"{lang}.traineddata", TARGET / "tessdata")
        (TARGET / "licenses").mkdir()
        for name in LICENSE_FILES:
            shutil.copy2(unpacked / name, TARGET / "licenses" / Path(name).name)
    log(f"Copied tesseract.exe, {len(dlls)} DLLs and {', '.join(LANGUAGES)} language data")

    strip(binaries)
    check(TARGET)
    stamp.write_text(VERSION + "\n")
    size = sum(f.stat().st_size for f in TARGET.rglob("*") if f.is_file())
    log(f"vendor/tesseract is ready ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
