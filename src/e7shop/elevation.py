"""Administrator rights. The packaged .exe requests them through its manifest (PyInstaller --uac-admin);
when running from source, the app relaunches itself elevated."""

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin(*, gui: bool = False) -> bool:
    """Start an elevated copy with the same arguments. Returns False if the UAC prompt was declined.

    The elevated copy gets `--cwd` because elevated processes don't reliably inherit the working
    directory. A CLI copy opens in a new console, so it also gets `--elevated` (pause before closing).
    The GUI runs under pythonw.exe so no console window opens next to it.
    """
    executable = sys.executable
    if getattr(sys, "frozen", False):
        params = sys.argv[1:]
    else:
        params = ["-m", "e7shop", *sys.argv[1:]]
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if gui and pythonw.exists():
            executable = str(pythonw)
    if not gui:
        params.append("--elevated")
    params += ["--cwd", os.getcwd()]
    # ShellExecuteW returns a value > 32 on success.
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, subprocess.list2cmdline(params), os.getcwd(), 1)
    return result > 32
