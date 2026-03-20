#!/usr/bin/env python3
"""
DevBoard  v10.0 — Cross-Platform
─────────────────────────────────────────────
New in v10:
  • Runs on Linux, macOS, and Windows
  • config.json   — written by setup.py; controls port, auth, timeouts
  • Auth           — PAM on Linux · secure password hash on macOS/Windows
  • Stats          — psutil replaces /proc/stat; works everywhere
  • Ports tab      — psutil.net_connections() on non-Linux
  • Commands       — platform-appropriate sets per OS
  • Apps panel     — platform-appropriate launchers

Run once:
    python3 setup.py        ← wizard: deps, service, shortcuts, config

Manual start:
    python3 dashboard.py
"""

from flask import Flask, request, jsonify, session, send_file
import subprocess, os, re, socket, secrets, json, mimetypes, shutil, time, uuid, fnmatch, hashlib
from pathlib import Path
from datetime import datetime
import platform as _platform

try:
    import psutil as _psutil
    _HAS_PSUTIL = True
except ImportError:
    _psutil = None          # type: ignore
    _HAS_PSUTIL = False

try:
    import webview as _webview
    _HAS_WEBVIEW = True
except ImportError:
    _webview     = None     # type: ignore
    _HAS_WEBVIEW = False

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

PLATFORM = _platform.system()   # 'Linux' | 'Darwin' | 'Windows'


# ═══════════════════════════════════════════════════════
#  CONFIG  — loaded from config.json, falls back to defaults
# ═══════════════════════════════════════════════════════
_CFG_FILE = Path(__file__).parent / 'config.json'

def _load_cfg() -> dict:
    try:
        return json.loads(_CFG_FILE.read_text()) if _CFG_FILE.exists() else {}
    except Exception:
        return {}

_CFG = _load_cfg()

PORT                    = int(_CFG.get('port',                    5000))
ALERT_TEMP_WARN         = int(_CFG.get('alert_temp_warn',         60))
ALERT_TEMP_CRIT         = int(_CFG.get('alert_temp_crit',         75))
ALERT_DISK_WARN         = int(_CFG.get('alert_disk_warn',         80))
ALERT_DISK_CRIT         = int(_CFG.get('alert_disk_crit',         90))
ALERT_CPU_WARN          = int(_CFG.get('alert_cpu_warn',          80))
WATCHED_SERVICES        = list(_CFG.get('watched_services',       []))
FS_ROOT                 = str(_CFG.get('fs_root',                 str(Path.home())))
SESSION_TIMEOUT_MINUTES = int(_CFG.get('session_timeout_minutes', 7))
AUTH_MODE               = _CFG.get('auth_mode', 'pam' if PLATFORM == 'Linux' else 'password')
_PASSWORD_HASH          = _CFG.get('password_hash', '')

SCRIPTS_FILE     = Path(__file__).parent / 'scripts.scr'
SCRIPTS_FILE_OLD = Path(__file__).parent / 'scripts.json'
DASHIGNORE_FILE  = Path(__file__).parent / '.dashignore'

ALWAYS_HIDDEN = {
    'dashboard.py', 'scripts.scr', 'scripts.json',
    'install_service.sh', 'setup.py', 'uninstall.py',
    'launch_dashboard.vbs', 'config.json', '.dashignore',
}

BINARY_EXTS = {
    'jpg','jpeg','png','gif','webp','bmp','tiff','tif','ico','heic','heif','avif','raw','cr2','nef',
    'mp3','wav','flac','aac','ogg','m4a','wma','opus','aiff','alac',
    'mp4','mkv','avi','mov','wmv','flv','webm','m4v','mpeg','mpg','3gp',
    'zip','tar','gz','bz2','xz','7z','rar','zst','lz4',
    'exe','bin','so','o','a','dll','dylib','elf','class','pyc','pyo','wasm',
    'pdf','docx','xlsx','pptx','doc','xls','ppt','odt','ods','odp',
    'ttf','otf','woff','woff2','eot','db','sqlite','sqlite3','mdb','pkl','npy','npz',
}


# ═══════════════════════════════════════════════════════
#  PLATFORM-SPECIFIC QUICK COMMANDS
# ═══════════════════════════════════════════════════════
if PLATFORM == 'Darwin':
    QUICK_COMMANDS = {
        "System": [
            {"name": "CPU & Memory",      "cmd": "top -l 1 -n 0 -s 0 | head -20"},
            {"name": "Disk Usage",        "cmd": "df -h"},
            {"name": "Uptime",            "cmd": "uptime"},
            {"name": "IP Addresses",      "cmd": "ifconfig | grep -E 'inet |^[a-z]'"},
            {"name": "System Profile",    "cmd": "system_profiler SPHardwareDataType SPSoftwareDataType | head -40"},
            {"name": "Top Processes",     "cmd": "ps aux -r | head -14"},
            {"name": "Kernel Info",       "cmd": "uname -a"},
            {"name": "Battery",           "cmd": "pmset -g batt 2>/dev/null || echo 'No battery'"},
        ],
        "Network": [
            {"name": "Full ifconfig",     "cmd": "ifconfig"},
            {"name": "Open Ports",        "cmd": "lsof -i -P -n | grep LISTEN"},
            {"name": "Ping Google",       "cmd": "ping -c 4 8.8.8.8"},
            {"name": "Route Table",       "cmd": "netstat -rn"},
            {"name": "ARP Table",         "cmd": "arp -a"},
            {"name": "Established",       "cmd": "netstat -an | grep ESTABLISHED | head -20"},
        ],
        "Services": [
            {"name": "Launch Agents",     "cmd": "launchctl list | grep -v com.apple | head -30"},
            {"name": "Brew Services",     "cmd": "brew services list 2>/dev/null || echo 'Homebrew not found'"},
            {"name": "Running Processes", "cmd": "ps aux | grep -v grep | head -20"},
            {"name": "Cron Jobs",         "cmd": "crontab -l 2>/dev/null || echo 'No crontab for current user'"},
        ],
        "Hardware": [
            {"name": "USB Devices",       "cmd": "system_profiler SPUSBDataType 2>/dev/null | head -50"},
            {"name": "Bluetooth",         "cmd": "system_profiler SPBluetoothDataType 2>/dev/null | head -20"},
            {"name": "Disks",             "cmd": "diskutil list"},
            {"name": "GPU Info",          "cmd": "system_profiler SPDisplaysDataType 2>/dev/null | head -20"},
        ],
    }
    APPS = [
        {"name": "Safari",   "icon": "SF", "color": "#0076d6", "cmd": "open -a Safari"},
        {"name": "Finder",   "icon": "FN", "color": "#1ca554", "cmd": "open ~"},
        {"name": "Terminal", "icon": "TM", "color": "#2b3a52", "cmd": "open -a Terminal"},
        {"name": "VS Code",  "icon": "VS", "color": "#0078d4", "cmd": "code . 2>/dev/null || open -a 'Visual Studio Code'"},
        {"name": "Activity", "icon": "AC", "color": "#6246ea", "cmd": "open -a 'Activity Monitor'"},
        {"name": "Restart",  "icon": "RB", "color": "#b07800", "cmd": "osascript -e 'tell application \"System Events\" to restart'"},
        {"name": "Shutdown", "icon": "SD", "color": "#b03030", "cmd": "osascript -e 'tell application \"System Events\" to shut down'"},
    ]

elif PLATFORM == 'Windows':
    QUICK_COMMANDS = {
        "System": [
            {"name": "CPU & Memory",      "cmd": "powershell -NoProfile -Command \"$os=(Get-WmiObject Win32_OperatingSystem);$cpu=(Get-WmiObject Win32_Processor|Select -First 1);Write-Host('CPU: '+$cpu.Name);Write-Host('Load: '+$cpu.LoadPercentage+'%');Write-Host('RAM total: '+[math]::Round($os.TotalVisibleMemorySize/1024)+' MB');Write-Host('RAM free: '+[math]::Round($os.FreePhysicalMemory/1024)+' MB')\""},
            {"name": "Disk Usage",        "cmd": "wmic logicaldisk get caption,size,freespace,filesystem"},
            {"name": "Uptime",            "cmd": "powershell -NoProfile -Command \"(Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime\""},
            {"name": "IP Addresses",      "cmd": "ipconfig"},
            {"name": "System Info",       "cmd": "systeminfo"},
            {"name": "Top Processes",     "cmd": "tasklist /v /fo table"},
            {"name": "Environment",       "cmd": "set"},
        ],
        "Network": [
            {"name": "Full ipconfig",     "cmd": "ipconfig /all"},
            {"name": "Active Ports",      "cmd": "netstat -an | findstr LISTENING"},
            {"name": "Ping Google",       "cmd": "ping -n 4 8.8.8.8"},
            {"name": "Route Table",       "cmd": "route print"},
            {"name": "ARP Table",         "cmd": "arp -a"},
            {"name": "Established",       "cmd": "netstat -an | findstr ESTABLISHED"},
        ],
        "Services": [
            {"name": "Running Services",  "cmd": "sc query type= all state= running"},
            {"name": "Scheduled Tasks",   "cmd": "schtasks /query /fo LIST | findstr /C:\"TaskName\" /C:\"Status\""},
            {"name": "Startup Items",     "cmd": "wmic startup get caption,command"},
        ],
        "Hardware": [
            {"name": "USB Devices",       "cmd": "wmic path Win32_USBHub get DeviceID,Description"},
            {"name": "Disk Drives",       "cmd": "wmic diskdrive get name,size,model"},
            {"name": "GPU Info",          "cmd": "wmic path win32_videocontroller get name,AdapterRAM"},
            {"name": "Battery",           "cmd": "wmic path Win32_Battery get EstimatedChargeRemaining,BatteryStatus 2>nul || echo No battery detected"},
        ],
    }
    APPS = [
        {"name": "Chrome",   "icon": "CH", "color": "#0076d6", "cmd": "start chrome"},
        {"name": "Explorer", "icon": "EX", "color": "#1ca554", "cmd": "explorer ."},
        {"name": "Terminal", "icon": "PS", "color": "#2b3a52", "cmd": "start powershell"},
        {"name": "VS Code",  "icon": "VS", "color": "#0078d4", "cmd": "code ."},
        {"name": "Task Mgr", "icon": "TK", "color": "#6246ea", "cmd": "taskmgr"},
        {"name": "Restart",  "icon": "RB", "color": "#b07800", "cmd": "shutdown /r /t 0"},
        {"name": "Shutdown", "icon": "SD", "color": "#b03030", "cmd": "shutdown /s /t 0"},
    ]

else:  # Linux (original set, unchanged)
    QUICK_COMMANDS = {
        "System": [
            {"name": "CPU Temp",        "cmd": "vcgencmd measure_temp 2>/dev/null || awk '{printf \"%.1f°C\", $1/1000}' /sys/class/thermal/thermal_zone0/temp"},
            {"name": "CPU & RAM",       "cmd": "echo '── CPU ──' && top -bn2 | grep 'Cpu(s)' | tail -1 && echo '' && echo '── Memory ──' && free -h"},
            {"name": "Disk Usage",      "cmd": "df -h | column -t"},
            {"name": "Uptime",          "cmd": "uptime -p && echo '' && uptime"},
            {"name": "IP Addresses",    "cmd": "hostname -I | tr ' ' '\\n' | grep -v '^$'"},
            {"name": "Pi Model",        "cmd": "cat /proc/device-tree/model 2>/dev/null && echo '' || uname -a"},
            {"name": "Top Processes",   "cmd": "ps aux --sort=-%cpu | head -14"},
            {"name": "Kernel Info",     "cmd": "uname -a"},
        ],
        "Network": [
            {"name": "Wifi Signal",     "cmd": "iwconfig wlan0 2>/dev/null | grep -E 'ESSID|Signal|Bit Rate' || echo 'No wlan0 interface found'"},
            {"name": "IP Details",      "cmd": "ip addr show | grep -E '^[0-9]|inet '"},
            {"name": "Open Ports",      "cmd": "ss -tulpn 2>/dev/null | grep LISTEN"},
            {"name": "Ping Google",     "cmd": "ping -c 4 8.8.8.8"},
            {"name": "Route Table",     "cmd": "ip route show"},
            {"name": "ARP Table",       "cmd": "arp -n 2>/dev/null || ip neigh show"},
        ],
        "Services": [
            {"name": "Running Services","cmd": "systemctl list-units --type=service --state=running --no-pager"},
            {"name": "Failed Services", "cmd": "systemctl --failed --no-pager"},
            {"name": "Recent Logs",     "cmd": "journalctl -n 40 --no-pager -q"},
            {"name": "Cron Jobs",       "cmd": "crontab -l 2>/dev/null || echo 'No crontab for current user'"},
        ],
        "Hardware": [
            {"name": "USB Devices",     "cmd": "lsusb"},
            {"name": "I2C Scan",        "cmd": "i2cdetect -y 1 2>/dev/null || echo 'Install: sudo apt install i2c-tools'"},
            {"name": "Camera",          "cmd": "libcamera-hello --list-cameras 2>/dev/null || vcgencmd get_camera 2>/dev/null || echo 'No camera tool found'"},
            {"name": "GPIO Pinout",     "cmd": "pinout 2>/dev/null | head -35 || echo 'Install: sudo apt install python3-gpiozero'"},
            {"name": "Throttle Status", "cmd": "vcgencmd get_throttled 2>/dev/null || echo 'vcgencmd not available'"},
            {"name": "Voltages",        "cmd": "for v in core sdram_c sdram_i sdram_p; do printf '%-12s: ' $v; vcgencmd measure_volts $v 2>/dev/null || echo n/a; done"},
        ],
    }
    APPS = [
        {"name": "Chromium",  "icon": "CH", "color": "#0076d6", "cmd": "DISPLAY=:0 chromium-browser --new-window &"},
        {"name": "Files",     "icon": "FM", "color": "#1ca554", "cmd": "DISPLAY=:0 pcmanfm &"},
        {"name": "Terminal",  "icon": "TM", "color": "#2b3a52", "cmd": "DISPLAY=:0 lxterminal &"},
        {"name": "VS Code",   "icon": "VS", "color": "#0078d4", "cmd": "DISPLAY=:0 code &"},
        {"name": "VLC",       "icon": "VL", "color": "#e86a17", "cmd": "DISPLAY=:0 vlc &"},
        {"name": "Calc",      "icon": "CA", "color": "#6246ea", "cmd": "DISPLAY=:0 galculator &"},
        {"name": "Reboot",    "icon": "RB", "color": "#b07800", "cmd": "sudo -n reboot"},
        {"name": "Shutdown",  "icon": "SD", "color": "#b03030", "cmd": "sudo -n shutdown -h now"},
    ]


# ═══════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════
def pam_auth(username: str, password: str):
    try:
        import pam
        p = pam.pam()
        return p.authenticate(username, password, service='login')
    except ImportError:
        return None
    except Exception:
        return False

def verify_password(password: str, stored: str) -> bool:
    """Verify a scrypt or pbkdf2 hash created by setup.py."""
    if not stored:
        return False
    try:
        method, salt, hashed = stored.split('$', 2)
        if method == 'scrypt':
            dk = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
        else:
            dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260_000)
        return dk.hex() == hashed
    except Exception:
        return False

def logged_in() -> bool:
    return session.get('authenticated') is True

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not logged_in():
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapper


# ═══════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════
ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
def strip_ansi(t: str) -> str:
    return ANSI.sub('', t)

def sh(cmd: str, timeout: int = 5) -> str:
    try:
        return subprocess.check_output(
            cmd, shell=True, text=True,
            timeout=timeout, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ''

# ── Cross-platform stat functions ────────────────────────

def get_cpu_pct() -> str:
    if _HAS_PSUTIL:
        try:
            return f"{_psutil.cpu_percent(interval=0.3):.0f}%"
        except Exception:
            pass
    # Linux /proc/stat fallback
    try:
        def _read():
            with open('/proc/stat') as f:
                cols = f.readline().split()[1:]
            c = list(map(int, cols))
            return sum(c), c[3] + c[4]
        t1, i1 = _read(); time.sleep(0.25); t2, i2 = _read()
        dt = t2 - t1
        return f"{round((dt - (i2 - i1)) / dt * 100)}%" if dt else '—'
    except Exception:
        return '—'

def get_ram_pct() -> str:
    if _HAS_PSUTIL:
        try:
            return f"{_psutil.virtual_memory().percent:.0f}%"
        except Exception:
            pass
    try:
        for line in sh('free').splitlines():
            if line.startswith('Mem:'):
                parts = line.split()
                total, used = int(parts[1]), int(parts[2])
                return f"{round(used/total*100)}%" if total else '—'
    except Exception:
        pass
    return '—'

def get_disk_pct() -> str:
    if _HAS_PSUTIL:
        try:
            root = 'C:\\' if PLATFORM == 'Windows' else '/'
            return f"{_psutil.disk_usage(root).percent:.0f}%"
        except Exception:
            pass
    try:
        lines = sh('df /').splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            return parts[4] if len(parts) >= 5 else '—'
    except Exception:
        pass
    return '—'

def get_temp() -> str:
    if _HAS_PSUTIL:
        try:
            temps = _psutil.sensors_temperatures()
            if temps:
                for key in ('coretemp', 'cpu_thermal', 'cpu-thermal', 'acpitz', 'k10temp', 'zenpower'):
                    if key in temps and temps[key]:
                        return f"{temps[key][0].current:.1f}°C"
                for entries in temps.values():
                    if entries:
                        return f"{entries[0].current:.1f}°C"
        except Exception:
            pass
    try:
        raw = Path('/sys/class/thermal/thermal_zone0/temp').read_text()
        return f"{int(raw)/1000:.1f}°C"
    except Exception:
        pass
    try:
        return sh('vcgencmd measure_temp 2>/dev/null').replace('temp=', '') or '—'
    except Exception:
        pass
    return '—'

# ── File system helpers ──────────────────────────────────

def safe_path(raw: str):
    try:
        p = Path(FS_ROOT).joinpath(raw.lstrip('/')).resolve()
        p.relative_to(Path(FS_ROOT).resolve())
        return p
    except Exception:
        return None

def fmt_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024: return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

# ── .dashignore ──────────────────────────────────────────
_ignore_patterns: list = []

def load_dashignore():
    global _ignore_patterns
    _ignore_patterns = []
    if not DASHIGNORE_FILE.exists():
        return
    for raw in DASHIGNORE_FILE.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line and not line.startswith('#'):
            _ignore_patterns.append(line)

def is_hidden(name: str, rel_path: str) -> bool:
    if name in ALWAYS_HIDDEN:
        return True
    for pat in _ignore_patterns:
        if '/' in pat:
            if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(rel_path, pat.rstrip('/') + '/*'):
                return True
        else:
            if fnmatch.fnmatch(name, pat):
                return True
    return False

# ── Scripts ──────────────────────────────────────────────

def load_scripts() -> list:
    if not SCRIPTS_FILE.exists() and SCRIPTS_FILE_OLD.exists():
        try:
            old = json.loads(SCRIPTS_FILE_OLD.read_text())
            save_scripts(old)
            SCRIPTS_FILE_OLD.unlink()
        except Exception:
            pass
    try:
        if not SCRIPTS_FILE.exists():
            return []
        scripts = []
        for block in SCRIPTS_FILE.read_text(encoding='utf-8').split('\n---\n'):
            block = block.strip()
            if not block:
                continue
            meta, cmd_lines = {}, []
            for line in block.splitlines():
                if line.startswith('@'):
                    k, _, v = line[1:].partition(':')
                    meta[k.strip().lower()] = v.strip()
                else:
                    cmd_lines.append(line)
            cmd = '\n'.join(cmd_lines).strip()
            if meta.get('name') and cmd:
                scripts.append({
                    'id':    meta.get('id', str(uuid.uuid4())[:8]),
                    'name':  meta['name'],
                    'desc':  meta.get('desc', ''),
                    'color': meta.get('color', '#4a607a'),
                    'cmd':   cmd,
                })
        return scripts
    except Exception:
        return []

def save_scripts(scripts: list):
    blocks = []
    for s in scripts:
        header = (f"@name:  {s['name']}\n"
                  f"@id:    {s.get('id', str(uuid.uuid4())[:8])}\n"
                  f"@desc:  {s.get('desc','')}\n"
                  f"@color: {s.get('color','#4a607a')}")
        blocks.append(header + '\n' + s['cmd'])
    SCRIPTS_FILE.write_text('\n---\n'.join(blocks) + '\n', encoding='utf-8')


# ═══════════════════════════════════════════════════════
#  ROUTES — auth / meta
# ═══════════════════════════════════════════════════════
@app.route('/')
def index():
    return HTML_PAGE

@app.route('/api/auth-mode')
def auth_mode_route():
    """Public — tells the login UI whether to show the username field."""
    return jsonify({'mode': AUTH_MODE})

@app.route('/api/login', methods=['POST'])
def login():
    data     = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not password:
        return jsonify({'ok': False, 'error': 'Password is required.'}), 400

    if AUTH_MODE == 'pam':
        if not username:
            return jsonify({'ok': False, 'error': 'Username and password required.'}), 400
        result = pam_auth(username, password)
        if result is None:
            return jsonify({'ok': False,
                'error': 'python-pam not installed.\nRun: pip3 install python-pam --break-system-packages'}), 500
        if result:
            session['authenticated'] = True
            session['username'] = username
            return jsonify({'ok': True, 'username': username})
        return jsonify({'ok': False, 'error': 'Incorrect username or password.'}), 401
    else:
        # password-hash mode (macOS / Windows)
        if not _PASSWORD_HASH:
            return jsonify({'ok': False,
                'error': 'No password configured. Run setup.py first.'}), 500
        if verify_password(password, _PASSWORD_HASH):
            session['authenticated'] = True
            session['username'] = username or 'admin'
            return jsonify({'ok': True, 'username': username or 'admin'})
        return jsonify({'ok': False, 'error': 'Incorrect password.'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/whoami')
def whoami():
    if logged_in():
        return jsonify({'ok': True, 'username': session.get('username', '')})
    return jsonify({'ok': False}), 401

@app.route('/api/config')
@require_auth
def cfg():
    return jsonify({
        'commands':   QUICK_COMMANDS,
        'apps':       APPS,
        'timeout_ms': SESSION_TIMEOUT_MINUTES * 60 * 1000,
        'platform':   PLATFORM,
        'auth_mode':  AUTH_MODE,
    })

@app.route('/api/run', methods=['POST'])
@require_auth
def run_cmd():
    cmd = (request.json or {}).get('cmd', '').strip()
    if not cmd:
        return jsonify({'error': 'No command'}), 400
    # On Linux, make bare sudo calls non-interactive so they fail fast rather than hang
    safe_cmd = re.sub(r'\bsudo\b(?!\s+-)', 'sudo -n', cmd) if PLATFORM == 'Linux' else cmd
    try:
        r = subprocess.run(
            safe_cmd, shell=True, capture_output=True, text=True,
            timeout=30, env={**os.environ, 'TERM': 'xterm-256color'}
        )
        return jsonify({'stdout': strip_ansi(r.stdout), 'stderr': strip_ansi(r.stderr),
                        'returncode': r.returncode, 'cmd': cmd})
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Timed out (30 s limit)', 'returncode': -1})
    except Exception as e:
        return jsonify({'error': str(e), 'returncode': -1})

@app.route('/api/status')
@require_auth
def status():
    return jsonify({
        'temp': get_temp(),
        'cpu':  get_cpu_pct(),
        'ram':  get_ram_pct(),
        'disk': get_disk_pct(),
    })

@app.route('/api/alerts')
@require_auth
def alerts():
    items = []
    try:
        t = float(get_temp().replace('°C', ''))
        if   t >= ALERT_TEMP_CRIT: items.append({'level':'crit','icon':'T','msg':f'CPU temp critical: {t:.1f}°C'})
        elif t >= ALERT_TEMP_WARN: items.append({'level':'warn','icon':'T','msg':f'CPU temp high: {t:.1f}°C'})
    except Exception:
        pass
    try:
        d = int(get_disk_pct().replace('%', ''))
        if   d >= ALERT_DISK_CRIT: items.append({'level':'crit','icon':'D','msg':f'Disk critically full: {d}%'})
        elif d >= ALERT_DISK_WARN: items.append({'level':'warn','icon':'D','msg':f'Disk space low: {d}% used'})
    except Exception:
        pass
    try:
        c = int(get_cpu_pct().replace('%', ''))
        if c >= ALERT_CPU_WARN: items.append({'level':'warn','icon':'C','msg':f'CPU usage high: {c}%'})
    except Exception:
        pass
    if PLATFORM == 'Linux':
        for svc in WATCHED_SERVICES:
            state = sh(f'systemctl is-active {svc} 2>/dev/null')
            if state != 'active':
                items.append({'level':'crit','icon':'S','msg':f'Service down: {svc} ({state or "unknown"})'})
    return jsonify({'alerts': items})


# ═══════════════════════════════════════════════════════
#  ROUTES — Ports (cross-platform)
# ═══════════════════════════════════════════════════════
@app.route('/api/ports')
@require_auth
def ports():
    if PLATFORM == 'Linux':
        return _ports_ss()
    return _ports_psutil()

def _ports_ss():
    raw  = sh('ss -tulpn 2>/dev/null', timeout=8)
    rows = []
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0]
        state = parts[1] if 'tcp' in proto.lower() else '—'
        local = parts[4]
        m = re.search(r':(\d+)$', local)
        if not m:
            continue
        proc = ''
        if len(parts) >= 6:
            pm = re.search(r'"([^"]+)"', parts[-1])
            if pm: proc = pm.group(1)
        rows.append({'proto': 'UDP' if 'udp' in proto.lower() else 'TCP',
                     'port': int(m.group(1)), 'state': state,
                     'local': local, 'proc': proc})
    rows.sort(key=lambda r: r['port'])
    return jsonify({'ports': rows})

def _ports_psutil():
    import socket as _sock
    rows, seen = [], set()
    if not _HAS_PSUTIL:
        return jsonify({'ports': [], 'error': 'psutil not installed'})
    try:
        for c in _psutil.net_connections(kind='inet'):
            if not c.laddr:
                continue
            listening = (
                (c.type == _sock.SOCK_STREAM and c.status == _psutil.CONN_LISTEN) or
                (c.type == _sock.SOCK_DGRAM)
            )
            if not listening:
                continue
            port  = c.laddr.port
            proto = 'UDP' if c.type == _sock.SOCK_DGRAM else 'TCP'
            key   = (proto, port)
            if key in seen:
                continue
            seen.add(key)
            proc = ''
            try:
                if c.pid:
                    proc = _psutil.Process(c.pid).name()
            except (_psutil.NoSuchProcess, _psutil.AccessDenied, Exception):
                pass
            rows.append({'proto': proto, 'port': port,
                         'state': getattr(c, 'status', '—') or '—',
                         'local': f'{c.laddr.ip}:{port}', 'proc': proc})
    except Exception:
        pass
    rows.sort(key=lambda r: r['port'])
    return jsonify({'ports': rows})


# ═══════════════════════════════════════════════════════
#  ROUTES — Docker
# ═══════════════════════════════════════════════════════
@app.route('/api/docker')
@require_auth
def docker_list():
    if not sh('command -v docker' if PLATFORM != 'Windows' else 'where docker'):
        return jsonify({'available': False, 'containers': []})
    fmt = '{"id":"{{.ID}}","name":"{{.Names}}","image":"{{.Image}}","status":"{{.Status}}","state":"{{.State}}"}'
    raw = sh(f"docker ps -a --format '{fmt}'", timeout=8)
    containers = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            try: containers.append(json.loads(line))
            except Exception: pass
    return jsonify({'available': True, 'containers': containers})

@app.route('/api/docker/action', methods=['POST'])
@require_auth
def docker_action():
    data   = request.json or {}
    action = data.get('action', '')
    cid    = data.get('id', '').strip()
    if action not in ('start', 'stop', 'restart') or not cid:
        return jsonify({'ok': False, 'error': 'Invalid action or id'}), 400
    if not re.match(r'^[a-f0-9A-F]{1,64}$', cid):
        return jsonify({'ok': False, 'error': 'Invalid container id'}), 400
    r = subprocess.run(f'docker {action} {cid}', shell=True,
                       capture_output=True, text=True, timeout=15)
    return jsonify({'ok': r.returncode == 0, 'stderr': r.stderr.strip()})

@app.route('/api/docker/logs')
@require_auth
def docker_logs():
    name = request.args.get('name', '').strip()
    if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
        return jsonify({'error': 'Invalid container name'}), 400
    out = sh(f'docker logs --tail 50 {name} 2>&1', timeout=10)
    return jsonify({'ok': True, 'logs': out})


# ═══════════════════════════════════════════════════════
#  ROUTES — File System
# ═══════════════════════════════════════════════════════
@app.route('/api/fs/list')
@require_auth
def fs_list():
    rel = request.args.get('path', '')
    p   = safe_path(rel)
    if p is None or not p.exists() or not p.is_dir():
        return jsonify({'error': 'Invalid or inaccessible path'}), 400
    entries = []
    try:
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        for item in items:
            try:   item_rel = str(item.relative_to(Path(FS_ROOT).resolve()))
            except Exception: item_rel = item.name
            if is_hidden(item.name, item_rel):
                continue
            try:
                st  = item.stat()
                ext = item.suffix.lstrip('.').lower()
                entries.append({
                    'name':     item.name,
                    'type':     'dir' if item.is_dir() else 'file',
                    'size':     fmt_size(st.st_size) if item.is_file() else '',
                    'size_b':   st.st_size if item.is_file() else 0,
                    'modified': datetime.fromtimestamp(st.st_mtime).strftime('%b %d %H:%M'),
                    'editable': item.is_file() and ext not in BINARY_EXTS and st.st_size <= 512*1024,
                })
            except PermissionError:
                entries.append({'name': item.name, 'type': 'dir' if item.is_dir() else 'file',
                                 'size': '', 'size_b': 0, 'modified': '—', 'editable': False})
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403

    try:   rel_parts = p.relative_to(Path(FS_ROOT).resolve()).parts
    except Exception: rel_parts = ()

    return jsonify({
        'path':    str(p),
        'rel':     '/'.join(rel_parts),
        'crumbs':  list(rel_parts),
        'parent':  str(p.parent.relative_to(Path(FS_ROOT).resolve())) if p != Path(FS_ROOT).resolve() else None,
        'entries': entries,
    })

@app.route('/api/fs/download')
@require_auth
def fs_download():
    p = safe_path(request.args.get('path', ''))
    if p is None or not p.is_file():
        return jsonify({'error': 'File not found'}), 404
    mime = mimetypes.guess_type(str(p))[0] or 'application/octet-stream'
    return send_file(str(p), mimetype=mime, as_attachment=True, download_name=p.name)

@app.route('/api/fs/upload', methods=['POST'])
@require_auth
def fs_upload():
    p = safe_path(request.form.get('path', ''))
    if p is None or not p.is_dir():
        return jsonify({'ok': False, 'error': 'Invalid directory'}), 400
    uploaded = []
    for f in request.files.getlist('files'):
        if not f.filename: continue
        fname = Path(f.filename).name
        f.save(str(p / fname))
        uploaded.append(fname)
    return jsonify({'ok': True, 'uploaded': uploaded})

@app.route('/api/fs/delete', methods=['POST'])
@require_auth
def fs_delete():
    p = safe_path((request.json or {}).get('path', ''))
    if p is None or not p.exists():
        return jsonify({'ok': False, 'error': 'Path not found'}), 404
    if p == Path(FS_ROOT).resolve():
        return jsonify({'ok': False, 'error': 'Cannot delete root'}), 400
    try:
        shutil.rmtree(str(p)) if p.is_dir() else p.unlink()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/fs/mkdir', methods=['POST'])
@require_auth
def fs_mkdir():
    data = request.json or {}
    p    = safe_path(data.get('path', ''))
    name = data.get('name', '').strip()
    if not name or '/' in name or '\\' in name or p is None or not p.is_dir():
        return jsonify({'ok': False, 'error': 'Invalid'}), 400
    try:
        (p / name).mkdir(exist_ok=True)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/fs/rename', methods=['POST'])
@require_auth
def fs_rename():
    data     = request.json or {}
    new_name = data.get('name', '').strip()
    if not new_name or '/' in new_name or '\\' in new_name or new_name in ('.', '..'):
        return jsonify({'ok': False, 'error': 'Invalid name'}), 400
    p = safe_path(data.get('path', ''))
    if p is None or not p.exists() or p == Path(FS_ROOT).resolve():
        return jsonify({'ok': False, 'error': 'Invalid path'}), 400
    try:
        p.rename(p.parent / new_name)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/fs/duplicate', methods=['POST'])
@require_auth
def fs_duplicate():
    p = safe_path((request.json or {}).get('path', ''))
    if p is None or not p.is_file():
        return jsonify({'ok': False, 'error': 'File not found'}), 404
    stem, suffix = p.stem, p.suffix
    dest = p.parent / f"{stem}_copy{suffix}"
    c = 1
    while dest.exists():
        dest = p.parent / f"{stem}_copy{c}{suffix}"
        c += 1
    try:
        shutil.copy2(str(p), str(dest))
        return jsonify({'ok': True, 'name': dest.name})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/fs/read')
@require_auth
def fs_read():
    p = safe_path(request.args.get('path', ''))
    if p is None or not p.is_file():
        return jsonify({'error': 'File not found'}), 404
    if p.stat().st_size > 512 * 1024:
        return jsonify({'error': 'File too large (max 512 KB)'}), 400
    try:
        return jsonify({'ok': True, 'content': p.read_text(errors='replace'), 'name': p.name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/fs/write', methods=['POST'])
@require_auth
def fs_write():
    data = request.json or {}
    p    = safe_path(data.get('path', ''))
    if p is None or not p.parent.exists():
        return jsonify({'ok': False, 'error': 'Invalid path'}), 400
    try:
        p.write_text(data.get('content', ''))
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════
#  ROUTES — Scripts
# ═══════════════════════════════════════════════════════
@app.route('/api/scripts', methods=['GET'])
@require_auth
def scripts_list():
    return jsonify({'scripts': load_scripts()})

@app.route('/api/scripts', methods=['POST'])
@require_auth
def scripts_create():
    data = request.json or {}
    name = data.get('name', '').strip()
    cmd  = data.get('cmd',  '').strip()
    if not name or not cmd:
        return jsonify({'ok': False, 'error': 'Name and command required'}), 400
    scripts = load_scripts()
    s = {'id': str(uuid.uuid4())[:8], 'name': name,
         'desc': data.get('desc', '').strip(), 'cmd': cmd,
         'color': data.get('color', '#4a607a')}
    scripts.append(s)
    save_scripts(scripts)
    return jsonify({'ok': True, 'script': s})

@app.route('/api/scripts/<sid>', methods=['PUT'])
@require_auth
def scripts_update(sid):
    data    = request.json or {}
    scripts = load_scripts()
    for s in scripts:
        if s['id'] == sid:
            s['name']  = data.get('name',  s['name']).strip()
            s['desc']  = data.get('desc',  s.get('desc', '')).strip()
            s['cmd']   = data.get('cmd',   s['cmd']).strip()
            s['color'] = data.get('color', s.get('color', '#4a607a'))
            save_scripts(scripts)
            return jsonify({'ok': True, 'script': s})
    return jsonify({'ok': False, 'error': 'Script not found'}), 404

@app.route('/api/scripts/<sid>', methods=['DELETE'])
@require_auth
def scripts_delete(sid):
    scripts = load_scripts()
    new     = [s for s in scripts if s['id'] != sid]
    if len(new) == len(scripts):
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    save_scripts(new)
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════
#  FRONTEND  (identical theme + layout from v9, login
#  updated to hide the username field in password mode)
# ═══════════════════════════════════════════════════════
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en" data-theme="terminal">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DevBoard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Syne:wght@400;600;700;800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
[data-theme="terminal"]{--bg:#080b10;--bg2:#0d1118;--bg3:#131a24;--bg4:#19222f;--bdr:#1e2a3a;--bdr2:#2a3a52;--acc:#00e87a;--hi:#3d9eff;--red:#ff4d6a;--warn:#ffb300;--txt:#c4d4ec;--txt2:#fff;--dim:#4a607a;--dim2:#2e3f56;--out:#9ab8d8;--font:'Syne',sans-serif;--mono:'JetBrains Mono',monospace;--r:10px;--rs:8px}
[data-theme="pi"]{--bg:#f0f0f2;--bg2:#fff;--bg3:#e8e8ec;--bg4:#dcdce2;--bdr:#d0d0d8;--bdr2:#b8b8c4;--acc:#c51a4a;--hi:#0071e3;--red:#d00025;--warn:#c47a00;--txt:#1d1d1f;--txt2:#000;--dim:#6e6e7a;--dim2:#aeaeb8;--out:#2c2c2e;--font:'Inter',sans-serif;--mono:'JetBrains Mono',monospace;--r:10px;--rs:8px}
[data-theme="matrix"]{--bg:#000;--bg2:#030c03;--bg3:#061206;--bg4:#0a1a0a;--bdr:#0c2a0c;--bdr2:#134013;--acc:#00ff41;--hi:#00dd33;--red:#ff0040;--warn:#aaff00;--txt:#00cc33;--txt2:#00ff41;--dim:#1a5c1a;--dim2:#0d350d;--out:#00ff41;--font:'JetBrains Mono',monospace;--mono:'JetBrains Mono',monospace;--r:4px;--rs:2px}
[data-theme="forest"]{--bg:#0c1410;--bg2:#111a14;--bg3:#172018;--bg4:#1d2a1e;--bdr:#253428;--bdr2:#2e4232;--acc:#74c97e;--hi:#82c9a0;--red:#e07878;--warn:#d4a84b;--txt:#b8d4bc;--txt2:#e0f0e2;--dim:#4a6b52;--dim2:#2e4838;--out:#a0c8a8;--font:'Syne',sans-serif;--mono:'JetBrains Mono',monospace;--r:12px;--rs:8px}
[data-theme="blue"]{--bg:#03060f;--bg2:#060d1c;--bg3:#0a1428;--bg4:#0f1e38;--bdr:#152640;--bdr2:#1e3858;--acc:#38bdf8;--hi:#7dd3fc;--red:#f87171;--warn:#fbbf24;--txt:#bae6fd;--txt2:#e0f4ff;--dim:#2a5478;--dim2:#183650;--out:#93c5fd;--font:'Syne',sans-serif;--mono:'JetBrains Mono',monospace;--r:10px;--rs:8px}
[data-theme="frost"]{--bg:#eaf2fb;--bg2:#f5f9ff;--bg3:#ddeaf8;--bg4:#cdddf2;--bdr:#b8d0ea;--bdr2:#8ab4d8;--acc:#1565c0;--hi:#0277bd;--red:#c62828;--warn:#e65100;--txt:#1a2744;--txt2:#0d1929;--dim:#4d6e8a;--dim2:#8ab0cc;--out:#1e3a52;--font:'Inter',sans-serif;--mono:'JetBrains Mono',monospace;--r:10px;--rs:8px}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--txt);font-family:var(--font)}
#login-ov{position:fixed;inset:0;z-index:999;background:var(--bg);display:flex;align-items:center;justify-content:center}
#login-ov.hide{display:none}
.lcard{background:var(--bg2);border:1px solid var(--bdr);border-radius:20px;padding:44px 48px 36px;width:min(400px,94vw)}
.llogo{font-family:var(--mono);font-size:12px;color:var(--dim);margin-bottom:22px;display:flex;align-items:center;gap:8px}
.llogo::before{content:'';display:block;width:7px;height:7px;border-radius:50%;background:var(--acc);flex-shrink:0}
.ltitle{font-size:26px;font-weight:800;color:var(--txt2);margin-bottom:5px}
.lsub{font-size:13px;color:var(--dim);margin-bottom:22px;line-height:1.6}
.lthemes{display:flex;align-items:center;gap:7px;margin-bottom:26px}
.lthemes-lbl{font-size:11px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.field{margin-bottom:13px}
.field label{display:block;font-size:11px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
.field input{width:100%;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);color:var(--txt);font-family:var(--mono);font-size:14px;padding:11px 14px;outline:none;transition:border-color .2s;resize:vertical}
.field input:focus{border-color:var(--acc)}
.lbtn{width:100%;margin-top:8px;padding:13px;background:var(--acc);color:var(--bg2);border:none;border-radius:var(--r);font-family:var(--font);font-weight:700;font-size:15px;cursor:pointer;transition:all .15s}
.lbtn:hover{filter:brightness(1.1)}.lbtn:active{transform:scale(.98)}
.lbtn:disabled{background:var(--dim2);cursor:not-allowed;color:var(--dim)}
.lerr{color:var(--red);font-size:12px;font-family:var(--mono);margin-top:11px;min-height:18px;white-space:pre-line;line-height:1.5}
.lnote{margin-top:16px;padding-top:16px;border-top:1px solid var(--bdr);font-size:11px;color:var(--dim);line-height:1.7}
.lnote code{font-family:var(--mono);font-size:10px;color:var(--acc);background:var(--bg3);padding:1px 6px;border-radius:4px}
.sw{width:14px;height:14px;border-radius:50%;cursor:pointer;border:2px solid transparent;transition:all .15s;flex-shrink:0}
.sw:hover{transform:scale(1.25)}.sw.on{border-color:var(--txt2);transform:scale(1.2)}
#ctx-menu{position:fixed;z-index:1500;background:var(--bg2);border:1px solid var(--bdr2);border-radius:var(--r);padding:4px;min-width:170px;box-shadow:0 8px 24px rgba(0,0,0,.25);display:none}
#ctx-menu.show{display:block;animation:ctx-in .12s ease}
@keyframes ctx-in{from{opacity:0;transform:scale(.95)}to{opacity:1;transform:scale(1)}}
.ctx-item{display:flex;align-items:center;gap:9px;padding:8px 12px;border-radius:7px;cursor:pointer;font-size:13px;font-weight:600;color:var(--txt);transition:background .1s;border:none;background:none;width:100%;text-align:left;font-family:var(--font)}
.ctx-item:hover{background:var(--bg3);color:var(--txt2)}
.ctx-item.danger:hover{background:color-mix(in srgb,var(--red) 12%,transparent);color:var(--red)}
.ctx-item.accent:hover{background:color-mix(in srgb,var(--acc) 10%,transparent);color:var(--acc)}
.ctx-sep{height:1px;background:var(--bdr);margin:3px 4px}
.ctx-ico{font-size:12px;font-family:var(--mono);font-weight:700;color:var(--dim);width:16px;text-align:center;flex-shrink:0;line-height:1}
.modal-ov{position:fixed;inset:0;z-index:1600;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center}
.modal-ov.show{display:flex;animation:ctx-in .15s ease}
.modal-card{background:var(--bg2);border:1px solid var(--bdr);border-radius:16px;padding:28px 32px;width:min(420px,94vw)}
.modal-title{font-size:16px;font-weight:700;color:var(--txt2);margin-bottom:16px}
.modal-input{width:100%;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);color:var(--txt);font-family:var(--mono);font-size:14px;padding:10px 13px;outline:none;margin-bottom:12px;transition:border-color .2s}
.modal-input:focus{border-color:var(--acc)}
.modal-btns{display:flex;gap:8px;justify-content:flex-end;margin-top:4px}
.mbtn{padding:8px 20px;border-radius:var(--rs);font-family:var(--font);font-weight:700;font-size:13px;cursor:pointer;transition:all .15s;border:1px solid var(--bdr)}
.mbtn.cancel{background:var(--bg3);color:var(--dim)}.mbtn.cancel:hover{color:var(--txt)}
.mbtn.confirm{background:var(--acc);color:var(--bg);border-color:transparent}.mbtn.confirm:hover{filter:brightness(1.1)}
.mbtn.danger{background:color-mix(in srgb,var(--red) 15%,transparent);color:var(--red);border-color:color-mix(in srgb,var(--red) 30%,transparent)}
.mbtn.danger:hover{background:color-mix(in srgb,var(--red) 25%,transparent)}
#idle-modal{position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center}
#idle-modal.show{display:flex;animation:ctx-in .2s ease}
.idle-card{background:var(--bg2);border:2px solid var(--warn);border-radius:20px;padding:36px 40px;width:min(340px,92vw);text-align:center}
.idle-icon{width:44px;height:44px;border-radius:50%;background:color-mix(in srgb,var(--warn) 18%,transparent);border:2px solid var(--warn);color:var(--warn);font-size:20px;font-weight:700;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-family:var(--mono)}
.idle-title{font-size:16px;font-weight:700;color:var(--txt2);margin-bottom:6px}
.idle-sub{font-size:13px;color:var(--dim);margin-bottom:20px;line-height:1.55}
.idle-countdown{font-size:52px;font-weight:800;font-family:var(--mono);color:var(--warn);line-height:1;margin-bottom:22px}
.idle-btn{display:block;width:100%;padding:12px;background:color-mix(in srgb,var(--warn) 15%,transparent);border:1.5px solid var(--warn);color:var(--warn);border-radius:var(--r);font-family:var(--font);font-weight:700;font-size:14px;cursor:pointer;transition:all .15s}
.idle-btn:hover{background:color-mix(in srgb,var(--warn) 25%,transparent)}
.idle-hint{font-size:11px;color:var(--dim2);margin-top:10px}
#scr-modal .modal-card{width:min(520px,96vw)}
.scr-cmd-ta{width:100%;min-height:120px;max-height:300px;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);color:var(--txt);font-family:var(--mono);font-size:13px;padding:10px 13px;outline:none;margin-bottom:12px;transition:border-color .2s;resize:vertical;line-height:1.6;tab-size:2}
.scr-cmd-ta:focus{border-color:var(--acc)}
.color-row{display:flex;gap:7px;margin-bottom:14px;align-items:center}
.color-row label{font-size:11px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin-right:4px}
.cpick{width:20px;height:20px;border-radius:50%;cursor:pointer;border:2px solid transparent;transition:all .15s;flex-shrink:0}
.cpick:hover{transform:scale(1.2)}.cpick.on{border-color:var(--txt2);transform:scale(1.15)}
#edit-modal{position:fixed;inset:0;z-index:1600;background:rgba(0,0,0,.6);display:none;align-items:stretch;justify-content:center;padding:24px}
#edit-modal.show{display:flex;animation:ctx-in .15s ease}
.edit-card{background:var(--bg2);border:1px solid var(--bdr);border-radius:16px;display:flex;flex-direction:column;width:100%;max-width:900px;overflow:hidden}
.edit-hdr{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--bdr);flex-shrink:0}
.edit-filename{font-family:var(--mono);font-size:13px;color:var(--acc);flex:1;font-weight:700}
.edit-status{font-size:11px;color:var(--dim);font-family:var(--mono)}
.edit-close{background:none;border:none;color:var(--dim);cursor:pointer;font-size:20px;line-height:1;padding:0 4px;transition:color .15s}.edit-close:hover{color:var(--red)}
#edit-ta{flex:1;resize:none;background:var(--bg3);border:none;outline:none;color:var(--txt);font-family:var(--mono);font-size:13px;line-height:1.7;padding:16px 20px;tab-size:2}
.edit-footer{display:flex;align-items:center;gap:10px;padding:10px 18px;border-top:1px solid var(--bdr);flex-shrink:0}
.edit-save{background:var(--acc);color:var(--bg);border:none;border-radius:var(--rs);padding:8px 24px;font-family:var(--font);font-weight:700;font-size:13px;cursor:pointer;transition:all .15s}.edit-save:hover{filter:brightness(1.1)}
.edit-discard{background:var(--bg3);border:1px solid var(--bdr);color:var(--dim);border-radius:var(--rs);padding:8px 18px;font-family:var(--font);font-weight:700;font-size:13px;cursor:pointer;transition:all .15s}.edit-discard:hover{color:var(--txt)}
.edit-sp{flex:1}
#hist-search{position:absolute;bottom:calc(100% + 4px);left:0;right:0;background:var(--bg2);border:1px solid var(--bdr2);border-radius:var(--r);padding:4px;max-height:220px;overflow-y:auto;box-shadow:0 -4px 20px rgba(0,0,0,.3);display:none;z-index:100}
#hist-search.show{display:block;animation:ctx-in .1s ease}
.hist-item{padding:8px 12px;border-radius:7px;cursor:pointer;font-family:var(--mono);font-size:12px;color:var(--txt);transition:background .1s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hist-item:hover,.hist-item.hi{background:var(--bg3);color:var(--txt2)}
.hist-empty{padding:12px;font-size:12px;color:var(--dim);text-align:center;font-family:var(--mono)}
.ibar-wrap{position:relative;flex-shrink:0}
#app{position:fixed;inset:0;display:flex;flex-direction:column;overflow:hidden}
header{flex-shrink:0;height:48px;background:var(--bg2);border-bottom:1px solid var(--bdr);display:flex;align-items:center;gap:8px;padding:0 14px;overflow:hidden}
#alert-bar{flex-shrink:0;display:none;flex-direction:column}
#alert-bar.has-alerts{display:flex}
.logo{font-family:var(--mono);font-weight:700;font-size:14px;color:var(--acc);white-space:nowrap;flex-shrink:0}
.logo em{color:var(--dim);font-style:normal}
.logo-user{color:var(--hi)}
.sp{flex:1;min-width:0}
.chip{display:flex;align-items:center;gap:4px;background:var(--bg3);border:1px solid var(--bdr);border-radius:20px;padding:3px 10px;font-family:var(--mono);font-size:11px;color:var(--dim);white-space:nowrap;flex-shrink:0}
.chip b{color:var(--txt);font-weight:700}
.chip.hot b{color:var(--red)}.chip.warm b{color:var(--warn)}
.hbtn{background:none;border:1px solid var(--bdr);color:var(--dim);border-radius:var(--rs);padding:3px 9px;cursor:pointer;font-size:12px;font-family:var(--font);transition:all .15s;white-space:nowrap;flex-shrink:0}
.hbtn:hover{border-color:var(--acc);color:var(--acc)}
.hbtn.danger:hover{border-color:var(--red);color:var(--red)}
.theme-strip{display:flex;align-items:center;gap:4px;padding:0 4px;flex-shrink:0}
.alert-item{display:flex;align-items:center;gap:10px;padding:6px 16px;font-size:12px;font-family:var(--mono);border-bottom:1px solid var(--bdr)}
.alert-item:last-child{border-bottom:none}
.alert-item.warn{border-left:3px solid var(--warn);background:color-mix(in srgb,var(--warn) 6%,transparent)}
.alert-item.crit{border-left:3px solid var(--red);background:color-mix(in srgb,var(--red) 8%,transparent)}
.alert-badge{font-size:9px;font-weight:700;width:18px;height:18px;border-radius:4px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.alert-item.warn .alert-badge{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn)}
.alert-item.crit .alert-badge{background:color-mix(in srgb,var(--red) 20%,transparent);color:var(--red)}
.alert-msg{flex:1}
.alert-item.warn .alert-msg{color:var(--warn)}
.alert-item.crit .alert-msg{color:var(--red)}
.alert-x{background:none;border:none;color:var(--dim);cursor:pointer;font-size:16px;padding:0 2px;line-height:1;transition:color .15s}.alert-x:hover{color:var(--txt)}
.body-wrap{flex:1;min-height:0;display:grid;grid-template-columns:52px 264px 1fr;overflow:hidden}
.icon-rail,.center-panel,.term{min-height:0;overflow:hidden}
.icon-rail{background:var(--bg2);border-right:1px solid var(--bdr);display:flex;flex-direction:column;align-items:center;padding:8px 0;gap:4px}
.rail-tab{position:relative;width:38px;height:44px;border-radius:var(--rs);display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;border:1px solid transparent;transition:all .15s;gap:3px;flex-shrink:0;background:none}
.rail-tab:hover{background:var(--bg3);border-color:var(--bdr)}
.rail-tab.on{background:color-mix(in srgb,var(--acc) 10%,transparent);border-color:color-mix(in srgb,var(--acc) 30%,transparent)}
.rail-ico{font-family:var(--mono);font-size:10px;font-weight:700;color:var(--dim);transition:color .15s;line-height:1}
.rail-tab:hover .rail-ico,.rail-tab.on .rail-ico{color:var(--acc)}
.rail-lbl{font-size:8px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.04em;line-height:1;transition:color .15s}
.rail-tab.on .rail-lbl{color:var(--acc)}
.rail-tab.on::after{content:'';position:absolute;right:0;top:50%;transform:translateY(-50%);width:3px;height:24px;background:var(--acc);border-radius:2px 0 0 2px}
.rail-spacer{flex:1}
.center-panel{display:flex;flex-direction:column;overflow:hidden;min-height:0;background:var(--bg2);border-right:1px solid var(--bdr)}
.tab-pane{display:none;flex-direction:column;flex:1;overflow:hidden;min-height:0}
.tab-pane.on{display:flex}
.panel-hdr{padding:10px 12px 8px;border-bottom:1px solid var(--bdr);flex-shrink:0;display:flex;align-items:center;gap:6px}
.panel-hdr-title{font-size:11px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;flex:1}
.panel-hdr-btn{background:none;border:none;color:var(--dim);cursor:pointer;font-size:14px;padding:0 3px;line-height:1;transition:color .15s}
.panel-hdr-btn:hover{color:var(--acc)}
.panel-hdr-btn.acc{background:var(--acc);color:var(--bg);border-radius:var(--rs);padding:3px 10px;font-family:var(--font);font-size:11px;font-weight:700;border:none}
.panel-hdr-btn.acc:hover{filter:brightness(1.1)}
.cat-nav{padding:8px;border-bottom:1px solid var(--bdr);flex-shrink:0;display:flex;flex-direction:column;gap:2px}
.cat-tab{width:100%;text-align:left;padding:8px 12px;border-radius:var(--rs);font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;cursor:pointer;background:none;border:none;font-family:var(--font);color:var(--dim);transition:all .15s;display:flex;align-items:center;gap:9px}
.cat-tab:hover{color:var(--txt);background:var(--bg3)}
.cat-tab.on{color:var(--acc);background:color-mix(in srgb,var(--acc) 8%,transparent)}
.cat-dot{width:6px;height:6px;border-radius:50%;background:var(--dim2);flex-shrink:0;transition:background .15s}
.cat-tab.on .cat-dot{background:var(--acc)}
.cmd-list{flex:1;overflow-y:auto;padding:8px}
.cmd-list::-webkit-scrollbar,.dp-list::-webkit-scrollbar,.fs-list::-webkit-scrollbar,.scr-list::-webkit-scrollbar,.prt-list::-webkit-scrollbar{width:3px}
.cmd-list::-webkit-scrollbar-thumb,.dp-list::-webkit-scrollbar-thumb,.fs-list::-webkit-scrollbar-thumb,.scr-list::-webkit-scrollbar-thumb,.prt-list::-webkit-scrollbar-thumb{background:var(--bdr);border-radius:2px}
.sec{display:none}.sec.on{display:block}
.cbtn{width:100%;text-align:left;background:var(--bg3);border:1px solid var(--bdr);color:var(--txt);font-family:var(--font);font-size:13px;font-weight:600;padding:10px 13px;border-radius:var(--rs);cursor:pointer;margin-bottom:5px;display:flex;align-items:center;justify-content:space-between;transition:all .15s}
.cbtn:hover{background:var(--bg4);border-color:var(--hi);color:var(--txt2)}
.cbtn:active{transform:scale(.99)}
.carr{color:var(--dim);font-size:11px;transition:color .15s}.cbtn:hover .carr{color:var(--hi)}
.apps-wrap{border-top:1px solid var(--bdr);padding:10px 10px 12px;flex-shrink:0}
.apps-lbl{font-size:10px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px;padding:0 2px}
.apps-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}
.abtn{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--r);padding:9px 4px 8px;cursor:pointer;text-align:center;font-family:var(--font);color:var(--dim);transition:all .15s;display:flex;flex-direction:column;align-items:center;gap:5px}
.abtn:hover{border-color:var(--bdr2);background:var(--bg4);color:var(--txt)}
.abtn:active{transform:scale(.95)}
.aico{width:34px;height:34px;border-radius:var(--rs);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:11px;font-weight:700;color:#fff;flex-shrink:0}
.aname{font-size:9px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.dp-list{flex:1;overflow-y:auto;padding:8px}
.dc{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);padding:10px 11px;margin-bottom:6px;cursor:context-menu}
.dc-top{display:flex;align-items:center;gap:8px;margin-bottom:5px}
.dc-state-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dc-name{font-size:13px;font-weight:700;color:var(--txt);font-family:var(--mono);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dc-state-lbl{font-size:10px;font-weight:700;font-family:var(--mono);flex-shrink:0}
.dc-image{font-size:11px;color:var(--dim);font-family:var(--mono);margin-bottom:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dc-actions{display:flex;gap:4px}
.dca{flex:1;background:var(--bg4);border:1px solid var(--bdr);color:var(--dim);border-radius:6px;padding:5px 4px;font-size:10px;font-weight:700;cursor:pointer;font-family:var(--font);text-align:center;transition:all .15s}
.dca:disabled{opacity:.35;cursor:not-allowed}
.dca.s-start:not(:disabled):hover{border-color:var(--acc);color:var(--acc)}
.dca.s-stop:not(:disabled):hover{border-color:var(--red);color:var(--red)}
.dca.s-restart:not(:disabled):hover{border-color:var(--hi);color:var(--hi)}
.dp-stats{display:grid;grid-template-columns:1fr 1fr;gap:5px;padding:8px 8px 4px;flex-shrink:0}
.dp-stat{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);padding:8px 10px}
.dp-stat-lbl{font-size:9px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px}
.dp-stat-val{font-size:18px;font-weight:800;color:var(--txt2);font-family:var(--mono)}
.dp-stat-val.ok{color:var(--acc)}.dp-stat-val.bad{color:var(--red)}
.dp-msg{padding:20px 12px;font-size:12px;color:var(--dim);text-align:center;font-family:var(--mono);line-height:1.7}
.dp-msg code{color:var(--acc);background:var(--bg3);padding:1px 6px;border-radius:4px;font-size:10px}
.fs-crumbs{padding:8px 10px;border-bottom:1px solid var(--bdr);flex-shrink:0;display:flex;align-items:center;flex-wrap:wrap;gap:2px;font-family:var(--mono);font-size:11px;min-height:36px}
.fs-crumb{color:var(--dim);cursor:pointer;padding:2px 4px;border-radius:4px;transition:color .15s;white-space:nowrap}
.fs-crumb:hover{color:var(--acc)}
.fs-crumb.active{color:var(--txt);cursor:default}
.fs-sep{color:var(--dim2);padding:0 1px}
.fs-list{flex:1;overflow-y:auto}
.fs-entry{display:flex;align-items:center;padding:0 8px;height:34px;cursor:pointer;border-bottom:1px solid color-mix(in srgb,var(--bdr) 35%,transparent);transition:background .1s}
.fs-entry:hover{background:var(--bg3)}
.fs-entry:last-child{border-bottom:none}
.fs-icon{font-family:var(--mono);font-size:9px;font-weight:700;color:var(--dim);flex-shrink:0;width:28px;text-align:center}
.fs-icon.dir{color:var(--hi)}
.fs-name{flex:1;min-width:0;font-size:12px;color:var(--txt);font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 4px}
.fs-entry:hover .fs-name{color:var(--txt2)}
.fs-size{font-size:10px;color:var(--dim);font-family:var(--mono);flex-shrink:0;min-width:44px;text-align:right;padding-right:4px}
.fs-acts{display:flex;gap:2px;flex-shrink:0;opacity:0;transition:opacity .15s;margin-left:4px}
.fs-entry:hover .fs-acts{opacity:1}
.fs-act{background:none;border:1px solid transparent;color:var(--dim);border-radius:5px;padding:3px 5px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;line-height:1}
.fs-act:hover{border-color:var(--bdr);background:var(--bg3);color:var(--txt)}
.fs-act.dl:hover{border-color:var(--acc);color:var(--acc);background:color-mix(in srgb,var(--acc) 8%,transparent)}
.fs-act svg{width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.fs-toolbar{display:flex;align-items:center;gap:5px;padding:6px 8px;border-top:1px solid var(--bdr);flex-shrink:0}
.fs-upload-btn{flex:1;background:color-mix(in srgb,var(--acc) 10%,transparent);border:1px dashed color-mix(in srgb,var(--acc) 40%,transparent);color:var(--acc);border-radius:var(--rs);padding:7px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--font);text-align:center;transition:all .15s}
.fs-upload-btn:hover{background:color-mix(in srgb,var(--acc) 18%,transparent)}
.fs-mkdir-btn{background:var(--bg3);border:1px solid var(--bdr);color:var(--dim);border-radius:var(--rs);padding:7px 10px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--font);transition:all .15s;white-space:nowrap}
.fs-mkdir-btn:hover{border-color:var(--hi);color:var(--hi)}
.fs-msg{padding:24px 12px;font-size:12px;color:var(--dim);text-align:center;font-family:var(--mono)}
#fs-file-input{display:none}
.fs-drop-active{outline:2px dashed var(--acc)!important}
.sts-pane{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:8px}
.sts-card{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);padding:10px 12px}
.sts-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.sts-label{font-size:11px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.sts-val{font-size:16px;font-weight:800;color:var(--txt2);font-family:var(--mono)}
.sts-chart{height:60px}
.scr-list{flex:1;overflow-y:auto;padding:8px}
.scr-card{background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);padding:10px 12px;margin-bottom:6px;cursor:context-menu;display:flex;align-items:center;gap:10px}
.scr-card:hover{background:var(--bg4);border-color:var(--bdr2)}
.scr-badge{width:32px;height:32px;border-radius:var(--rs);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:10px;font-weight:700;color:#fff;flex-shrink:0}
.scr-body{flex:1;min-width:0}
.scr-name{font-size:13px;font-weight:700;color:var(--txt);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scr-desc{font-size:11px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:2px}
.scr-run{background:var(--acc);color:var(--bg);border:none;border-radius:var(--rs);padding:5px 12px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--font);transition:all .15s;flex-shrink:0}
.scr-run:hover{filter:brightness(1.1)}
.scr-empty{padding:24px 12px;font-size:12px;color:var(--dim);text-align:center;font-family:var(--mono);line-height:1.8}
.prt-list{flex:1;overflow-y:auto}
.prt-hdr-row,.prt-row{display:grid;grid-template-columns:58px 44px 70px 1fr;align-items:center;padding:7px 12px;border-bottom:1px solid color-mix(in srgb,var(--bdr) 40%,transparent);font-family:var(--mono);font-size:12px}
.prt-hdr-row{font-size:10px;font-weight:700;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;background:var(--bg3);border-bottom:1px solid var(--bdr);flex-shrink:0}
.prt-row{cursor:pointer;transition:background .1s}
.prt-row:hover{background:var(--bg3)}
.prt-row:last-child{border-bottom:none}
.prt-port{font-weight:700;color:var(--txt2)}
.prt-badge{font-size:9px;font-weight:700;padding:2px 5px;border-radius:4px;width:fit-content}
.prt-tcp{background:color-mix(in srgb,var(--hi) 15%,transparent);color:var(--hi)}
.prt-udp{background:color-mix(in srgb,var(--warn) 15%,transparent);color:var(--warn)}
.prt-state{font-size:11px;color:var(--dim)}
.prt-proc{color:var(--txt);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.prt-proc.empty{color:var(--dim2)}
.prt-msg{padding:20px 12px;font-size:12px;color:var(--dim);text-align:center;font-family:var(--mono)}
.prt-summary{display:flex;gap:5px;padding:8px 10px;border-bottom:1px solid var(--bdr);flex-shrink:0;flex-wrap:wrap}
.prt-stat{font-size:11px;font-weight:700;padding:3px 9px;border-radius:10px;background:var(--bg3);border:1px solid var(--bdr);color:var(--dim)}
.term{display:flex;flex-direction:column;min-height:0;min-width:0;overflow:hidden;background:var(--bg)}
.t-bar{background:var(--bg2);border-bottom:1px solid var(--bdr);display:flex;align-items:center;gap:10px;padding:7px 16px;font-size:11px;color:var(--dim);flex-shrink:0}
.t-bar code{font-family:var(--mono);font-size:11px;color:var(--hi);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.clr{background:none;border:1px solid var(--bdr);color:var(--dim);border-radius:var(--rs);padding:3px 10px;cursor:pointer;font-size:11px;font-family:var(--font);transition:all .15s;flex-shrink:0}
.clr:hover{border-color:var(--red);color:var(--red)}
#out{flex:1;overflow-y:auto;padding:16px 20px;font-family:var(--mono);font-size:13px;line-height:1.7;min-height:0;min-width:0;user-select:text}
#out::-webkit-scrollbar{width:5px}
#out::-webkit-scrollbar-thumb{background:var(--bdr);border-radius:3px}
.ob{margin-bottom:20px;border-bottom:1px solid color-mix(in srgb,var(--bdr) 50%,transparent);padding-bottom:16px}
.ob:last-child{border-bottom:none}
.oc{color:var(--dim);margin-bottom:6px;font-size:12px}
.oc .pr{color:var(--acc)}
.os{color:var(--out);white-space:pre-wrap;word-break:break-all}
.oe{color:var(--red);white-space:pre-wrap;word-break:break-all}
.ex0{color:var(--acc);font-size:11px;margin-top:6px}
.ex1{color:var(--red);font-size:11px;margin-top:6px}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.spin{animation:blink .8s ease-in-out infinite;color:var(--dim)}
.empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--dim);gap:10px}
.empty .big{font-size:32px;font-family:var(--mono);font-weight:700;color:var(--dim2)}
.empty p{font-size:13px}
.ibar{background:var(--bg2);border-top:1px solid var(--bdr);padding:10px 14px;display:flex;gap:8px;align-items:center}
.pr2{font-family:var(--mono);color:var(--acc);font-size:16px;font-weight:700;flex-shrink:0}
#ci{flex:1;min-width:0;background:var(--bg3);border:1px solid var(--bdr);border-radius:var(--rs);color:var(--txt);font-family:var(--mono);font-size:13px;padding:9px 13px;outline:none;transition:border-color .2s}
#ci:focus{border-color:var(--acc)}
#rb{background:var(--acc);color:var(--bg);border:none;border-radius:var(--rs);padding:9px 22px;cursor:pointer;font-family:var(--font);font-weight:700;font-size:13px;transition:all .2s;white-space:nowrap;flex-shrink:0}
#rb:hover{filter:brightness(1.1)}
#rb:active{transform:scale(.97)}
#rb:disabled{background:var(--dim2);cursor:not-allowed;color:var(--dim)}
[data-theme="matrix"] #out::after{content:'';position:fixed;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.08) 2px,rgba(0,0,0,.08) 4px);z-index:0}
</style>
</head>
<body>

<div id="idle-modal">
  <div class="idle-card">
    <div class="idle-icon">!</div>
    <div class="idle-title">Session expiring</div>
    <div class="idle-sub">No activity detected.<br>You will be signed out in:</div>
    <div class="idle-countdown" id="idle-countdown">60</div>
    <button class="idle-btn" id="idle-stay-btn">Stay signed in</button>
    <div class="idle-hint">Any click or keypress also keeps you signed in</div>
  </div>
</div>

<div id="ctx-menu"></div>

<div id="rnm-modal" class="modal-ov">
  <div class="modal-card">
    <div class="modal-title" id="rnm-title">Rename</div>
    <input class="modal-input" id="rnm-input" type="text" spellcheck="false">
    <div class="modal-btns">
      <button class="mbtn cancel" onclick="closeRename()">Cancel</button>
      <button class="mbtn confirm" onclick="doRename()">Rename</button>
    </div>
  </div>
</div>

<div id="scr-modal" class="modal-ov">
  <div class="modal-card">
    <div class="modal-title" id="scr-modal-title">New script</div>
    <input class="modal-input" id="scr-name" type="text" placeholder="Script name" spellcheck="false">
    <input class="modal-input" id="scr-desc" type="text" placeholder="Description (optional)" spellcheck="false">
    <textarea class="scr-cmd-ta" id="scr-cmd" placeholder="Shell command or multi-line script" spellcheck="false"></textarea>
    <div class="color-row">
      <label>Colour</label>
      <span class="cpick on" data-c="#4a607a" style="background:#4a607a"></span>
      <span class="cpick" data-c="#0076d6" style="background:#0076d6"></span>
      <span class="cpick" data-c="#1ca554" style="background:#1ca554"></span>
      <span class="cpick" data-c="#e86a17" style="background:#e86a17"></span>
      <span class="cpick" data-c="#c41a4a" style="background:#c41a4a"></span>
      <span class="cpick" data-c="#6246ea" style="background:#6246ea"></span>
      <span class="cpick" data-c="#0277bd" style="background:#0277bd"></span>
      <span class="cpick" data-c="#b07800" style="background:#b07800"></span>
    </div>
    <div class="modal-btns">
      <button class="mbtn cancel" onclick="closeScrModal()">Cancel</button>
      <button class="mbtn danger" id="scr-delete-btn" style="display:none" onclick="doDeleteScript()">Delete</button>
      <button class="mbtn confirm" onclick="doSaveScript()">Save</button>
    </div>
  </div>
</div>

<div id="edit-modal">
  <div class="edit-card">
    <div class="edit-hdr">
      <span class="edit-filename" id="edit-filename"></span>
      <span class="edit-status" id="edit-status"></span>
      <button class="edit-close" onclick="closeEditor()">&times;</button>
    </div>
    <textarea id="edit-ta" spellcheck="false"></textarea>
    <div class="edit-footer">
      <button class="edit-save" onclick="saveFile()">Save</button>
      <button class="edit-discard" onclick="closeEditor()">Discard</button>
      <div class="edit-sp"></div>
      <span style="font-size:11px;color:var(--dim);font-family:var(--mono)">Ctrl+S to save</span>
    </div>
  </div>
</div>

<!-- LOGIN -->
<div id="login-ov">
  <div class="lcard">
    <div class="llogo">devboard &mdash; v10.0</div>
    <div class="ltitle">Sign in</div>
    <div class="lsub" id="l-sub">Loading&hellip;</div>
    <div class="lthemes">
      <span class="lthemes-lbl">Theme</span>
      <span class="sw" data-t="pi"       style="background:#c51a4a" title="Pi Modern"></span>
      <span class="sw" data-t="terminal" style="background:#00e87a" title="True Terminal"></span>
      <span class="sw" data-t="matrix"   style="background:#00ff41" title="The Matrix"></span>
      <span class="sw" data-t="forest"   style="background:#74c97e" title="Calm Forest"></span>
      <span class="sw" data-t="blue"     style="background:#38bdf8" title="Deep Blue"></span>
      <span class="sw" data-t="frost"    style="background:#1565c0" title="Arctic Frost"></span>
    </div>
    <div class="field" id="l-user-field"><label>Username</label><input id="l-user" type="text" placeholder="pi" autocomplete="username" spellcheck="false"></div>
    <div class="field"><label>Password</label><input id="l-pass" type="password" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;" autocomplete="current-password"></div>
    <button class="lbtn" id="l-btn" onclick="doLogin()">Sign in</button>
    <div class="lerr" id="l-err"></div>
    <div class="lnote" id="l-note"></div>
  </div>
</div>

<!-- APP -->
<div id="app">
  <header>
    <div class="logo">pi<em>@</em><span class="logo-user" id="h-user">dashboard</span></div>
    <div class="sp"></div>
    <div class="chip" id="c-temp">TEMP&nbsp;<b id="s-temp">—</b></div>
    <div class="chip" id="c-cpu">CPU&nbsp;<b id="s-cpu">—</b></div>
    <div class="chip" id="c-ram">RAM&nbsp;<b id="s-ram">—</b></div>
    <div class="chip" id="c-disk">DISK&nbsp;<b id="s-disk">—</b></div>
    <button class="hbtn" onclick="refreshStats()">&#x21BB;</button>
    <div class="theme-strip">
      <span class="sw" data-t="pi"       style="background:#c51a4a" title="Pi Modern"></span>
      <span class="sw" data-t="terminal" style="background:#00e87a" title="True Terminal"></span>
      <span class="sw" data-t="matrix"   style="background:#00ff41" title="The Matrix"></span>
      <span class="sw" data-t="forest"   style="background:#74c97e" title="Calm Forest"></span>
      <span class="sw" data-t="blue"     style="background:#38bdf8" title="Deep Blue"></span>
      <span class="sw" data-t="frost"    style="background:#1565c0" title="Arctic Frost"></span>
    </div>
    <button class="hbtn danger" onclick="doLogout()">sign out</button>
  </header>
  <div id="alert-bar"></div>
  <div class="body-wrap">
    <div class="icon-rail">
      <button class="rail-tab on" id="rt-cmd"    onclick="switchTab('cmd')"    title="Commands"><span class="rail-ico">CMD</span><span class="rail-lbl">Cmds</span></button>
      <button class="rail-tab"    id="rt-docker" onclick="switchTab('docker')" title="Docker"><span class="rail-ico">DKR</span><span class="rail-lbl">Docker</span></button>
      <button class="rail-tab"    id="rt-fs"     onclick="switchTab('fs')"     title="Files"><span class="rail-ico">FLS</span><span class="rail-lbl">Files</span></button>
      <button class="rail-tab"    id="rt-sts"    onclick="switchTab('sts')"    title="Stats"><span class="rail-ico">STS</span><span class="rail-lbl">Stats</span></button>
      <button class="rail-tab"    id="rt-scr"    onclick="switchTab('scr')"    title="Scripts"><span class="rail-ico">SCR</span><span class="rail-lbl">Scripts</span></button>
      <button class="rail-tab"    id="rt-prt"    onclick="switchTab('prt')"    title="Ports"><span class="rail-ico">PRT</span><span class="rail-lbl">Ports</span></button>
      <div class="rail-spacer"></div>
    </div>
    <div class="center-panel">
      <div class="tab-pane on" id="pane-cmd">
        <div class="cat-nav" id="cat-nav"></div>
        <div class="cmd-list" id="cmd-list"></div>
        <div class="apps-wrap">
          <div class="apps-lbl">Launch app</div>
          <div class="apps-grid" id="apps-grid"></div>
        </div>
      </div>
      <div class="tab-pane" id="pane-docker">
        <div class="panel-hdr"><span class="panel-hdr-title">Docker containers</span><button class="panel-hdr-btn" onclick="loadDocker()" title="Refresh">&#x21BB;</button></div>
        <div class="dp-stats" id="dp-stats"></div>
        <div class="dp-list" id="dp-list"><div class="dp-msg">Loading&hellip;</div></div>
      </div>
      <div class="tab-pane" id="pane-fs">
        <div class="panel-hdr"><span class="panel-hdr-title">File Manager</span><button class="panel-hdr-btn" onclick="fsReload()" title="Refresh">&#x21BB;</button></div>
        <div class="fs-crumbs" id="fs-crumbs"></div>
        <div class="fs-list" id="fs-list"></div>
        <div class="fs-toolbar">
          <label class="fs-upload-btn" for="fs-file-input">&#x2B06; Upload</label>
          <input type="file" id="fs-file-input" multiple onchange="fsUpload(this)">
          <button class="fs-mkdir-btn" onclick="fsMkdir()">+ Folder</button>
        </div>
      </div>
      <div class="tab-pane" id="pane-sts">
        <div class="panel-hdr"><span class="panel-hdr-title">Live stats</span><span style="font-size:10px;color:var(--dim);font-family:var(--mono);margin-right:4px" id="sts-age"></span><button class="panel-hdr-btn" onclick="pollStats()" title="Refresh now">&#x21BB;</button></div>
        <div class="sts-pane" id="sts-pane">
          <div class="sts-card"><div class="sts-top"><span class="sts-label">CPU</span><span class="sts-val" id="sts-cpu-val">—</span></div><div class="sts-chart"><canvas id="sts-cpu-chart"></canvas></div></div>
          <div class="sts-card"><div class="sts-top"><span class="sts-label">RAM</span><span class="sts-val" id="sts-ram-val">—</span></div><div class="sts-chart"><canvas id="sts-ram-chart"></canvas></div></div>
          <div class="sts-card"><div class="sts-top"><span class="sts-label">Temperature</span><span class="sts-val" id="sts-temp-val">—</span></div><div class="sts-chart"><canvas id="sts-temp-chart"></canvas></div></div>
          <div class="sts-card"><div class="sts-top"><span class="sts-label">Disk (root)</span><span class="sts-val" id="sts-disk-val">—</span></div><div class="sts-chart"><canvas id="sts-disk-chart"></canvas></div></div>
        </div>
      </div>
      <div class="tab-pane" id="pane-scr">
        <div class="panel-hdr"><span class="panel-hdr-title">Saved scripts</span><button class="panel-hdr-btn acc" onclick="openScrModal(null)">+ New</button></div>
        <div class="scr-list" id="scr-list"><div class="scr-empty">No scripts yet.</div></div>
      </div>
      <div class="tab-pane" id="pane-prt">
        <div class="panel-hdr"><span class="panel-hdr-title">Open ports</span><button class="panel-hdr-btn" onclick="loadPorts()" title="Refresh">&#x21BB;</button></div>
        <div class="prt-summary" id="prt-summary"></div>
        <div class="prt-hdr-row"><span>Port</span><span>Proto</span><span>State</span><span>Process</span></div>
        <div class="prt-list" id="prt-list"><div class="prt-msg">Loading&hellip;</div></div>
      </div>
    </div>
    <div class="term">
      <div class="t-bar"><span>OUTPUT</span><code id="lc">—</code><button class="clr" onclick="clearOut()">clear</button></div>
      <div id="out"><div class="empty"><div class="big">~/pi</div><p>Pick a command or type one below</p></div></div>
      <div class="ibar-wrap">
        <div id="hist-search"></div>
        <div class="ibar">
          <span class="pr2">$</span>
          <input id="ci" type="text" placeholder="Enter any shell command… (Ctrl+R to search history)" autocomplete="off" spellcheck="false">
          <button id="rb" onclick="runCustom()">Run</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let config={commands:{},apps:[]}, hist=[], hidx=-1, busy=false, fsCwd='';
let _renameCtx=null, _editCtx=null, _scrEditId=null, _scrColor='#4a607a';
let _timeoutMs=7*60*1000, _authMode='pam';

/* ── Idle timeout (unchanged from v9) ── */
let _idleTimer=null, _warnTimer=null, _countdownInterval=null, _warnActive=false;
function resetIdle(){
  if(_warnActive) dismissIdleModal();
  clearTimeout(_idleTimer); clearTimeout(_warnTimer);
  const warnAt=_timeoutMs-60000;
  _warnTimer=setTimeout(showIdleWarning, warnAt>0?warnAt:0);
}
function showIdleWarning(){
  _warnActive=true; let secs=60;
  document.getElementById('idle-countdown').textContent=secs;
  document.getElementById('idle-modal').classList.add('show');
  _countdownInterval=setInterval(()=>{
    secs--; document.getElementById('idle-countdown').textContent=secs;
    if(secs<=0){clearInterval(_countdownInterval);doIdleLogout();}
  },1000);
}
function dismissIdleModal(){
  _warnActive=false; clearInterval(_countdownInterval);
  document.getElementById('idle-modal').classList.remove('show'); resetIdle();
}
async function doIdleLogout(){
  clearInterval(_countdownInterval);
  await fetch('/api/logout',{method:'POST'}).catch(()=>{});
  location.reload();
}
function trackActivity(){
  document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')resetIdle();},true);
  document.addEventListener('click',e=>{if(e.target.closest('button'))resetIdle();},true);
  document.addEventListener('contextmenu',()=>resetIdle(),true);
}
document.getElementById('idle-stay-btn').addEventListener('click',()=>dismissIdleModal());

/* ── Theme ── */
const VALID_THEMES=['pi','terminal','matrix','forest','blue','frost'];
function applyTheme(t){
  if(!VALID_THEMES.includes(t))t='terminal';
  document.documentElement.setAttribute('data-theme',t);
  localStorage.setItem('pi-dash-theme',t);
  document.querySelectorAll('.sw').forEach(s=>s.classList.toggle('on',s.dataset.t===t));
}
document.querySelectorAll('.sw').forEach(s=>s.addEventListener('click',()=>applyTheme(s.dataset.t)));
applyTheme(localStorage.getItem('pi-dash-theme')||'terminal');

/* ── Context menu ── */
const ctxMenu=document.getElementById('ctx-menu');
function showCtx(ev,items){
  ev.preventDefault();ev.stopPropagation();ctxMenu.innerHTML='';
  items.forEach(item=>{
    if(item==='-'){const d=document.createElement('div');d.className='ctx-sep';ctxMenu.appendChild(d);return;}
    const b=document.createElement('button');
    b.className='ctx-item'+(item.cls?' '+item.cls:'');
    b.innerHTML=`<span class="ctx-ico">${item.ico||''}</span>${esc(item.label)}`;
    b.onclick=()=>{closeCtx();item.fn();};ctxMenu.appendChild(b);
  });
  const mw=180,mh=ctxMenu.childElementCount*38;
  let x=ev.clientX,y=ev.clientY;
  if(x+mw>window.innerWidth)x=window.innerWidth-mw-8;
  if(y+mh>window.innerHeight)y=window.innerHeight-mh-8;
  ctxMenu.style.left=x+'px';ctxMenu.style.top=y+'px';ctxMenu.classList.add('show');
}
function closeCtx(){ctxMenu.classList.remove('show');}
document.addEventListener('click',closeCtx);
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeCtx();closeRename();closeEditor();}});
function fsCtx(ev,relPath,entry){
  document.querySelectorAll('.fs-entry.ctx-open').forEach(e=>e.classList.remove('ctx-open'));
  ev.currentTarget.classList.add('ctx-open');
  const items=[];
  if(entry.type==='dir'){items.push({ico:'&#x25BA;',label:'Open',fn:()=>fsLoad(relPath)});}
  else{
    items.push({ico:'&#x2B07;',label:'Download',cls:'accent',fn:()=>fsDl(new Event('x'),encodeURIComponent(relPath),entry.name)});
    if(entry.editable)items.push({ico:'&#x270E;',label:'Edit',fn:()=>openEditor(relPath,entry.name)});
  }
  items.push('-');
  items.push({ico:'&#x270F;',label:'Rename',fn:()=>openRename(relPath,entry.name)});
  if(entry.type==='file')items.push({ico:'&#x2398;',label:'Duplicate',fn:()=>fsDuplicate(relPath)});
  items.push('-');
  items.push({ico:'&#x2715;',label:'Delete',cls:'danger',fn:()=>fsRm(new Event('x'),encodeURIComponent(relPath),entry.name,true)});
  showCtx(ev,items);
}
function cmdCtx(ev,cmd,name){
  showCtx(ev,[
    {ico:'&#x25BA;',label:'Run',cls:'accent',fn:()=>run(cmd,name)},
    {ico:'&#x2398;',label:'Copy command',fn:()=>navigator.clipboard.writeText(cmd).catch(()=>{})},
    '-',
    {ico:'&#x2295;',label:'Paste to input',fn:()=>{ci.value=cmd;ci.focus();}},
    {ico:'&#x2B07;',label:'Save as script',fn:()=>openScrModal(null,{name,cmd})},
  ]);
}
document.getElementById('out').addEventListener('contextmenu',ev=>{
  showCtx(ev,[
    {ico:'&#x2398;',label:'Copy all output',fn:()=>navigator.clipboard.writeText(document.getElementById('out').innerText).catch(()=>{})},
    {ico:'&#x2715;',label:'Clear output',cls:'danger',fn:clearOut},
  ]);
});
function dcCtx(ev,c){
  const isRun=c.state==='running';
  showCtx(ev,[
    {ico:'&#x25BA;',label:'Start',cls:isRun?'':'accent',fn:()=>dkAct('start',c.id)},
    {ico:'&#x25A0;',label:'Stop',cls:isRun?'danger':'',fn:()=>dkAct('stop',c.id)},
    {ico:'&#x21BA;',label:'Restart',fn:()=>dkAct('restart',c.id)},
    '-',
    {ico:'&#x1F4CB;',label:'View logs',fn:()=>dockerLogs(c.name)},
  ]);
}
function scrCtx(ev,s){
  showCtx(ev,[
    {ico:'&#x25BA;',label:'Run',cls:'accent',fn:()=>run(s.cmd,s.name)},
    '-',
    {ico:'&#x270E;',label:'Edit',fn:()=>openScrModal(s)},
    {ico:'&#x2715;',label:'Delete',cls:'danger',fn:()=>deleteScript(s.id)},
  ]);
}

/* ── Rename modal ── */
function openRename(relPath,currentName){
  _renameCtx={relPath,currentName};
  document.getElementById('rnm-title').textContent='Rename "'+currentName+'"';
  const inp=document.getElementById('rnm-input');inp.value=currentName;
  document.getElementById('rnm-modal').classList.add('show');
  setTimeout(()=>{inp.focus();inp.select();},50);
}
function closeRename(){document.getElementById('rnm-modal').classList.remove('show');_renameCtx=null;}
document.getElementById('rnm-input').addEventListener('keydown',e=>{if(e.key==='Enter')doRename();if(e.key==='Escape')closeRename();});
async function doRename(){
  if(!_renameCtx)return;
  const newName=document.getElementById('rnm-input').value.trim();
  if(!newName||newName===_renameCtx.currentName){closeRename();return;}
  try{
    const r=await fetch('/api/fs/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:_renameCtx.relPath,name:newName})});
    const d=await r.json();
    if(d.ok){closeRename();fsReload();}else alert('Rename failed: '+d.error);
  }catch(e){alert('Error: '+e.message);}
}

/* ── Script modal ── */
document.querySelectorAll('.cpick').forEach(c=>{
  c.addEventListener('click',()=>{
    document.querySelectorAll('.cpick').forEach(x=>x.classList.remove('on'));
    c.classList.add('on');_scrColor=c.dataset.c;
  });
});
function openScrModal(script,prefill){
  _scrEditId=script?script.id:null;
  document.getElementById('scr-modal-title').textContent=script?'Edit script':'New script';
  document.getElementById('scr-name').value=script?script.name:(prefill?.name||'');
  document.getElementById('scr-desc').value=script?script.desc:'';
  document.getElementById('scr-cmd').value=script?script.cmd:(prefill?.cmd||'');
  const col=script?script.color:'#4a607a';
  _scrColor=col;
  document.querySelectorAll('.cpick').forEach(c=>c.classList.toggle('on',c.dataset.c===col));
  document.getElementById('scr-delete-btn').style.display=script?'':'none';
  document.getElementById('scr-modal').classList.add('show');
  setTimeout(()=>document.getElementById('scr-name').focus(),50);
}
function closeScrModal(){document.getElementById('scr-modal').classList.remove('show');_scrEditId=null;}
async function doSaveScript(){
  const name=document.getElementById('scr-name').value.trim();
  const cmd=document.getElementById('scr-cmd').value.trim();
  if(!name||!cmd){alert('Name and command are required.');return;}
  const body={name,desc:document.getElementById('scr-desc').value.trim(),cmd,color:_scrColor};
  try{
    let r;
    if(_scrEditId) r=await fetch('/api/scripts/'+_scrEditId,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    else r=await fetch('/api/scripts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){closeScrModal();loadScripts();}else alert('Save failed: '+d.error);
  }catch(e){alert('Error: '+e.message);}
}
async function doDeleteScript(){if(!_scrEditId)return;if(!confirm('Delete this script?'))return;await deleteScript(_scrEditId);closeScrModal();}
async function deleteScript(id){
  try{const r=await fetch('/api/scripts/'+id,{method:'DELETE'});const d=await r.json();if(d.ok)loadScripts();else alert('Delete failed: '+d.error);}
  catch(e){alert('Error: '+e.message);}
}

/* ── Text editor ── */
function openEditor(relPath,name){
  _editCtx={relPath,name};
  document.getElementById('edit-filename').textContent=name;
  document.getElementById('edit-status').textContent='Loading\u2026';
  document.getElementById('edit-ta').value='';
  document.getElementById('edit-modal').classList.add('show');
  fetch('/api/fs/read?path='+encodeURIComponent(relPath))
    .then(r=>r.json())
    .then(d=>{
      if(d.error){document.getElementById('edit-status').textContent=d.error;return;}
      document.getElementById('edit-ta').value=d.content;
      document.getElementById('edit-status').textContent='';
      document.getElementById('edit-ta').focus();
    }).catch(e=>{document.getElementById('edit-status').textContent='Error: '+e.message;});
}
function closeEditor(){document.getElementById('edit-modal').classList.remove('show');_editCtx=null;}
async function saveFile(){
  if(!_editCtx)return;
  const content=document.getElementById('edit-ta').value;
  document.getElementById('edit-status').textContent='Saving\u2026';
  try{
    const r=await fetch('/api/fs/write',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:_editCtx.relPath,content})});
    const d=await r.json();
    document.getElementById('edit-status').textContent=d.ok?'Saved \u2713':'Error: '+d.error;
    if(d.ok)setTimeout(()=>{if(document.getElementById('edit-status').textContent.startsWith('Saved'))document.getElementById('edit-status').textContent='';},2000);
  }catch(e){document.getElementById('edit-status').textContent='Error: '+e.message;}
}
document.getElementById('edit-ta').addEventListener('keydown',e=>{
  if(e.ctrlKey&&e.key==='s'){e.preventDefault();saveFile();}
  if(e.key==='Tab'){e.preventDefault();const ta=e.target,s=ta.selectionStart;ta.value=ta.value.slice(0,s)+'\t'+ta.value.slice(ta.selectionEnd);ta.selectionStart=ta.selectionEnd=s+1;}
});

/* ── Tab ── */
function switchTab(name){
  document.querySelectorAll('.rail-tab').forEach(b=>b.classList.remove('on'));
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('on'));
  document.getElementById('rt-'+name).classList.add('on');
  document.getElementById('pane-'+name).classList.add('on');
  if(name==='docker')loadDocker();
  if(name==='fs')fsLoad(fsCwd);
  if(name==='scr')loadScripts();
  if(name==='prt')loadPorts();
  if(name==='sts'){
    initCharts();
    if(!_stsTimer){pollStats();_stsTimer=setInterval(pollStats,3000);}
  } else {
    if(_stsTimer){clearInterval(_stsTimer);_stsTimer=null;}
  }
}

/* ── Login ── */
// Fetch auth mode on load to conditionally show/hide username field
(async()=>{
  try{
    const r=await fetch('/api/auth-mode');
    const d=await r.json();
    _authMode=d.mode||'pam';
  }catch(e){_authMode='pam';}

  if(_authMode==='password'){
    document.getElementById('l-user-field').style.display='none';
    document.getElementById('l-sub').textContent='Enter your dashboard password.';
    document.getElementById('l-note').innerHTML='No password set? Run <code>python3 setup.py</code> to configure.';
  } else {
    document.getElementById('l-sub').textContent='Your system username and password \u2014 same as SSH.';
    document.getElementById('l-note').innerHTML='If login fails: <code>sudo usermod -aG shadow $USER</code> then log out &amp; back in.';
  }
})();

['l-user','l-pass'].forEach(id=>{
  document.getElementById(id).addEventListener('keydown',e=>{
    document.getElementById('l-err').textContent='';
    if(e.key==='Enter')doLogin();
  });
});
async function doLogin(){
  const user=document.getElementById('l-user').value.trim();
  const pass=document.getElementById('l-pass').value;
  const err=document.getElementById('l-err'),btn=document.getElementById('l-btn');
  if(_authMode==='pam'&&!user){err.textContent='Enter username and password.';return;}
  if(!pass){err.textContent='Enter your password.';return;}
  btn.disabled=true;btn.textContent='Signing in\u2026';
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user,password:pass})});
    const d=await r.json();
    if(d.ok){document.getElementById('login-ov').classList.add('hide');document.getElementById('h-user').textContent=d.username;loadApp();}
    else{err.textContent=d.error||'Login failed.';document.getElementById('l-pass').value='';}
  }catch(e){err.textContent='Connection error: '+e.message;}
  btn.disabled=false;btn.textContent='Sign in';
}
async function doLogout(){await fetch('/api/logout',{method:'POST'});location.reload();}

/* ── Init ── */
(async()=>{
  try{
    const w=await fetch('/api/whoami');
    if(w.ok){const d=await w.json();if(d.ok){document.getElementById('login-ov').classList.add('hide');document.getElementById('h-user').textContent=d.username;loadApp();return;}}
  }catch(e){}
  document.getElementById(_authMode==='password'?'l-pass':'l-user').focus();
})();

async function loadApp(){
  const r=await fetch('/api/config');if(!r.ok){location.reload();return;}
  config=await r.json();
  _timeoutMs=config.timeout_ms||7*60*1000;
  _authMode=config.auth_mode||'pam';
  buildSidebar();refreshStats();loadAlerts();
  setInterval(refreshStats,15000);setInterval(loadAlerts,30000);
  trackActivity();resetIdle();
}

/* ── Alerts ── */
async function loadAlerts(){
  try{
    const r=await fetch('/api/alerts');if(!r.ok)return;
    const d=await r.json();
    const bar=document.getElementById('alert-bar');
    const gone=JSON.parse(sessionStorage.getItem('dismissed')||'[]');
    const show=d.alerts.filter(a=>!gone.includes(a.msg));
    bar.innerHTML='';
    if(show.length){
      bar.classList.add('has-alerts');
      show.forEach(a=>{
        const el=document.createElement('div');el.className='alert-item '+a.level;
        el.innerHTML=`<span class="alert-badge">${esc(a.icon)}</span><span class="alert-msg">${esc(a.msg)}</span><button class="alert-x">&times;</button>`;
        el.querySelector('.alert-x').onclick=()=>{const arr=JSON.parse(sessionStorage.getItem('dismissed')||'[]');arr.push(a.msg);sessionStorage.setItem('dismissed',JSON.stringify(arr));loadAlerts();};
        bar.appendChild(el);
      });
    }else{bar.classList.remove('has-alerts');}
  }catch(e){}
}

/* ── Ports ── */
let _prtRefresh=null;
async function loadPorts(){
  const list=document.getElementById('prt-list'),summary=document.getElementById('prt-summary');
  list.innerHTML='<div class="prt-msg">Loading\u2026</div>';summary.innerHTML='';
  try{
    const r=await fetch('/api/ports');if(!r.ok)return;
    const d=await r.json();
    const ports=d.ports;
    if(!ports||!ports.length){list.innerHTML='<div class="prt-msg">No open ports found.</div>';return;}
    const tcp=ports.filter(p=>p.proto==='TCP').length,udp=ports.filter(p=>p.proto==='UDP').length;
    summary.innerHTML=`<span class="prt-stat">Total: ${ports.length}</span><span class="prt-stat" style="color:var(--hi)">TCP: ${tcp}</span><span class="prt-stat" style="color:var(--warn)">UDP: ${udp}</span>`;
    list.innerHTML='';
    ports.forEach(p=>{
      const row=document.createElement('div');row.className='prt-row';
      row.innerHTML=`<span class="prt-port">${p.port}</span><span><span class="prt-badge ${p.proto==='TCP'?'prt-tcp':'prt-udp'}">${p.proto}</span></span><span class="prt-state">${esc(p.state)}</span><span class="prt-proc ${p.proc?'':'empty'}">${p.proc?esc(p.proc):'—'}</span>`;
      row.title='Click to look up process in terminal';
      row.addEventListener('click',()=>{
        const cmd=p.proc?`ps aux | grep -i "${p.proc}" | grep -v grep`:`ss -tulpn | grep :${p.port}`;
        run(cmd,`port ${p.port} lookup`);
      });
      list.appendChild(row);
    });
    clearTimeout(_prtRefresh);
    _prtRefresh=setTimeout(()=>{if(document.getElementById('pane-prt').classList.contains('on'))loadPorts();},15000);
  }catch(e){list.innerHTML=`<div class="prt-msg">Error: ${esc(e.message)}</div>`;}
}

/* ── Docker ── */
const SC={running:'#00e87a',exited:'#ff4d6a',stopped:'#ff4d6a',paused:'#ffb300',restarting:'#3d9eff',created:'#4a607a'};
async function loadDocker(){
  const list=document.getElementById('dp-list'),stats=document.getElementById('dp-stats');
  list.innerHTML='<div class="dp-msg">Loading\u2026</div>';stats.innerHTML='';
  try{
    const r=await fetch('/api/docker');if(!r.ok)return;
    const d=await r.json();
    if(!d.available){list.innerHTML='<div class="dp-msg">Docker not found.<br>Install Docker Desktop or docker.io for your OS.</div>';return;}
    const all=d.containers,runC=all.filter(c=>c.state==='running').length,stp=all.length-runC;
    stats.innerHTML=`<div class="dp-stat"><div class="dp-stat-lbl">Running</div><div class="dp-stat-val ok">${runC}</div></div><div class="dp-stat"><div class="dp-stat-lbl">Stopped</div><div class="dp-stat-val ${stp>0?'bad':'ok'}">${stp}</div></div>`;
    if(!all.length){list.innerHTML='<div class="dp-msg">No containers found.</div>';return;}
    list.innerHTML='';
    all.forEach(c=>{
      const col=SC[c.state]||'#4a607a',isRun=c.state==='running';
      const card=document.createElement('div');card.className='dc';
      card.innerHTML=`<div class="dc-top"><div class="dc-state-dot" style="background:${col}"></div><div class="dc-name">${esc(c.name.replace(/^[/]/,''))}</div><div class="dc-state-lbl" style="color:${col}">${esc(c.state)}</div></div><div class="dc-image">${esc(c.image)}</div><div class="dc-actions"><button class="dca s-start" ${isRun?'disabled':''} data-id="${c.id}" data-act="start">Start</button><button class="dca s-stop" ${!isRun?'disabled':''} data-id="${c.id}" data-act="stop">Stop</button><button class="dca s-restart" data-id="${c.id}" data-act="restart">Restart</button></div>`;
      card.querySelectorAll('.dca').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();dkAct(b.dataset.act,b.dataset.id,b);}));
      card.addEventListener('contextmenu',ev=>dcCtx(ev,c));
      list.appendChild(card);
    });
  }catch(e){list.innerHTML='<div class="dp-msg">Could not connect to Docker.</div>';}
}
async function dkAct(action,id,btn){
  const card=btn?btn.closest('.dc'):null;
  if(card)card.querySelectorAll('.dca').forEach(b=>b.disabled=true);
  try{await fetch('/api/docker/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,id})});setTimeout(loadDocker,900);}
  catch(e){if(card)card.querySelectorAll('.dca').forEach(b=>b.disabled=false);}
}
async function dockerLogs(name){run(`docker logs --tail 50 ${name} 2>&1`,'docker logs '+name);}

/* ── Scripts ── */
const _scrMap=new Map();
async function loadScripts(){
  try{
    const r=await fetch('/api/scripts');if(!r.ok)return;
    const d=await r.json();
    _scrMap.clear();
    const list=document.getElementById('scr-list');list.innerHTML='';
    if(!d.scripts.length){list.innerHTML='<div class="scr-empty">No scripts yet.<br>Click + New to create one,<br>or right-click any CMD button to save it.</div>';return;}
    d.scripts.forEach(s=>{
      _scrMap.set(s.id,s);
      const card=document.createElement('div');card.className='scr-card';
      const initials=s.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
      card.innerHTML=`<div class="scr-badge" style="background:${s.color}">${esc(initials)}</div><div class="scr-body"><div class="scr-name">${esc(s.name)}</div>${s.desc?`<div class="scr-desc">${esc(s.desc)}</div>`:''}</div><button class="scr-run" data-id="${s.id}">Run</button>`;
      card.querySelector('.scr-run').addEventListener('click',e=>{e.stopPropagation();const sc=_scrMap.get(s.id);if(sc)run(sc.cmd,sc.name);});
      card.addEventListener('contextmenu',ev=>{ev.preventDefault();ev.stopPropagation();scrCtx(ev,s);});
      list.appendChild(card);
    });
  }catch(e){}
}

/* ── File Manager ── */
async function fsLoad(rel){
  fsCwd=rel||'';
  const list=document.getElementById('fs-list'),crumbs=document.getElementById('fs-crumbs');
  list.innerHTML='<div class="fs-msg">Loading\u2026</div>';
  try{
    const r=await fetch('/api/fs/list?path='+encodeURIComponent(fsCwd));
    if(!r.ok){list.innerHTML='<div class="fs-msg">Error loading directory.</div>';return;}
    const d=await r.json();
    if(d.error){list.innerHTML=`<div class="fs-msg">${esc(d.error)}</div>`;return;}
    crumbs.innerHTML='';
    const home=document.createElement('span');home.className='fs-crumb';home.textContent='~';home.onclick=()=>fsLoad('');crumbs.appendChild(home);
    (d.crumbs||[]).forEach((part,i,arr)=>{
      const sep=document.createElement('span');sep.className='fs-sep';sep.textContent='/';crumbs.appendChild(sep);
      const c=document.createElement('span');c.className='fs-crumb'+(i===arr.length-1?' active':'');c.textContent=part;
      if(i<arr.length-1){const nr=arr.slice(0,i+1).join('/');c.onclick=()=>fsLoad(nr);}
      crumbs.appendChild(c);
    });
    list.innerHTML='';
    if(!d.entries.length&&d.parent===null){list.innerHTML='<div class="fs-msg">Empty directory.</div>';return;}
    if(d.parent!==null){
      const up=document.createElement('div');up.className='fs-entry';
      up.innerHTML=`<span class="fs-icon dir">..</span><span class="fs-name">..</span>`;
      up.onclick=()=>fsLoad(d.parent==='.'?'':d.parent);
      list.appendChild(up);
    }
    d.entries.forEach(e=>{
      const row=document.createElement('div');row.className='fs-entry';
      const isDir=e.type==='dir';
      const ext=e.name.includes('.')?e.name.split('.').pop().toUpperCase().slice(0,3):'—';
      const relPath=fsCwd?fsCwd+'/'+e.name:e.name;
      const relEnc=encodeURIComponent(relPath);
      row.innerHTML=
        `<span class="fs-icon ${isDir?'dir':''}">${isDir?'DIR':ext}</span>`+
        `<span class="fs-name" title="${esc(e.name)} — ${e.modified}">${esc(e.name)}</span>`+
        `<span class="fs-size">${isDir?'':e.size}</span>`+
        `<span class="fs-acts">`+
          (!isDir?`<button class="fs-act dl" title="Download" onclick="fsDl(event,'${relEnc}','${esc(e.name)}')">`+
            `<svg viewBox="0 0 24 24"><path d="M12 3v13m-5-5 5 5 5-5"/><path d="M3 20h18"/></svg></button>`:'  ')+
          (e.editable?`<button class="fs-act" title="Edit" onclick="openEditor('${relPath}','${esc(e.name)}')">`+
            `<svg viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>`:'  ')+
        `</span>`;
      if(isDir)row.addEventListener('click',ev=>{if(!ev.target.classList.contains('fs-act'))fsLoad(relPath);});
      row.addEventListener('contextmenu',ev=>{ev.stopPropagation();fsCtx(ev,relPath,e);});
      list.appendChild(row);
    });
    setupDrop(list);
  }catch(e){list.innerHTML=`<div class="fs-msg">Error: ${esc(e.message)}</div>`;}
}
function fsReload(){fsLoad(fsCwd);}
function fsDl(ev,relEnc,name){ev.stopPropagation();const a=document.createElement('a');a.href='/api/fs/download?path='+relEnc;a.download=name;document.body.appendChild(a);a.click();document.body.removeChild(a);}
async function fsRm(ev,relEnc,name,skipStopProp){
  if(!skipStopProp)ev.stopPropagation();
  if(!confirm('Delete "'+name+'"? This cannot be undone.'))return;
  try{const r=await fetch('/api/fs/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:decodeURIComponent(relEnc)})});const d=await r.json();if(d.ok)fsReload();else alert('Delete failed: '+d.error);}
  catch(e){alert('Error: '+e.message);}
}
async function fsDuplicate(relPath){
  try{const r=await fetch('/api/fs/duplicate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:relPath})});const d=await r.json();if(d.ok)fsReload();else alert('Duplicate failed: '+d.error);}
  catch(e){alert('Error: '+e.message);}
}
async function fsUpload(input){
  if(!input.files.length)return;
  const fd=new FormData();fd.append('path',fsCwd);Array.from(input.files).forEach(f=>fd.append('files',f));
  try{const r=await fetch('/api/fs/upload',{method:'POST',body:fd});const d=await r.json();if(d.ok)fsReload();else alert('Upload failed: '+d.error);}
  catch(e){alert('Upload error: '+e.message);}
  input.value='';
}
async function fsMkdir(){
  const name=prompt('New folder name:');if(!name||!name.trim())return;
  try{const r=await fetch('/api/fs/mkdir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:fsCwd,name:name.trim()})});const d=await r.json();if(d.ok)fsReload();else alert('Failed: '+d.error);}
  catch(e){alert('Error: '+e.message);}
}
function setupDrop(el){
  el.ondragover=ev=>{ev.preventDefault();el.classList.add('fs-drop-active');};
  el.ondragleave=()=>el.classList.remove('fs-drop-active');
  el.ondrop=async ev=>{
    ev.preventDefault();el.classList.remove('fs-drop-active');
    const files=Array.from(ev.dataTransfer.files);if(!files.length)return;
    const fd=new FormData();fd.append('path',fsCwd);files.forEach(f=>fd.append('files',f));
    try{const r=await fetch('/api/fs/upload',{method:'POST',body:fd});const d=await r.json();if(d.ok)fsReload();}catch(e){}
  };
}

/* ── Sidebar ── */
function buildSidebar(){
  const nav=document.getElementById('cat-nav'),list=document.getElementById('cmd-list');
  nav.innerHTML='';list.innerHTML='';
  Object.entries(config.commands).forEach(([cat,cmds],i)=>{
    const t=document.createElement('button');t.className='cat-tab'+(i===0?' on':'');
    t.innerHTML='<span class="cat-dot"></span>'+esc(cat);
    t.onclick=()=>{document.querySelectorAll('.cat-tab').forEach(x=>x.classList.remove('on'));document.querySelectorAll('.sec').forEach(x=>x.classList.remove('on'));t.classList.add('on');document.getElementById('sec-'+i).classList.add('on');};
    nav.appendChild(t);
    const s=document.createElement('div');s.className='sec'+(i===0?' on':'');s.id='sec-'+i;
    cmds.forEach(c=>{
      const b=document.createElement('button');b.className='cbtn';
      b.innerHTML=esc(c.name)+'<span class="carr">&#9654;</span>';
      b.onclick=()=>run(c.cmd,c.name);
      b.addEventListener('contextmenu',ev=>cmdCtx(ev,c.cmd,c.name));
      s.appendChild(b);
    });
    list.appendChild(s);
  });
  const ag=document.getElementById('apps-grid');ag.innerHTML='';
  config.apps.forEach(a=>{
    const b=document.createElement('button');b.className='abtn';
    b.innerHTML='<span class="aico" style="background:'+a.color+'">'+esc(a.icon)+'</span><span class="aname">'+esc(a.name)+'</span>';
    b.onclick=()=>{if(a.name==='Reboot'||a.name==='Shutdown'||a.name==='Restart'){if(!confirm('Really '+a.name.toLowerCase()+'?'))return;}run(a.cmd,a.name);};
    ag.appendChild(b);
  });
}

/* ── Stats header ── */
async function refreshStats(){
  try{
    const r=await fetch('/api/status');if(!r.ok)return;
    const d=await r.json();
    document.getElementById('s-temp').textContent=d.temp||'—';
    document.getElementById('s-cpu').textContent=d.cpu||'—';
    document.getElementById('s-ram').textContent=d.ram||'—';
    document.getElementById('s-disk').textContent=d.disk||'—';
    const n=parseFloat(d.temp);
    document.getElementById('c-temp').className='chip'+(n>75?' hot':n>60?' warm':'');
  }catch(e){}
}

/* ── Live charts ── */
const MAX_PTS=100;
const _buf={cpu:[],ram:[],temp:[],disk:[]};
let _charts={},_stsTimer=null;
function makeChart(id,color){
  const ctx=document.getElementById(id).getContext('2d');
  return new Chart(ctx,{type:'line',data:{labels:Array(MAX_PTS).fill(''),datasets:[{data:[],borderColor:color,borderWidth:1.5,pointRadius:0,fill:true,backgroundColor:color+'22',tension:0.3}]},options:{animation:false,plugins:{legend:{display:false}},scales:{x:{display:false},y:{display:false,min:0,max:100}},responsive:true,maintainAspectRatio:false}});
}
function initCharts(){
  if(_charts.cpu)return;
  _charts.cpu  =makeChart('sts-cpu-chart',  '#3d9eff');
  _charts.ram  =makeChart('sts-ram-chart',  '#00e87a');
  _charts.temp =makeChart('sts-temp-chart', '#ffb300');
  _charts.disk =makeChart('sts-disk-chart', '#c41a4a');
}
async function pollStats(){
  try{
    const r=await fetch('/api/status');if(!r.ok)return;
    const d=await r.json();
    const push=(buf,val)=>{if(buf.length>=MAX_PTS)buf.shift();buf.push(val);};
    const pct=s=>parseFloat(s)||0;
    push(_buf.cpu,pct(d.cpu));push(_buf.ram,pct(d.ram));push(_buf.temp,pct(d.temp));push(_buf.disk,pct(d.disk));
    document.getElementById('sts-cpu-val').textContent=d.cpu||'—';
    document.getElementById('sts-ram-val').textContent=d.ram||'—';
    document.getElementById('sts-temp-val').textContent=d.temp||'—';
    document.getElementById('sts-disk-val').textContent=d.disk||'—';
    document.getElementById('sts-age').textContent=new Date().toLocaleTimeString();
    if(_charts.cpu){['cpu','ram','temp','disk'].forEach(k=>{_charts[k].data.datasets[0].data=[..._buf[k]];_charts[k].update('none');});}
  }catch(e){}
}

/* ── History search ── */
const ci=document.getElementById('ci');
const hs=document.getElementById('hist-search');
let hsIdx=-1;
function openHistSearch(){
  const q=ci.value.toLowerCase();
  const matches=q?hist.filter(h=>h.toLowerCase().includes(q)):hist;
  if(!matches.length){hs.innerHTML='<div class="hist-empty">No history yet</div>';hs.classList.add('show');return;}
  hs.innerHTML='';hsIdx=-1;
  matches.slice(0,12).forEach((h)=>{
    const item=document.createElement('div');item.className='hist-item';item.textContent=h;
    item.addEventListener('mousedown',e=>{e.preventDefault();ci.value=h;closeHistSearch();ci.focus();});
    hs.appendChild(item);
  });
  hs.classList.add('show');
}
function closeHistSearch(){hs.classList.remove('show');hsIdx=-1;}
function histMove(dir){
  const items=[...hs.querySelectorAll('.hist-item')];if(!items.length)return;
  if(hsIdx>=0)items[hsIdx].classList.remove('hi');
  hsIdx=(hsIdx+dir+items.length)%items.length;
  items[hsIdx].classList.add('hi');
  ci.value=items[hsIdx].textContent;
  hs.scrollTop=items[hsIdx].offsetTop-hs.clientHeight/2;
}
ci.addEventListener('keydown',e=>{
  if(e.ctrlKey&&e.key==='r'){e.preventDefault();openHistSearch();return;}
  if(hs.classList.contains('show')){
    if(e.key==='ArrowUp'){e.preventDefault();histMove(-1);return;}
    if(e.key==='ArrowDown'){e.preventDefault();histMove(1);return;}
    if(e.key==='Enter'){e.preventDefault();if(hsIdx>=0){ci.value=hs.querySelectorAll('.hist-item')[hsIdx].textContent;}closeHistSearch();runCustom();return;}
    if(e.key==='Escape'){closeHistSearch();return;}
  }
  if(e.key==='Enter'){runCustom();return;}
  if(e.key==='ArrowUp'){e.preventDefault();if(hidx<hist.length-1){hidx++;ci.value=hist[hidx];}}
  if(e.key==='ArrowDown'){e.preventDefault();if(hidx>0){hidx--;ci.value=hist[hidx];}else{hidx=-1;ci.value='';}}
});
document.addEventListener('click',e=>{if(!hs.contains(e.target)&&e.target!==ci)closeHistSearch();});

/* ── Run ── */
async function run(cmd,label){
  if(busy)return;busy=true;document.getElementById('rb').disabled=true;
  document.getElementById('lc').textContent=label||cmd;
  const out=document.getElementById('out');
  const es=out.querySelector('.empty');if(es)es.remove();
  const b=document.createElement('div');b.className='ob';
  b.innerHTML='<div class="oc"><span class="pr">$ </span>'+esc(cmd)+'</div><div class="os spin">running\u2026</div>';
  out.appendChild(b);out.scrollTop=out.scrollHeight;
  try{
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd})});
    const d=await r.json();
    const so=b.querySelector('.os');
    if(d.error){so.className='oe';so.textContent=d.error;}
    else{
      so.className='os';so.textContent=d.stdout||(d.stderr?'':'(no output)');
      if(d.stderr){const e=document.createElement('div');e.className='oe';e.textContent=d.stderr;b.appendChild(e);}
      const ex=document.createElement('div');ex.className=d.returncode===0?'ex0':'ex1';ex.textContent='exit '+d.returncode;b.appendChild(ex);
    }
  }catch(e){const so=b.querySelector('.os');so.className='oe';so.textContent='Request failed: '+e.message;}
  out.scrollTop=out.scrollHeight;busy=false;document.getElementById('rb').disabled=false;
}
function runCustom(){const cmd=ci.value.trim();if(!cmd)return;hist.unshift(cmd);hidx=-1;ci.value='';run(cmd);}
function clearOut(){document.getElementById('out').innerHTML='<div class="empty"><div class="big">~/pi</div><p>Pick a command or type one below</p></div>';document.getElementById('lc').textContent='—';}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════
if __name__ == '__main__':
    load_dashignore()
    if _ignore_patterns:
        print(f"  .dashignore: {len(_ignore_patterns)} rule(s) loaded")

    if not _CFG_FILE.exists():
        print('\n  ⚠  No config.json found.')
        print('  Run setup.py first:  python3 setup.py\n')

    try:    local_ip = socket.gethostbyname(socket.gethostname())
    except: local_ip = 'your-ip'
    pad = ' ' * max(0, 14 - len(local_ip))

    os_tag     = {'Linux': '🐧 Linux', 'Darwin': '🍎 macOS', 'Windows': '🪟 Windows'}.get(PLATFORM, PLATFORM)
    psutil_tag = '✓ psutil'   if _HAS_PSUTIL  else '✗ missing — run setup.py'
    win_tag    = '✓ pywebview' if _HAS_WEBVIEW else '✗ missing — run setup.py'

    print(f"""
╔═══════════════════════════════════════════╗
║      DevBoard  v10.0          ║
╠═══════════════════════════════════════════╣
║  Local:    http://localhost:{PORT}            ║
║  Network:  http://{local_ip}{pad}:{PORT}   ║
╠═══════════════════════════════════════════╣
║  Platform: {os_tag:<33}║
║  Auth:     {AUTH_MODE:<33}║
║  psutil:   {psutil_tag:<33}║
║  window:   {win_tag:<33}║
╠═══════════════════════════════════════════╣
║  Tabs: CMD · DKR · FLS · STS · SCR · PRT ║
║  Setup:  python3 setup.py                 ║
║  Remove: python3 uninstall.py             ║
╚═══════════════════════════════════════════╝
""")

    if _HAS_WEBVIEW and '--browser' not in sys.argv:
        # ── Native window mode ──────────────────────────────────────────
        # Flask runs on a background daemon thread; pywebview owns the
        # main thread (required on macOS/Windows for the UI event loop).
        import threading as _threading

        _flask_ready = _threading.Event()

        def _run_flask():
            # Suppress Flask's startup banner — we already printed ours
            import logging
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)
            app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

        flask_thread = _threading.Thread(target=_run_flask, daemon=True)
        flask_thread.start()

        # Poll until Flask is accepting connections (≤ 3 s)
        import socket as _sock
        for _ in range(30):
            try:
                with _sock.create_connection(('127.0.0.1', PORT), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)

        print(f'  Opening dashboard window (port {PORT})…\n'
              f'  Network URL: http://{local_ip}:{PORT}\n'
              f'  Ctrl+C or close the window to stop.\n')

        _webview.create_window(
            'DevBoard',
            f'http://localhost:{PORT}',
            width=1300, height=860,
            min_size=(960, 640),
            resizable=True,
            background_color='#080b10',
        )
        _webview.start(debug=False)
        # Window closed — Flask daemon thread exits with the process

    else:
        # ── Headless / browser mode ─────────────────────────────────────
        print(f'  Ctrl+C to stop.\n')
        app.run(host='0.0.0.0', port=PORT, debug=False)

