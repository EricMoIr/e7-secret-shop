"""Administrator rights. The packaged .exe requests them through its manifest (PyInstaller --uac-admin);
when running from source, the app relaunches itself elevated."""

import ctypes
import os
import subprocess
import sys


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> bool:
    """Start an elevated copy with the same arguments. Returns False if the UAC prompt was declined.

    The elevated copy opens in a new console, so it gets `--elevated` (pause before closing) and
    `--cwd` (elevated processes don't reliably inherit the working directory).
    """
    if getattr(sys, "frozen", False):
        params = sys.argv[1:]
    else:
        params = ["-m", "e7shop", *sys.argv[1:]]
    params += ["--elevated", "--cwd", os.getcwd()]
    # ShellExecuteW returns a value > 32 on success.
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, subprocess.list2cmdline(params), os.getcwd(), 1)
    return result > 32
