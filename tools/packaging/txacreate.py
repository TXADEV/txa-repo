"""PyInstaller entry point for txacreate."""
import sys

from txa.create.cli import main

sys.exit(main())
