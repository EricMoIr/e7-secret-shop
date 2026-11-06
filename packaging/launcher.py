"""PyInstaller entry point. `e7shop/__main__.py` uses relative imports, so it can't be the script itself."""

import sys

from e7shop.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
