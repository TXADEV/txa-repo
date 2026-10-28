"""PyInstaller standalone entry point for txacreate (TXA Manifest Authoring & PR Publisher)."""
import os
import sys

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from _splash import init_console, print_banner
from txa.create.cli import main

if __name__ == "__main__":
    init_console()
    print_banner("TXA Create (txacreate)", "Publish installers and manifest cards to the TXA repository")
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\033[33m! Thao tác đã được huỷ bởi người dùng (Cancelled).\033[0m")
        sys.exit(130)
