#!/usr/bin/env python3
"""
_launcher.py — PyInstaller entry-point
─────────────────────────────────────────────────────────────────────────────
When the user runs the bundled executable this script decides what to do:

  • No config.json  → run setup wizard   (first run)
  • config.json exists + --setup flag    → run setup wizard   (re-configure)
  • config.json exists + --uninstall     → run uninstaller
  • Otherwise                            → run the dashboard

The bundled setup.py / dashboard.py / uninstall.py are extracted to a
temp dir by PyInstaller (sys._MEIPASS) and imported from there.
─────────────────────────────────────────────────────────────────────────────
"""

import sys
import os
import importlib.util
from pathlib import Path

# ── Resolve paths ──────────────────────────────────────────────────────────
# When frozen, sys._MEIPASS is the temp extraction dir.
# When running from source, fall back to the script's own directory.
_BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)).resolve()
_CWD  = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

# config.json lives next to the executable (not in _MEIPASS)
_CONFIG = _CWD / 'config.json'


def _load(name: str):
    """Dynamically load one of the bundled .py files by name."""
    spec = importlib.util.spec_from_file_location(name, _BASE / f'{name}.py')
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    args = sys.argv[1:]

    if '--uninstall' in args:
        sys.argv = [sys.argv[0]] + [a for a in args if a != '--uninstall']
        _load('uninstall').main()

    elif not _CONFIG.exists() or '--setup' in args:
        sys.argv = [sys.argv[0]] + [a for a in args if a != '--setup']
        _load('setup').main()

    else:
        _load('dashboard').main() if hasattr(_load('dashboard'), 'main') else \
            exec((_BASE / 'dashboard.py').read_text(), {'__name__': '__main__'})


if __name__ == '__main__':
    main()
