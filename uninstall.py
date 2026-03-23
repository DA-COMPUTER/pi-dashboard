#!/usr/bin/env python3
"""
DevBoard -- Uninstaller
==========================
Reads config.json to find exactly what was installed and removes it.
Does NOT require elevated privileges (everything was installed as the current user).

Usage:
    python3 uninstall.py           # interactive confirmation
    python3 uninstall.py --yes     # skip confirmation prompt
"""

import sys, os, json, subprocess, platform
from pathlib import Path

PLATFORM    = platform.system()
BASE_DIR    = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / 'config.json'


# ═══════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════
_removed:   list[str] = []
_skipped:   list[str] = []
_failed:    list[str] = []

def _rm(path_str: str, label: str = ''):
    p = Path(path_str)
    tag = label or str(p)
    if not p.exists():
        _skipped.append(f'  - not found: {tag}')
        return
    try:
        p.unlink()
        _removed.append(f'  + removed: {tag}')
    except Exception as e:
        _failed.append(f'  x could not remove {tag}: {e}')

def _run(*cmd, ok_codes=(0,)) -> tuple[bool, str]:
    r = subprocess.run(list(cmd), capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    return r.returncode in ok_codes, out


# ═══════════════════════════════════════════════════════
#  SERVICE REMOVAL
# ═══════════════════════════════════════════════════════
def remove_service(inst: dict):
    svc_type = inst.get('service_type', '')

    if svc_type == 'systemd-user':
        name = inst.get('service_name', 'devboard')
        for cmd in [
            ['systemctl', '--user', 'stop',    name],
            ['systemctl', '--user', 'disable', name],
        ]:
            ok, out = _run(*cmd)
            label = ' '.join(cmd)
            if ok: _removed.append(f'  + {label}')
            else:  _skipped.append(f'  - {label}  ({out or "already stopped/disabled"})')
        _run('systemctl', '--user', 'daemon-reload')

        unit = inst.get('unit_file', '')
        if unit:
            _rm(unit, f'unit file: {unit}')

    elif svc_type == 'launchd':
        plist = inst.get('service_file', '')
        if plist:
            ok, out = _run('launchctl', 'unload', plist, ok_codes=(0, 1))
            if ok: _removed.append(f'  + launchctl unload {plist}')
            else:  _skipped.append(f'  - launchctl unload ({out})')
            _rm(plist, f'plist: {plist}')

    elif svc_type == 'schtask':
        name = inst.get('service_name', 'DevBoard')
        ok, out = _run('schtasks', '/delete', '/f', '/tn', name, ok_codes=(0,))
        if ok: _removed.append(f'  + schtasks /delete /tn {name}')
        else:  _skipped.append(f'  - task not found: {name}  ({out})')

        vbs = inst.get('vbs', '')
        if vbs:
            _rm(vbs, f'launcher: {vbs}')

    else:
        _skipped.append(f'  - unknown service type "{svc_type}" -- skipped')


# ═══════════════════════════════════════════════════════
#  SHORTCUT REMOVAL
# ═══════════════════════════════════════════════════════
def remove_shortcuts(inst: dict):
    for key in ('shortcut_desktop', 'shortcut_menu', 'shortcut_startmenu'):
        path = inst.get(key, '')
        if path:
            _rm(path)


# ═══════════════════════════════════════════════════════
#  CONFIG REMOVAL
# ═══════════════════════════════════════════════════════
def remove_config():
    _rm(str(CONFIG_FILE), 'config.json')


# ═══════════════════════════════════════════════════════
#  LOGS DIR (only if empty)
# ═══════════════════════════════════════════════════════
def try_remove_logs():
    logs = BASE_DIR / 'logs'
    if not logs.exists():
        return
    entries = list(logs.iterdir())
    if not entries:
        try:
            logs.rmdir()
            _removed.append(f'  + removed empty logs dir: {logs}')
        except Exception:
            pass
    else:
        _skipped.append(f'  - logs dir kept (contains {len(entries)} file(s)): {logs}')


# ═══════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════
def main():
    print()
    print('-' * 54)
    print('  DevBoard -- Uninstaller')
    print('-' * 54)
    print()

    if not CONFIG_FILE.exists():
        print('  No config.json found in this directory.')
        print('  Nothing to uninstall automatically.\n')
        print(f'  To fully remove, delete the folder:\n    {BASE_DIR}\n')
        return

    try:
        cfg  = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        inst = cfg.get('installed', {})
    except Exception as e:
        print(f'  Could not read config.json: {e}')
        sys.exit(1)

    # -- Preview what will happen --
    print('  The following will be removed:\n')

    if inst.get('service'):
        stype = inst.get('service_type', 'unknown')
        name  = inst.get('service_name') or inst.get('service_file', '')
        print(f'  - Service ({stype})  {name}')

    for key, label in [
        ('shortcut_desktop',   'Desktop shortcut'),
        ('shortcut_menu',      'App-menu entry'),
        ('shortcut_startmenu', 'Start Menu entry'),
        ('vbs',                'Launcher script'),
        ('unit_file',          'Systemd unit file'),
        ('service_file',       'Launchd plist'),
    ]:
        v = inst.get(key, '')
        if v:
            print(f'  - {label}: {v}')

    print(f'  - {CONFIG_FILE}')
    print()

    if '--yes' not in sys.argv:
        ans = input('  Proceed with uninstall? [y/N]: ').strip().lower()
        if ans != 'y':
            print('\n  Cancelled.\n')
            return
    else:
        print('  --yes flag detected, proceeding automatically.')

    print()

    # -- Execute --
    if inst.get('service'):
        print('  Stopping / removing service...')
        remove_service(inst)

    print('  Removing shortcuts...')
    remove_shortcuts(inst)

    print('  Removing config...')
    remove_config()
    try_remove_logs()

    # -- Summary --
    print()
    print('-' * 54)
    print('  Uninstall summary')
    print('-' * 54)
    for line in _removed:  print(line)
    for line in _skipped:  print(line)
    for line in _failed:   print(line)

    print()
    if _failed:
        print('  !  Some items could not be removed (see above).')
    else:
        print('  +  Uninstall complete.')

    print(f'\n  You can safely delete the dashboard folder:\n    {BASE_DIR}\n')


if __name__ == '__main__':
    main()
