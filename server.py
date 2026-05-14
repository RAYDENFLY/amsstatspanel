#!/usr/bin/env python3
"""
AMSSTUDIO PANEL — Backend API
Jalankan: python3 server.py
Endpoint: http://localhost:5000/api/stats
"""

import json, os, re, time, subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ── Config ────────────────────────────────────────────────────
PORT        = 5000
IFACE       = "wlp1s0"          # ganti jika pakai eth0 atau enp1s0
DISK_MOUNT  = "/"
CACHE_TTL   = 2                 # detik cache (jangan terlalu sering baca disk)

# ── Cache ─────────────────────────────────────────────────────
_cache = {"ts": 0, "data": None}
_net_prev = {"ts": 0, "rx": 0, "tx": 0}

# ══════════════════════════════════════════════════════════════
# COLLECTORS
# ══════════════════════════════════════════════════════════════

def read_file(path, default=""):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return default

def cmd(args, default=""):
    try:
        return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return default

# ── CPU ───────────────────────────────────────────────────────
def get_cpu():
    # model
    cpuinfo = read_file("/proc/cpuinfo")
    model = "Unknown"
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break

    cores_str = cmd(["nproc"])
    cores = int(cores_str) if cores_str.isdigit() else 2

    freq = "0.00"
    freq_file = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
    if os.path.exists(freq_file):
        try:
            freq = f"{int(read_file(freq_file)) / 1_000_000:.2f}"
        except Exception:
            pass

    # usage via /proc/stat (2 reads, 200ms apart)
    def read_stat():
        line = [l for l in read_file("/proc/stat").splitlines() if l.startswith("cpu ")][0]
        vals = list(map(int, line.split()[1:]))
        idle = vals[3] + vals[4]
        total = sum(vals)
        return total, idle

    t1, i1 = read_stat()
    time.sleep(0.2)
    t2, i2 = read_stat()
    usage = round(100 * (1 - (i2 - i1) / max(t2 - t1, 1)), 1)

    # temperature
    temp = 0.0
    for pattern in [
        "/sys/class/thermal/thermal_zone*/temp",
        "/sys/class/hwmon/hwmon*/temp1_input",
    ]:
        import glob
        paths = glob.glob(pattern)
        if paths:
            try:
                temp = int(read_file(paths[0])) / 1000.0
                break
            except Exception:
                pass

    # load average
    load = read_file("/proc/loadavg", "0 0 0").split()[:3]
    load_avg = [float(x) for x in load]

    return {
        "model": model,
        "cores": cores,
        "freq_ghz": float(freq),
        "usage_pct": usage,
        "temp_c": round(temp, 1),
        "load_avg": load_avg,
    }

# ── Memory ────────────────────────────────────────────────────
def get_memory():
    meminfo = {}
    for line in read_file("/proc/meminfo").splitlines():
        k, v = line.split(":", 1)
        meminfo[k.strip()] = int(v.strip().split()[0])  # kB

    total  = meminfo.get("MemTotal", 0)
    avail  = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    used   = total - avail
    st     = meminfo.get("SwapTotal", 0)
    sf     = meminfo.get("SwapFree", 0)
    su     = st - sf

    return {
        "used_mib":       round(used   / 1024, 2),
        "total_mib":      round(total  / 1024, 2),
        "swap_used_mib":  round(su     / 1024, 2),
        "swap_total_mib": round(st     / 1024, 2),
    }

# ── Disk ──────────────────────────────────────────────────────
def get_disk():
    st = os.statvfs(DISK_MOUNT)
    total_b = st.f_blocks * st.f_frsize
    free_b  = st.f_bfree  * st.f_frsize
    used_b  = total_b - free_b

    # disk temp via smartctl (needs sudo / hddtemp fallback)
    temp = 0.0
    try:
        out = subprocess.check_output(
            ["smartctl", "-A", "/dev/sda"], stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            if "Temperature_Celsius" in line or "194 " in line:
                temp = float(line.split()[-1])
                break
    except Exception:
        pass

    # fstype
    fs = "ext4"
    try:
        mounts = read_file("/proc/mounts")
        for line in mounts.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1] == DISK_MOUNT:
                fs = parts[2]
                break
    except Exception:
        pass

    return {
        "used_gib":  round(used_b  / (1024**3), 2),
        "total_gib": round(total_b / (1024**3), 2),
        "temp_c":    temp,
        "mount":     DISK_MOUNT,
        "fs":        fs,
    }

# ── Network ───────────────────────────────────────────────────
def get_network():
    global _net_prev

    def read_net(iface, key):
        p = f"/sys/class/net/{iface}/statistics/{key}"
        return int(read_file(p, "0"))

    now = time.time()
    rx  = read_net(IFACE, "rx_bytes")
    tx  = read_net(IFACE, "tx_bytes")

    dt = now - _net_prev["ts"] if _net_prev["ts"] else 1
    up_bps = (tx - _net_prev["tx"]) / dt if _net_prev["tx"] else 0
    dn_bps = (rx - _net_prev["rx"]) / dt if _net_prev["rx"] else 0
    _net_prev = {"ts": now, "rx": rx, "tx": tx}

    # local IP
    ip = cmd(["ip", "-4", "addr", "show", IFACE])
    local_ip = "--"
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", ip)
    if m:
        local_ip = m.group(1)

    return {
        "iface":           IFACE,
        "local_ip":        local_ip,
        "up_bps":          max(0, up_bps),
        "dn_bps":          max(0, dn_bps),
        "total_up_bytes":  tx,
        "total_dn_bytes":  rx,
    }

# ── Battery ───────────────────────────────────────────────────
def get_battery():
    base = "/sys/class/power_supply"
    bat_path = None
    for d in (os.listdir(base) if os.path.isdir(base) else []):
        if d.startswith("BAT"):
            bat_path = os.path.join(base, d)
            break

    if not bat_path:
        return {"pct": 0, "status": "N/A", "plugged": False}

    pct    = int(read_file(f"{bat_path}/capacity", "0"))
    status = read_file(f"{bat_path}/status", "Unknown")  # Charging / Discharging / Full
    plugged = status in ("Charging", "Full")

    return {"pct": pct, "status": status, "plugged": plugged}

# ── System ────────────────────────────────────────────────────
def get_system():
    hostname = read_file("/etc/hostname") or cmd(["hostname"])

    # OS
    os_name = "Linux"
    for f in ["/etc/os-release", "/usr/lib/os-release"]:
        if os.path.exists(f):
            for line in read_file(f).splitlines():
                if line.startswith("PRETTY_NAME="):
                    os_name = line.split("=", 1)[1].strip().strip('"')
                    break
            break

    kernel = cmd(["uname", "-r"])
    kernel_full = "Linux " + kernel if kernel else "Linux"

    # uptime
    uptime_sec = int(float(read_file("/proc/uptime", "0").split()[0]))

    # packages
    pkgs = "N/A"
    try:
        out = cmd(["dpkg", "--get-selections"])
        pkgs = str(len([l for l in out.splitlines() if "install" in l])) + " (dpkg)"
    except Exception:
        pass

    # shell
    shell = os.environ.get("SHELL", "/bin/bash")
    shell_name = os.path.basename(shell)
    shell_ver = cmd([shell, "--version"])
    shell_ver_short = shell_ver.split("\n")[0] if shell_ver else shell_name

    # GPU
    gpu = "N/A"
    lspci_out = cmd(["lspci"])
    for line in lspci_out.splitlines():
        if "VGA" in line or "Display" in line or "3D" in line:
            gpu = line.split(":", 2)[-1].strip()
            break

    # display
    res = "N/A"
    hz  = "N/A"
    xdpyinfo = cmd(["xdpyinfo"])
    m = re.search(r"dimensions:\s+(\d+x\d+)", xdpyinfo)
    if m:
        res = m.group(1).replace("x", "×")

    return {
        "hostname":    hostname,
        "os":          os_name,
        "kernel":      kernel_full,
        "uptime_sec":  uptime_sec,
        "packages":    pkgs,
        "shell":       shell_ver_short,
        "gpu":         gpu,
        "display_res": res,
        "display_hz":  hz,
    }

# ── Processes ─────────────────────────────────────────────────
def get_processes():
    procs = []

    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue

            try:
                cmdline = read_file(f"/proc/{pid}/cmdline")
                statm   = read_file(f"/proc/{pid}/statm")
                stat    = read_file(f"/proc/{pid}/stat")

                if not cmdline:
                    continue

                name = os.path.basename(cmdline.split("\x00")[0])[:16]

                # memory
                mem_pages = int(statm.split()[1])
                mem_mb = round((mem_pages * 4096) / 1024 / 1024, 1)

                # cpu ticks
                parts = stat.split()
                utime = int(parts[13])
                stime = int(parts[14])
                cpu = round((utime + stime) / 100, 1)

                procs.append({
                    "name": name,
                    "cpu": cpu,
                    "mem_mb": mem_mb,
                })

            except Exception:
                continue

        procs.sort(key=lambda x: x["cpu"], reverse=True)

    except Exception as e:
        print("process error:", e)

    return procs[:6]

# ── Main collector ────────────────────────────────────────────
def collect():
    global _cache
    if time.time() - _cache["ts"] < CACHE_TTL and _cache["data"]:
        return _cache["data"]

    data = {
        "cpu":       get_cpu(),
        "memory":    get_memory(),
        "disk":      get_disk(),
        "network":   get_network(),
        "battery":   get_battery(),
        "system":    get_system(),
        "processes": get_processes(),
        "ts":        int(time.time()),
    }
    _cache = {"ts": time.time(), "data": data}
    return data

# ══════════════════════════════════════════════════════════════
# HTTP SERVER
# ══════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress access log

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/stats":
            try:
                data = collect()
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self.send_response(500)
                self._cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        elif self.path in ("/", "/index.html"):
            # Serve the HTML panel directly
            html_path = Path(__file__).parent / "amsstudio-panel.html"
            if html_path.exists():
                body = html_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

if __name__ == "__main__":
    print(f"╔══════════════════════════════════╗")
    print(f"║   AMSSTUDIO PANEL — Backend API  ║")
    print(f"╚══════════════════════════════════╝")
    print(f"  Listening on http://0.0.0.0:{PORT}")
    print(f"  Endpoint : http://localhost:{PORT}/api/stats")
    print(f"  Panel    : http://localhost:{PORT}/")
    print(f"  Interface: {IFACE}")
    print(f"  Disk     : {DISK_MOUNT}")
    print(f"  Press Ctrl+C to stop\n")

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")