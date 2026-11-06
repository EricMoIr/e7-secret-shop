#!/usr/bin/env bash
# Build the Windows exe from WSL. Windows Python can't build reliably from a \\wsl$ path, so this
# copies the working tree (tracked and untracked files, minus what .gitignore excludes) to a
# Windows folder, runs scripts/build.ps1 there, and prints where the exe is.
#
# Tesseract is prepared here first (scripts/fetch_tesseract.py), because WSL has the binutils `strip`
# that removes ~95 MB of debug info from its DLLs; Windows usually doesn't.
#
#     scripts/build-from-wsl.sh [--release] [windows-folder-as-wsl-path]
#
# The default folder is %USERPROFILE%\e7shop-build. Its .venv-build is kept between builds.
set -euo pipefail

release=()
if [[ ${1:-} == --release ]]; then
    release=(-Release)
    shift
fi
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
if [[ $# -ge 1 ]]; then
    win_dir=$1
else
    profile=$(cmd.exe /c "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')
    win_dir="$(wslpath "$profile")/e7shop-build"
fi

python3 "$repo/scripts/fetch_tesseract.py"

mkdir -p "$win_dir"
# Replace the sources so deleted files don't linger; keep .venv-build, build/ and dist/.
rm -rf "$win_dir/src" "$win_dir/packaging" "$win_dir/scripts" "$win_dir/tests" "$win_dir/vendor/tesseract"
(cd "$repo" && git ls-files -z --cached --others --exclude-standard | tar --null --ignore-failed-read -T - -cf -) | tar -xf - -C "$win_dir"
mkdir -p "$win_dir/vendor"
cp -r "$repo/vendor/tesseract" "$win_dir/vendor/"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$win_dir/scripts/build.ps1")" "${release[@]}"
echo "Exe: $(wslpath -w "$win_dir/dist/E7SecretShop.exe")"
