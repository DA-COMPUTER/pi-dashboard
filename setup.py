#!/usr/bin/env python3
"""
DevBoard — Cross-Platform Setup Wizard
===========================================
Runs a browser-based wizard using only Python's standard library.
No dependencies required to run the wizard itself.

Usage:
    python3 setup.py              # auto-opens browser
    python3 setup.py --no-browser # print URL only
"""

import sys, os, json, subprocess, platform, hashlib, secrets, socket
import threading, time, webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import webview as _webview
    _HAS_WEBVIEW = True
except ImportError:
    _webview      = None   # type: ignore
    _HAS_WEBVIEW  = False

# ═══════════════════════════════════════════════════════
#  GLOBALS
# ═══════════════════════════════════════════════════════
PLATFORM = platform.system()          # 'Linux' | 'Darwin' | 'Windows'
BASE_DIR  = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / 'config.json'
_server     = None
_install_state: dict = {'running': False, 'output': '', 'success': None}


# ═══════════════════════════════════════════════════════
#  SYSTEM INFO
# ═══════════════════════════════════════════════════════
def get_system_info() -> dict:
    return {
        'platform':        PLATFORM,
        'platform_detail': platform.platform(),
        'python':          sys.version.split()[0],
        'python_path':     sys.executable,
        'base_dir':        str(BASE_DIR),
        'has_flask':       _try_import('flask'),
        'has_psutil':      _try_import('psutil'),
        'has_config':      CONFIG_FILE.exists(),
        'in_venv':         sys.prefix != sys.base_prefix,
        'service_label':   {'Linux': 'systemd (user)', 'Darwin': 'launchd', 'Windows': 'Task Scheduler'}.get(PLATFORM, 'unknown'),
    }

def _try_import(name: str) -> bool:
    try:   __import__(name); return True
    except ImportError: return False


# ═══════════════════════════════════════════════════════
#  PASSWORD HASHING  (stdlib only — scrypt → pbkdf2 fallback)
# ═══════════════════════════════════════════════════════
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    try:
        dk = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
        return f"scrypt${salt}${dk.hex()}"
    except Exception:
        dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260_000)
        return f"pbkdf2${salt}${dk.hex()}"


# ═══════════════════════════════════════════════════════
#  CONFIG WRITER
# ═══════════════════════════════════════════════════════
def write_config(data: dict) -> dict:
    try:
        cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        port    = max(1024, min(65535, int(data.get('port', 5000))))
        timeout = max(1, min(120,   int(data.get('timeout', 7))))
        auth    = 'pam' if PLATFORM == 'Linux' else 'password'

        cfg.update({
            'port': port,
            'auth_mode': auth,
            'session_timeout_minutes': timeout,
            'alert_temp_warn': 60,  'alert_temp_crit': 75,
            'alert_disk_warn': 80,  'alert_disk_crit': 90,
            'alert_cpu_warn':  80,
            'watched_services': cfg.get('watched_services', []),
            'fs_root': str(Path.home()),
            'installed': cfg.get('installed', {}),
        })

        pw = data.get('password', '').strip()
        if pw:
            cfg['password_hash'] = hash_password(pw)

        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ═══════════════════════════════════════════════════════
#  DEPENDENCY INSTALLER
# ═══════════════════════════════════════════════════════
def start_install() -> dict:
    if _install_state['running']:
        return {'ok': False, 'error': 'Already running'}
    _install_state.update({'running': True, 'output': '', 'success': None})
    threading.Thread(target=_do_install, daemon=True).start()
    return {'ok': True}

def _log(msg: str):
    _install_state['output'] += msg + '\n'

def _do_install():
    pkgs = ['flask', 'psutil', 'pywebview']
    if PLATFORM == 'Linux':
        pkgs.append('python-pam')

    _log(f'[setup] Python  : {sys.executable}')
    _log(f'[setup] Packages: {", ".join(pkgs)}')
    _log(f'[setup] Platform: {PLATFORM}')
    _log('')

    cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade'] + pkgs
    # On Linux outside a venv, pip may require --break-system-packages (PEP 668)
    if PLATFORM == 'Linux' and sys.prefix == sys.base_prefix:
        cmd.append('--break-system-packages')

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
        for line in proc.stdout:
            _log(line.rstrip())
        proc.wait()
        ok = proc.returncode == 0
        _log('')
        _log('[setup] ✓ Installation complete!' if ok else f'[setup] ✗ pip exited with code {proc.returncode}')
        _install_state.update({'running': False, 'success': ok})
    except Exception as e:
        _log(f'[setup] ✗ {e}')
        _install_state.update({'running': False, 'success': False})


# ═══════════════════════════════════════════════════════
#  SERVICE REGISTRATION
# ═══════════════════════════════════════════════════════
def register_service() -> dict:
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        if   PLATFORM == 'Linux':   return _systemd_user(cfg)
        elif PLATFORM == 'Darwin':  return _launchd(cfg)
        elif PLATFORM == 'Windows': return _schtask(cfg)
        return {'ok': False, 'error': f'Unsupported platform: {PLATFORM}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

def _save_installed(cfg: dict, updates: dict):
    cfg.setdefault('installed', {}).update(updates)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def _systemd_user(cfg: dict) -> dict:
    """Installs a systemd *user* service — no sudo required."""
    unit_dir = Path.home() / '.config' / 'systemd' / 'user'
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_file = unit_dir / 'devboard.service'

    unit_file.write_text(f"""[Unit]
Description=DevBoard
After=network.target

[Service]
Type=simple
WorkingDirectory={BASE_DIR}
ExecStart={sys.executable} {BASE_DIR / 'dashboard.py'}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""")

    for cmd in [
        ['systemctl', '--user', 'daemon-reload'],
        ['systemctl', '--user', 'enable', 'devboard'],
        ['systemctl', '--user', 'restart', 'devboard'],
    ]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and cmd[2] != 'daemon-reload':
            return {'ok': False, 'error': (r.stderr or r.stdout).strip()}

    # linger = keep user services running after logout
    subprocess.run(['loginctl', 'enable-linger', os.environ.get('USER', '')],
                   capture_output=True)
    _save_installed(cfg, {'service': True, 'service_type': 'systemd-user',
                           'service_name': 'devboard', 'unit_file': str(unit_file)})
    return {'ok': True, 'detail': 'systemd user service enabled and started'}

def _launchd(cfg: dict) -> dict:
    label    = 'com.devboard.app'
    plist_dir = Path.home() / 'Library' / 'LaunchAgents'
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist = plist_dir / f'{label}.plist'
    log_dir = BASE_DIR / 'logs'
    log_dir.mkdir(exist_ok=True)

    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{BASE_DIR / 'dashboard.py'}</string>
  </array>
  <key>WorkingDirectory</key><string>{BASE_DIR}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log_dir / 'dashboard.log'}</string>
  <key>StandardErrorPath</key><string>{log_dir / 'dashboard-err.log'}</string>
</dict></plist>""")

    subprocess.run(['launchctl', 'unload', str(plist)], capture_output=True)
    r = subprocess.run(['launchctl', 'load', str(plist)], capture_output=True, text=True)
    _save_installed(cfg, {'service': True, 'service_type': 'launchd', 'service_file': str(plist)})
    if r.returncode == 0:
        return {'ok': True, 'detail': f'launchd agent installed → {plist}'}
    return {'ok': False, 'error': (r.stderr or r.stdout).strip()}

def _schtask(cfg: dict) -> dict:
    """Creates a Task Scheduler task that runs on login (no admin required)."""
    task = 'DevBoard'
    vbs  = BASE_DIR / 'launch_dashboard.vbs'
    # VBScript launches Python silently (no console window)
    vbs.write_text(
        f'CreateObject("WScript.Shell").Run '
        f'Chr(34) & "{sys.executable}" & Chr(34) & " " & '
        f'Chr(34) & "{BASE_DIR / chr(92)}dashboard.py" & Chr(34), 0, False\n'
    )
    r = subprocess.run([
        'schtasks', '/create', '/f',
        '/tn', task,
        '/tr', f'wscript.exe "{vbs}"',
        '/sc', 'ONLOGON', '/rl', 'LIMITED',
    ], capture_output=True, text=True)
    _save_installed(cfg, {'service': True, 'service_type': 'schtask',
                           'service_name': task, 'vbs': str(vbs)})
    if r.returncode == 0:
        return {'ok': True, 'detail': f'Task Scheduler task "{task}" created (runs on login)'}
    return {'ok': False, 'error': (r.stderr or r.stdout).strip()}


# ═══════════════════════════════════════════════════════
#  SHORTCUTS
# ═══════════════════════════════════════════════════════
def create_shortcut() -> dict:
    try:
        cfg  = json.loads(CONFIG_FILE.read_text())
        port = cfg.get('port', 5000)
        url  = f'http://localhost:{port}'

        if   PLATFORM == 'Linux':   created = _linux_shortcuts(url, cfg)
        elif PLATFORM == 'Darwin':  created = _mac_shortcuts(url, cfg)
        elif PLATFORM == 'Windows': created = _windows_shortcuts(url, cfg)
        else: return {'ok': False, 'error': 'Unsupported platform'}

        return {'ok': True, 'detail': '\n'.join(created)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

def _desktop_entry(url: str) -> str:
    return (f"[Desktop Entry]\nName=DevBoard\nComment=DevBoard\n"
            f"Exec=xdg-open {url}\nIcon=utilities-terminal\nTerminal=false\n"
            f"Type=Application\nCategories=System;\n")

def _linux_shortcuts(url: str, cfg: dict) -> list[str]:
    content = _desktop_entry(url)
    created = []
    desktop = Path.home() / 'Desktop'
    if desktop.exists():
        p = desktop / 'devboard.desktop'
        p.write_text(content); p.chmod(0o755)
        cfg.setdefault('installed', {})['shortcut_desktop'] = str(p)
        created.append(f'Desktop → {p}')
    menu = Path.home() / '.local' / 'share' / 'applications'
    menu.mkdir(parents=True, exist_ok=True)
    p = menu / 'devboard.desktop'
    p.write_text(content); p.chmod(0o755)
    cfg.setdefault('installed', {})['shortcut_menu'] = str(p)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    created.append(f'App menu → {p}')
    return created

def _mac_shortcuts(url: str, cfg: dict) -> list[str]:
    created = []
    desktop = Path.home() / 'Desktop'
    if desktop.exists():
        p = desktop / 'DevBoard.webloc'
        p.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            f'<plist version="1.0"><dict><key>URL</key>'
            f'<string>{url}</string></dict></plist>'
        )
        cfg.setdefault('installed', {})['shortcut_desktop'] = str(p)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        created.append(f'Desktop → {p}')
    return created

def _windows_shortcuts(url: str, cfg: dict) -> list[str]:
    content = f'[InternetShortcut]\nURL={url}\n'
    created = []
    inst = cfg.setdefault('installed', {})

    desktop = Path.home() / 'Desktop'
    if desktop.exists():
        p = desktop / 'DevBoard.url'
        p.write_text(content)
        inst['shortcut_desktop'] = str(p)
        created.append(f'Desktop → {p}')

    start = Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
    if start.exists():
        p = start / 'DevBoard.url'
        p.write_text(content)
        inst['shortcut_startmenu'] = str(p)
        created.append(f'Start Menu → {p}')

    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    return created


# ═══════════════════════════════════════════════════════
#  HTTP SERVER  (stdlib — no Flask)
# ═══════════════════════════════════════════════════════
class SetupHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if   path in ('/', '/index.html'): self._html(WIZARD_HTML)
        elif path == '/api/info':           self._json(get_system_info())
        elif path == '/api/install-status': self._json(dict(_install_state))
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        n    = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        path = self.path.split('?')[0]

        routes = {
            '/api/write-config':    lambda: write_config(body),
            '/api/start-install':   start_install,
            '/api/register-service': register_service,
            '/api/create-shortcut': create_shortcut,
            '/api/finish':          _trigger_close,
        }
        fn = routes.get(path)
        if fn:
            self._json(fn())
        else:
            self.send_response(404); self.end_headers()

    def _html(self, s: str):
        b = s.encode()
        self.send_response(200)
        self.send_header('Content-Type',   'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def _json(self, d: dict):
        b = json.dumps(d).encode()
        self.send_response(200)
        self.send_header('Content-Type',   'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def log_message(self, *_): pass   # suppress console noise


def _find_free_port(start: int = 7331) -> int:
    for p in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:   s.bind(('127.0.0.1', p)); return p
            except OSError: continue
    raise RuntimeError('No free port available in range 7331-7350')


# ═══════════════════════════════════════════════════════
#  WIZARD HTML
# ═══════════════════════════════════════════════════════
WIZARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DevBoard — Setup</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#080b10;--bg2:#0d1118;--bg3:#131a24;--bg4:#19222f;
  --bdr:#1e2a3a;--bdr2:#2a3a52;
  --acc:#00e87a;--hi:#3d9eff;--red:#ff4d6a;--warn:#ffb300;
  --txt:#c4d4ec;--txt2:#fff;--dim:#4a607a;--dim2:#2e3f56;
  --font:'Syne',sans-serif;--mono:'JetBrains Mono',monospace;
  --r:10px;--rs:8px;
}
html,body{min-height:100%;background:var(--bg);color:var(--txt);font-family:var(--font);line-height:1.5}
body{display:flex;flex-direction:column;align-items:center;padding:40px 16px 80px}

/* ── Header ── */
.logo{font-family:var(--mono);font-size:12px;color:var(--dim);margin-bottom:40px;display:flex;align-items:center;gap:8px;letter-spacing:.04em}
.logo::before{content:'';width:7px;height:7px;border-radius:50%;background:var(--acc);display:block;flex-shrink:0}

/* ── Progress bar ── */
.progress{display:flex;align-items:flex-start;gap:0;margin-bottom:36px;width:min(640px,100%)}
.pdot{flex:1;display:flex;flex-direction:column;align-items:center;gap:7px;position:relative}
.pdot:not(:last-child)::after{content:'';position:absolute;top:12px;left:calc(50% + 14px);right:calc(-50% + 14px);height:1px;background:var(--bdr2);transition:background .4s}
.pdot.done:not(:last-child)::after{background:var(--acc)}
.pc{width:25px;height:25px;border-radius:50%;border:1.5px solid var(--bdr2);background:var(--bg2);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:9px;font-weight:700;color:var(--dim);transition:all .3s;z-index:1;flex-shrink:0}
.pdot.active .pc{border-color:var(--acc);color:var(--acc);box-shadow:0 0 0 3px color-mix(in srgb,var(--acc) 15%,transparent)}
.pdot.done .pc{border-color:var(--acc);background:var(--acc);color:var(--bg)}
.pl{font-size:9px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.07em;text-align:center;white-space:nowrap}
.pdot.active .pl{color:var(--acc)}
.pdot.done .pl{color:color-mix(in srgb,var(--acc) 65%,transparent)}

/* ── Card ── */
.card{background:var(--bg2);border:1px solid var(--bdr);border-radius:16px;padding:40px 44px;width:min(640px,100%)}
@media(max-width:600px){.card{padding:28px 20px}}

h1{font-size:26px;font-weight:800;color:var(--txt2);margin-bottom:8px}
.sub{font-size:14px;color:var(--dim);line-height:1.7;margin-bottom:28px}
.sub code{font-family:var(--mono);font-size:12px;color:var(--acc);background:var(--bg3);padding:1px 7px;border-radius:4px}

/* ── Info chips ── */
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:22px}
.chip{display:flex;align-items:center;gap:5px;padding:4px 13px 4px 10px;background:var(--bg3);border:1px solid var(--bdr);border-radius:20px;font-family:var(--mono);font-size:11px;color:var(--dim)}
.chip b{color:var(--txt);font-weight:600}
.chip.ok b{color:var(--acc)}.chip.warn b{color:var(--warn)}.chip.err b{color:var(--red)}

/* ── Alert boxes ── */
.warn-box,.info-box,.ok-box,.err-box{border-radius:var(--r);padding:13px 16px;font-size:13px;line-height:1.65;margin-bottom:20px}
.warn-box{background:color-mix(in srgb,var(--warn) 7%,transparent);border:1px solid color-mix(in srgb,var(--warn) 28%,transparent);color:var(--warn);display:none}
.warn-box.show{display:block}
.info-box{background:color-mix(in srgb,var(--hi) 6%,transparent);border:1px solid color-mix(in srgb,var(--hi) 22%,transparent);color:#8cb8d8}
.ok-box{background:color-mix(in srgb,var(--acc) 6%,transparent);border:1px solid color-mix(in srgb,var(--acc) 22%,transparent);color:var(--acc);display:none;margin-top:14px}
.ok-box.show{display:block}
.err-box{background:color-mix(in srgb,var(--red) 7%,transparent);border:1px solid color-mix(in srgb,var(--red) 28%,transparent);color:var(--red);display:none;margin-top:14px}
.err-box.show{display:block}

/* ── Form ── */
.field{margin-bottom:17px}
.field label{display:block;font-size:10px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.1em;margin-bottom:7px}
.field input{width:100%;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);color:var(--txt);font-family:var(--mono);font-size:14px;padding:11px 14px;outline:none;transition:border-color .2s}
.field input:focus{border-color:var(--acc)}
.field .hint{font-size:11px;color:var(--dim2);margin-top:6px;font-family:var(--mono);line-height:1.5}
.fields-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}

/* ── Buttons ── */
.btns{display:flex;align-items:center;gap:10px;margin-top:28px;flex-wrap:wrap}
.btn{padding:11px 26px;border-radius:var(--r);font-family:var(--font);font-weight:700;font-size:14px;cursor:pointer;transition:all .15s;border:none;white-space:nowrap}
.btn.primary{background:var(--acc);color:var(--bg)}
.btn.primary:hover{filter:brightness(1.1)}
.btn.primary:disabled{background:var(--dim2);color:var(--dim);cursor:not-allowed}
.btn.secondary{background:var(--bg3);border:1px solid var(--bdr);color:var(--dim)}
.btn.secondary:hover{border-color:var(--bdr2);color:var(--txt)}
.btn-link{background:none;border:none;color:var(--dim);cursor:pointer;font-family:var(--font);font-size:13px;text-decoration:underline;padding:0}
.btn-link:hover{color:var(--txt)}

/* ── Terminal output ── */
.terminal{background:var(--bg);border:1px solid var(--bdr);border-radius:var(--rs);padding:14px 18px;font-family:var(--mono);font-size:12px;color:#8ab8d8;max-height:240px;overflow-y:auto;margin-top:18px;line-height:1.75;white-space:pre-wrap;word-break:break-all;display:none}
.terminal.show{display:block}
.terminal::-webkit-scrollbar{width:3px}
.terminal::-webkit-scrollbar-thumb{background:var(--bdr2);border-radius:2px}

/* ── Done screen ── */
.url-box{background:var(--bg3);border:1.5px solid color-mix(in srgb,var(--acc) 40%,transparent);border-radius:var(--r);padding:18px 24px;font-family:var(--mono);font-size:20px;font-weight:700;color:var(--acc);text-align:center;margin:22px 0;cursor:pointer;transition:all .15s;letter-spacing:.02em}
.url-box:hover{background:var(--bg4);border-color:var(--acc)}
.cmds-box{background:var(--bg);border:1px solid var(--bdr);border-radius:var(--rs);padding:16px 20px;font-family:var(--mono);font-size:12px;color:#7a9ab8;line-height:2;margin-top:18px}
.cmds-lbl{font-size:9px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.09em;margin-bottom:8px}

/* ── Step visibility ── */
.step{display:none}
.step.active{display:block;animation:fin .25s ease}
@keyframes fin{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>

<div class="logo">devboard &mdash; setup wizard</div>

<div class="progress" id="progress">
  <div class="pdot active" data-s="1"><div class="pc">1</div><div class="pl">Welcome</div></div>
  <div class="pdot"        data-s="2"><div class="pc">2</div><div class="pl">Configure</div></div>
  <div class="pdot"        data-s="3"><div class="pc">3</div><div class="pl">Install</div></div>
  <div class="pdot"        data-s="4"><div class="pc">4</div><div class="pl">Service</div></div>
  <div class="pdot"        data-s="5"><div class="pc">5</div><div class="pl">Shortcuts</div></div>
  <div class="pdot"        data-s="6"><div class="pc">6</div><div class="pl">Done</div></div>
</div>

<div class="card">

  <!-- ─── Step 1: Welcome ─── -->
  <div class="step active" id="s1">
    <h1>Welcome</h1>
    <p class="sub">This wizard configures DevBoard on your machine. It takes about a minute and requires no elevated permissions.</p>
    <div class="chips">
      <div class="chip" id="chip-os">OS <b id="ci-os">detecting&hellip;</b></div>
      <div class="chip">Python <b id="ci-py">&mdash;</b></div>
      <div class="chip" id="chip-flask">Flask <b id="ci-flask">&mdash;</b></div>
      <div class="chip" id="chip-psutil">psutil <b id="ci-psutil">&mdash;</b></div>
    </div>
    <div class="warn-box" id="existing-warn">
      ⚠&nbsp; A <code>config.json</code> already exists. This wizard will update it — your scripts and files are untouched.
    </div>
    <div class="info-box" id="auth-info">Detecting authentication method&hellip;</div>
    <div class="btns">
      <button class="btn primary" id="s1-next" onclick="goTo(2)" disabled>Let&rsquo;s get started &rarr;</button>
    </div>
  </div>

  <!-- ─── Step 2: Configure ─── -->
  <div class="step" id="s2">
    <h1>Configure</h1>
    <p class="sub">Set the port, login credentials, and session timeout for your dashboard.</p>
    <div class="fields-row">
      <div class="field">
        <label>Port</label>
        <input id="f-port" type="number" value="5000" min="1024" max="65535">
        <div class="hint">Default 5000 &middot; range 1024–65535</div>
      </div>
      <div class="field">
        <label>Session timeout (min)</label>
        <input id="f-timeout" type="number" value="7" min="1" max="120">
        <div class="hint">Idle minutes before auto-logout</div>
      </div>
    </div>
    <div id="pw-section">
      <div class="field">
        <label id="pw-label">Password</label>
        <input id="f-pw" type="password" placeholder="Enter a password" autocomplete="new-password">
        <div class="hint" id="pw-hint"></div>
      </div>
      <div class="field">
        <label>Confirm password</label>
        <input id="f-pw2" type="password" placeholder="Re-enter password" autocomplete="new-password">
      </div>
    </div>
    <div class="err-box" id="cfg-err"></div>
    <div class="btns">
      <button class="btn secondary" onclick="goTo(1)">&larr; Back</button>
      <button class="btn primary" onclick="saveConfig()">Save &amp; Continue &rarr;</button>
    </div>
  </div>

  <!-- ─── Step 3: Install ─── -->
  <div class="step" id="s3">
    <h1>Install Dependencies</h1>
    <p class="sub">The following packages will be installed via <code>pip</code>. Already-installed packages will be upgraded if needed.</p>
    <div class="chips" id="dep-chips"></div>
    <div class="btns" id="install-btns">
      <button class="btn secondary" onclick="goTo(2)">&larr; Back</button>
      <button class="btn primary" id="install-btn" onclick="startInstall()">Install now &rarr;</button>
    </div>
    <div class="terminal" id="install-out"></div>
    <div class="ok-box" id="install-ok">&#10003;&nbsp; All packages installed successfully.</div>
    <div class="err-box" id="install-err"></div>
    <div class="btns" id="after-btns" style="display:none">
      <button class="btn primary" onclick="goTo(4)">Continue &rarr;</button>
    </div>
  </div>

  <!-- ─── Step 4: Service ─── -->
  <div class="step" id="s4">
    <h1>Register Service</h1>
    <p class="sub" id="svc-sub">Register the dashboard as a background service so it starts automatically.</p>
    <div class="info-box" id="svc-info"></div>
    <div class="ok-box"  id="svc-ok"></div>
    <div class="err-box" id="svc-err"></div>
    <div class="btns" id="svc-btns">
      <button class="btn secondary" onclick="goTo(3)">&larr; Back</button>
      <button class="btn primary" id="svc-btn" onclick="doService()">Register &rarr;</button>
      <button class="btn-link" onclick="skipTo(5)">Skip this step</button>
    </div>
    <div class="btns" id="svc-next" style="display:none">
      <button class="btn primary" onclick="goTo(5)">Continue &rarr;</button>
    </div>
  </div>

  <!-- ─── Step 5: Shortcuts ─── -->
  <div class="step" id="s5">
    <h1>Create Shortcuts</h1>
    <p class="sub">Add a shortcut to quickly open the dashboard in your browser.</p>
    <div class="info-box" id="sc-info"></div>
    <div class="ok-box"  id="sc-ok"></div>
    <div class="err-box" id="sc-err"></div>
    <div class="btns" id="sc-btns">
      <button class="btn secondary" onclick="goTo(4)">&larr; Back</button>
      <button class="btn primary" id="sc-btn" onclick="doShortcut()">Create shortcuts &rarr;</button>
      <button class="btn-link" onclick="skipTo(6)">Skip this step</button>
    </div>
    <div class="btns" id="sc-next" style="display:none">
      <button class="btn primary" onclick="goTo(6)">Continue &rarr;</button>
    </div>
  </div>

  <!-- ─── Step 6: Done ─── -->
  <div class="step" id="s6">
    <h1>You&rsquo;re all set &#127881;</h1>
    <p class="sub">DevBoard is installed and ready. Click the URL below to open it, or run <code>python3 dashboard.py</code> manually.</p>
    <div class="url-box" id="done-url" onclick="openDash()">http://localhost:5000</div>
    <div class="cmds-box">
      <div class="cmds-lbl">Useful commands</div>
      <div id="done-cmds"></div>
    </div>
    <div class="btns" style="margin-top:28px">
      <button class="btn primary" onclick="openDash()">Open Dashboard &rarr;</button>
      <button class="btn secondary" onclick="finish()">Close wizard</button>
    </div>
  </div>

</div><!-- /card -->

<script>
let _info = {};

// ── Boot ─────────────────────────────────────────────
(async () => {
  try {
    const r = await fetch('/api/info');
    _info = await r.json();
    renderInfo();
  } catch(e) {
    document.getElementById('s1-next').disabled = false;
    document.getElementById('auth-info').textContent = 'Could not detect platform.';
  }
})();

function renderInfo() {
  const {platform, python, has_flask, has_psutil, has_config, service_label} = _info;

  // OS chip
  const osLabel = {Linux:'🐧 Linux', Darwin:'🍎 macOS', Windows:'🪟 Windows'}[platform] || platform;
  document.getElementById('ci-os').textContent = osLabel;

  document.getElementById('ci-py').textContent = python;

  set_chip('chip-flask',  'ci-flask',  has_flask,  'installed', 'not installed');
  set_chip('chip-psutil', 'ci-psutil', has_psutil, 'installed', 'not installed');

  if (has_config) document.getElementById('existing-warn').classList.add('show');

  // Auth info box
  const authMsgs = {
    Linux:   '🔐 Linux: Uses PAM system credentials (same as SSH). Set a password below — it serves as a fallback if python-pam is unavailable.',
    Darwin:  '🔐 macOS: Uses a password you set here, stored as a secure hash in config.json.',
    Windows: '🔐 Windows: Uses a password you set here, stored as a secure hash in config.json.',
  };
  document.getElementById('auth-info').textContent = authMsgs[platform] || '🔐 Uses password-based authentication.';

  // Password field labels
  if (platform === 'Linux') {
    document.getElementById('pw-label').textContent = 'Password (fallback)';
    document.getElementById('pw-hint').textContent = 'Optional on Linux — PAM handles auth. Used if python-pam is unavailable.';
  } else {
    document.getElementById('pw-label').textContent = 'Password';
    document.getElementById('pw-hint').textContent = 'Required. Min 6 characters. Stored as a secure hash.';
  }

  // Service step
  document.getElementById('svc-sub').textContent =
    `Register the dashboard as a ${service_label} service so it starts automatically in the background.`;
  const svcDetails = {
    Linux:   'Uses a systemd user service (~/.config/systemd/user/) — no sudo required. Starts on login and restarts automatically if it crashes. loginctl enable-linger ensures it persists after logout.',
    Darwin:  'Uses a launchd user agent (~/Library/LaunchAgents/) — no sudo required. Loaded at login, kept alive automatically.',
    Windows: 'Uses Windows Task Scheduler (run on logon, limited rights) — no admin required. A tiny VBScript launcher keeps the console window hidden.',
  };
  document.getElementById('svc-info').textContent = svcDetails[platform] || '';

  // Shortcuts step
  const scDetails = {
    Linux:   'Creates a .desktop file on your Desktop and in the application menu (~/.local/share/applications/).',
    Darwin:  'Creates a .webloc bookmark file on your Desktop.',
    Windows: 'Creates a .url shortcut on your Desktop and in the Start Menu Programs folder.',
  };
  document.getElementById('sc-info').textContent = scDetails[platform] || '';

  // Dep chips
  const deps = ['flask', 'psutil'];
  if (platform === 'Linux') deps.push('python-pam');
  const dc = document.getElementById('dep-chips');
  deps.forEach(d => {
    const have = (d==='flask'&&has_flask)||(d==='psutil'&&has_psutil);
    const el = document.createElement('div');
    el.className = 'chip ' + (have ? 'ok' : '');
    el.innerHTML = `<b>${d}</b>&nbsp;${have ? '&#10003;' : ''}`;
    dc.appendChild(el);
  });

  // Done URL + commands
  const port = 5000;
  document.getElementById('done-url').textContent = `http://localhost:${port}`;
  const cmdsMap = {
    Linux:   'systemctl --user status devboard\nsystemctl --user stop    devboard\nsystemctl --user start   devboard\njournalctl --user -u devboard -f',
    Darwin:  'launchctl list | grep pidashboard\nlaunchctl stop  com.devboard.app\nlaunchctl start com.devboard.app\ncat ~/Library/Logs/devboard.log',
    Windows: 'schtasks /query /tn DevBoard\ntaskkill /f /im python.exe\n# Or open Task Scheduler → DevBoard',
  };
  document.getElementById('done-cmds').textContent = cmdsMap[platform] || 'python3 dashboard.py';

  document.getElementById('s1-next').disabled = false;
}

function set_chip(chipId, valId, ok, okTxt, failTxt) {
  document.getElementById(valId).textContent = ok ? okTxt : failTxt;
  document.getElementById(chipId).classList.toggle('ok',   ok);
  document.getElementById(chipId).classList.toggle('warn', !ok);
}

// ── Navigation ───────────────────────────────────────
function goTo(n) {
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
  document.getElementById('s' + n).classList.add('active');
  document.querySelectorAll('.pdot').forEach(d => {
    const sn = +d.dataset.s;
    d.classList.toggle('active', sn === n);
    d.classList.toggle('done', sn < n);
    d.querySelector('.pc').textContent = sn < n ? '✓' : sn;
  });
  window.scrollTo({top:0, behavior:'smooth'});
}
function skipTo(n) { goTo(n); }

// ── Step 2: Config ───────────────────────────────────
async function saveConfig() {
  const port    = +document.getElementById('f-port').value;
  const timeout = +document.getElementById('f-timeout').value;
  const pw      = document.getElementById('f-pw').value;
  const pw2     = document.getElementById('f-pw2').value;
  const err     = document.getElementById('cfg-err');
  err.classList.remove('show');

  if (port < 1024 || port > 65535)         return showErr(err, 'Port must be 1024–65535.');
  if (_info.platform !== 'Linux' && !pw)   return showErr(err, 'A password is required on this platform.');
  if (pw && pw !== pw2)                    return showErr(err, 'Passwords do not match.');
  if (pw && pw.length < 6)                 return showErr(err, 'Password must be at least 6 characters.');

  try {
    const d = await post('/api/write-config', {port, timeout, password: pw});
    if (d.ok) {
      document.getElementById('done-url').textContent = `http://localhost:${port}`;
      goTo(3);
    } else {
      showErr(err, d.error || 'Failed to save config.');
    }
  } catch(e) { showErr(err, 'Error: ' + e.message); }
}

// ── Step 3: Install ──────────────────────────────────
let _poll = null;

async function startInstall() {
  document.getElementById('install-btn').disabled = true;
  document.getElementById('install-btn').textContent = 'Installing…';
  const out = document.getElementById('install-out');
  out.classList.add('show'); out.textContent = 'Starting…\n';
  await post('/api/start-install', {});
  _poll = setInterval(pollInstall, 700);
}

async function pollInstall() {
  try {
    const d = await (await fetch('/api/install-status')).json();
    const out = document.getElementById('install-out');
    out.textContent = d.output;
    out.scrollTop = out.scrollHeight;
    if (!d.running) {
      clearInterval(_poll);
      document.getElementById('install-btns').style.display = 'none';
      document.getElementById('after-btns').style.display = 'flex';
      if (d.success) document.getElementById('install-ok').classList.add('show');
      else           showErr(document.getElementById('install-err'), 'Installation failed. See output above.');
    }
  } catch(e) {}
}

// ── Step 4: Service ──────────────────────────────────
async function doService() {
  const btn = document.getElementById('svc-btn');
  btn.disabled = true; btn.textContent = 'Registering…';
  try {
    const d = await post('/api/register-service', {});
    document.getElementById('svc-btns').style.display = 'none';
    document.getElementById('svc-next').style.display = 'flex';
    if (d.ok) {
      const b = document.getElementById('svc-ok');
      b.textContent = '✓ ' + (d.detail || 'Service registered.'); b.classList.add('show');
    } else {
      showErr(document.getElementById('svc-err'), d.error || 'Registration failed.');
    }
  } catch(e) {
    showErr(document.getElementById('svc-err'), e.message);
    document.getElementById('svc-btns').style.display = 'none';
    document.getElementById('svc-next').style.display = 'flex';
  }
}

// ── Step 5: Shortcuts ────────────────────────────────
async function doShortcut() {
  const btn = document.getElementById('sc-btn');
  btn.disabled = true; btn.textContent = 'Creating…';
  try {
    const d = await post('/api/create-shortcut', {});
    document.getElementById('sc-btns').style.display = 'none';
    document.getElementById('sc-next').style.display = 'flex';
    if (d.ok) {
      const b = document.getElementById('sc-ok');
      b.innerHTML = '&#10003;&nbsp;Created:<br><small style="font-family:var(--mono)">' + esc(d.detail || '') + '</small>';
      b.classList.add('show');
    } else {
      showErr(document.getElementById('sc-err'), d.error || 'Shortcut creation failed.');
    }
  } catch(e) {
    showErr(document.getElementById('sc-err'), e.message);
    document.getElementById('sc-btns').style.display = 'none';
    document.getElementById('sc-next').style.display = 'flex';
  }
}

// ── Step 6: Done ─────────────────────────────────────
function openDash() { window.open(document.getElementById('done-url').textContent, '_blank'); }
async function finish() {
  await post('/api/finish', {}).catch(()=>{});
  window.close();
}

// ── Utilities ────────────────────────────────────────
async function post(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  return r.json();
}
function showErr(el, msg) { el.textContent = msg; el.classList.add('show'); }
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// Enter-key shortcut on confirm-password field
document.getElementById('f-pw2').addEventListener('keydown', e => { if(e.key==='Enter') saveConfig(); });
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════
#  CLOSE HELPER  (called by /api/finish and window-close)
# ═══════════════════════════════════════════════════════
def _trigger_close() -> dict:
    """Return immediately so the HTTP response is sent, then close."""
    threading.Thread(target=_do_close, daemon=True).start()
    return {'ok': True}

def _do_close():
    time.sleep(0.35)   # let the JSON response reach the page first
    # Destroy the webview window (safe to call from any thread)
    if _HAS_WEBVIEW:
        try:
            for w in _webview.windows:
                w.destroy()
        except Exception:
            pass
    # Shut down the HTTP server
    global _server
    if _server:
        try:
            _server.shutdown()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════
def main():
    global _server

    print()
    print('─' * 54)
    print('  DevBoard — Setup Wizard')
    print(f'  Platform : {PLATFORM} ({platform.machine()})')
    print(f'  Python   : {sys.version.split()[0]}')
    print(f'  Base dir : {BASE_DIR}')
    print(f'  Window   : {"pywebview" if _HAS_WEBVIEW else 'browser (pywebview not installed)'}')
    print('─' * 54)

    # Start the stdlib HTTP server on a background daemon thread so it
    # doesn't block — both webview and browser modes need this.
    wport   = _find_free_port()
    _server = HTTPServer(('127.0.0.1', wport), SetupHandler)
    url     = f'http://localhost:{wport}'
    srv_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    srv_thread.start()

    if _HAS_WEBVIEW and '--browser' not in sys.argv:
        # ── Native window mode ──────────────────────────────
        print(f'\n  Opening setup window…')
        print('  Close the window or complete the wizard to exit.\n')
        win = _webview.create_window(
            'DevBoard — Setup',
            url,
            width=860, height=740,
            min_size=(720, 580),
            resizable=True,
            on_top=False,
            background_color='#080b10',
        )
        _webview.start(debug=False)
        # Window closed — make sure server is also stopped
        if _server:
            try: _server.shutdown()
            except Exception: pass

    else:
        # ── Browser fallback ────────────────────────────────
        if '--no-browser' not in sys.argv:
            threading.Timer(0.7, lambda: webbrowser.open(url)).start()
            print(f'\n  Wizard → {url}  (opening in browser…)')
        else:
            print(f'\n  Wizard → {url}')
        print('  Press Ctrl+C to cancel\n')
        try:
            srv_thread.join()   # block until /api/finish shuts the server
        except KeyboardInterrupt:
            print('\n  Setup cancelled.')
            if _server:
                try: _server.shutdown()
                except Exception: pass

    print('  Setup wizard closed.\n')


if __name__ == '__main__':
    main()
