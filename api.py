"""Flask REST API exposed on localhost:5199 for the GUI."""

import csv
import io
import json
import os
import subprocess
import threading
from collections import deque
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request

import config

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

app = Flask(__name__)

# Shared state injected by daemon.py before starting the API thread
_state = {
    "serial_connected": False,
    "serial_port": "",
    "ots_connected": False,
    "ots_host": "",
    "ots_port": 0,
    "node_count": 0,
}

_traffic_log = deque(maxlen=500)
_log_lock = threading.Lock()

# Per-node telemetry store: {node_id: {battery, voltage, air_util, ch_util, uptime, snr, rssi, last_heard, callsign}}
_telemetry = {}
_telemetry_lock = threading.Lock()

# Airtime tracking
_airtime_current = 0.0
_airtime_peak    = 0.0
_airtime_lock    = threading.Lock()

# Bridge packet counters
_stats = {"mesh_rx": 0, "ots_rx": 0, "mesh_tx": 0, "ots_tx": 0}
_stats_lock = threading.Lock()

_serial_iface = None
_ots_client   = None


# ---------------------------------------------------------------------------
# Internal helpers (called by daemon)
# ---------------------------------------------------------------------------

def set_interfaces(serial_iface, ots_client):
    global _serial_iface, _ots_client
    _serial_iface = serial_iface
    _ots_client   = ots_client


def update_state(**kwargs):
    _state.update(kwargs)


def log_traffic(direction, summary, channel=None, portnum=None, node_id="", callsign=""):
    """direction: 'mesh→ots' or 'ots→mesh'"""
    now_utc = datetime.now(timezone.utc)
    entry = {
        "time":      now_utc.strftime("%H:%M:%S"),
        "direction": direction,
        "summary":   summary,
        "channel":   channel or "",
        "portnum":   portnum or "",
        "node_id":   node_id,
        "callsign":  callsign,
    }
    with _log_lock:
        _traffic_log.append(entry)
    with _stats_lock:
        if direction.startswith("mesh"):
            _stats["mesh_rx"] += 1
        else:
            _stats["ots_rx"] += 1
    # Persist to daily log file
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        date_str = now_utc.strftime("%Y-%m-%d")
        file_entry = dict(entry, time=now_utc.isoformat())
        with open(os.path.join(_LOG_DIR, f"{date_str}.jsonl"), "a") as fh:
            fh.write(json.dumps(file_entry) + "\n")
    except Exception as exc:
        app.logger.error("log_to_file failed: %s", exc)


def update_telemetry(node_id, callsign, data):
    """Called by daemon with parsed telemetry/nodeinfo/position data."""
    global _airtime_current, _airtime_peak
    with _telemetry_lock:
        entry = _telemetry.setdefault(node_id, {
            "node_id":   node_id,
            "callsign":  callsign,
            "battery":   None,
            "voltage":   None,
            "air_util":  None,
            "ch_util":   None,
            "uptime":    None,
            "snr":       None,
            "rssi":      None,
            "lat":       None,
            "lon":       None,
            "last_heard": None,
        })
        entry.update({k: v for k, v in data.items() if v is not None})
        entry["callsign"]   = callsign or entry["callsign"]
        entry["last_heard"] = datetime.now(timezone.utc).strftime("%H:%M:%S")

    # Track airtime peak across all nodes
    air = data.get("air_util")
    if air is not None:
        with _airtime_lock:
            _airtime_current = air
            if air > _airtime_peak:
                _airtime_peak = air


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/status")
def status():
    cfg = config.get()
    with _airtime_lock:
        cur = _airtime_current
        peak = _airtime_peak
    return jsonify({
        "serial_connected": _state["serial_connected"],
        "serial_port":      config.active_port(),
        "serial_mode":      cfg.get("serial_mode", "usb"),
        "ots_connected":    _state["ots_connected"],
        "ots_host":         cfg.get("ots_host"),
        "ots_port":         cfg.get("ots_port"),
        "node_count":       _state["node_count"],
        "airtime_current":  round(cur, 2),
        "airtime_peak":     round(peak, 2),
    })


@app.route("/traffic")
def traffic():
    limit = int(request.args.get("limit", 100))
    with _log_lock:
        entries = list(_traffic_log)[-limit:]
    return jsonify(entries)


@app.route("/telemetry")
def telemetry():
    with _telemetry_lock:
        return jsonify(list(_telemetry.values()))


@app.route("/config", methods=["GET"])
def get_config():
    return jsonify(config.get())


@app.route("/config", methods=["POST"])
def post_config():
    data = request.get_json(force=True)
    config.save(data)
    return jsonify({"ok": True})


@app.route("/nodes")
def nodes():
    result = []
    if _serial_iface:
        for n in _serial_iface.get_nodes():
            user = n.get("user", {})
            result.append({
                "id":        n.get("num"),
                "callsign":  user.get("longName") or user.get("shortName") or "?",
                "lastHeard": n.get("lastHeard"),
                "snr":       n.get("snr"),
            })
    return jsonify(result)


@app.route("/channels", methods=["GET"])
def get_channels():
    device_channels = []
    if _serial_iface:
        device_channels = _serial_iface.get_channels()
    cfg_channels = {ch["index"]: ch for ch in config.get().get("mesh_channels", [])}
    merged = []
    for dch in device_channels:
        idx = dch["index"]
        cfg = cfg_channels.get(idx, {})
        merged.append({
            "index":   idx,
            "name":    dch.get("name") or cfg.get("name") or f"Channel {idx}",
            "psk":     cfg.get("psk", "AQ=="),
            "enabled": cfg.get("enabled", idx == 0),
        })
    return jsonify(merged)


@app.route("/channels", methods=["POST"])
def post_channels():
    channels = request.get_json(force=True)
    cfg = config.get()
    cfg["mesh_channels"] = channels
    config.save(cfg)
    return jsonify({"ok": True})


@app.route("/service/restart", methods=["POST"])
def svc_restart():
    _run_systemctl("restart")
    return jsonify({"ok": True})


@app.route("/service/stop", methods=["POST"])
def svc_stop():
    _run_systemctl("stop")
    return jsonify({"ok": True})


@app.route("/service/start", methods=["POST"])
def svc_start():
    _run_systemctl("start")
    return jsonify({"ok": True})


@app.route("/sysinfo")
def sysinfo():
    result = {
        "cpu": None, "ram_percent": None,
        "ram_used_mb": None, "ram_total_mb": None,
        "uptime": None, "internet": None,
    }
    # CPU + RAM via psutil (preferred) or /proc fallback
    try:
        import psutil as _ps
        result["cpu"] = _ps.cpu_percent(interval=0.1)
        vm = _ps.virtual_memory()
        result["ram_percent"]  = round(vm.percent, 1)
        result["ram_used_mb"]  = vm.used  >> 20
        result["ram_total_mb"] = vm.total >> 20
    except ImportError:
        try:
            mem = {}
            with open("/proc/meminfo") as fh:
                for line in fh:
                    k, v = line.split(":")
                    mem[k.strip()] = int(v.strip().split()[0])
            total = mem.get("MemTotal", 0)
            avail = mem.get("MemAvailable", 0)
            used  = total - avail
            result["ram_used_mb"]  = used  // 1024
            result["ram_total_mb"] = total // 1024
            result["ram_percent"]  = round(used / total * 100, 1) if total else 0
        except Exception:
            pass
    # Pi uptime
    try:
        with open("/proc/uptime") as fh:
            result["uptime"] = int(float(fh.read().split()[0]))
    except Exception:
        pass
    # Internet (quick socket probe, 0.5s timeout)
    try:
        import socket as _sock
        conn = _sock.create_connection(("8.8.8.8", 53), timeout=0.5)
        conn.close()
        result["internet"] = True
    except Exception:
        result["internet"] = False
    return jsonify(result)


@app.route("/stats")
def stats():
    with _stats_lock:
        return jsonify(dict(_stats))


@app.route("/mesh/info")
def mesh_info():
    if _serial_iface:
        info = _serial_iface.get_info()
        if info:
            return jsonify(info)
    return jsonify({"error": "not connected"}), 503


@app.route("/mesh/config", methods=["GET"])
def mesh_config_get():
    if _serial_iface:
        cfg = _serial_iface.get_local_config()
        if cfg:
            return jsonify(cfg)
    return jsonify({"error": "not connected"}), 503


@app.route("/mesh/config/<section>", methods=["POST"])
def mesh_config_set(section):
    updates = request.get_json(force=True)
    if not _serial_iface:
        return jsonify({"error": "not connected"}), 503
    ok = _serial_iface.set_local_config(section, updates)
    return jsonify({"ok": ok})


@app.route("/mesh/owner", methods=["POST"])
def mesh_owner():
    data = request.get_json(force=True)
    if not _serial_iface:
        return jsonify({"error": "not connected"}), 503
    ok = _serial_iface.set_owner(data.get("long_name", ""), data.get("short_name", ""))
    return jsonify({"ok": ok})


@app.route("/mesh/send_text", methods=["POST"])
def mesh_send_text():
    data    = request.get_json(force=True)
    text    = data.get("text", "").strip()
    channel = int(data.get("channel", 0))
    if not text:
        return jsonify({"error": "empty message"}), 400
    if not _serial_iface:
        return jsonify({"error": "not connected"}), 503
    ok = _serial_iface.send_text(text, channel_index=channel)
    if ok:
        log_traffic("mesh→ots", f'[TEST MSG] "{text}"',
                    portnum="TEXT_MESSAGE_APP", channel=f"Ch{channel}")
    return jsonify({"ok": ok})


@app.route("/mesh/reboot", methods=["POST"])
def mesh_reboot():
    if not _serial_iface:
        return jsonify({"error": "not connected"}), 503
    ok = _serial_iface.reboot_node()
    return jsonify({"ok": ok})


@app.route("/mesh/shutdown", methods=["POST"])
def mesh_shutdown():
    if not _serial_iface:
        return jsonify({"error": "not connected"}), 503
    ok = _serial_iface.shutdown_node()
    return jsonify({"ok": ok})


@app.route("/journal")
def journal():
    lines = int(request.args.get("lines", 200))
    try:
        result = subprocess.run(
            ["journalctl", "-u", "mesh-ots-bridge",
             f"-n{lines}", "--no-pager", "-o", "short"],
            capture_output=True, text=True, timeout=5,
        )
        return jsonify({"lines": result.stdout.splitlines()})
    except Exception as exc:
        return jsonify({"lines": [], "error": str(exc)})


@app.route("/datalog")
def datalog():
    limit  = int(request.args.get("limit", 500))
    date   = request.args.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    portnum_filter = request.args.get("portnum", "")
    path   = os.path.join(_LOG_DIR, f"{date}.jsonl")
    entries = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if portnum_filter and e.get("portnum") != portnum_filter:
                    continue
                entries.append(e)
    except FileNotFoundError:
        pass
    return jsonify(entries[-limit:])


@app.route("/datalog/dates")
def datalog_dates():
    try:
        dates = sorted(
            f[:-6] for f in os.listdir(_LOG_DIR) if f.endswith(".jsonl")
        )
    except FileNotFoundError:
        dates = []
    return jsonify(dates)


@app.route("/datalog/export")
def datalog_export():
    date = request.args.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    path = os.path.join(_LOG_DIR, f"{date}.jsonl")
    entries = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except FileNotFoundError:
        pass
    fields = ["time", "direction", "node_id", "callsign", "portnum", "channel", "summary"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(entries)
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=mesh-log-{date}.csv"},
    )


def _run_systemctl(action):
    try:
        subprocess.run(
            ["sudo", "systemctl", action, "mesh-ots-bridge"],
            check=True, timeout=10,
            capture_output=True,
        )
    except Exception as e:
        app.logger.error("systemctl %s failed: %s", action, e)


def start(host="127.0.0.1", port=5199):
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, use_reloader=False),
        daemon=True,
        name="flask-api",
    )
    t.start()
