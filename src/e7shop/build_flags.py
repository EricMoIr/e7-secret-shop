"""Build-time flags.

build.spec generates an `e7shop_build_flags` module and bundles it into the exe; `scripts/build.ps1
-Release` sets RELEASE = True there. Running from source there's no such module, so these are the
development defaults.
"""

try:
    from e7shop_build_flags import RELEASE  # type: ignore[import-not-found]
except ImportError:
    RELEASE = False
