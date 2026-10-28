"""PyInstaller standalone entry point for txaci (TXA Continuous Integration & 16-Step Validator)."""
import os
import sys

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from _splash import init_console, print_banner
from txa.ci.cli import main

if __name__ == "__main__":
    init_console()
    print_banner("TXA CI (txaci)", "Automated 16-step manifest validation bot & triage gatekeeper")
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\033[33m! Thao tác đã được huỷ bởi người dùng (Cancelled).\033[0m")
        sys.exit(130)
