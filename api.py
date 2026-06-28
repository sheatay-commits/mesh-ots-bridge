"""Flask REST API exposed on localhost:5199 for the GUI."""

import subprocess
import threading
from collections import deque
from datetime import datetime, timezone

from flask import Flask, jsonify, request

import config

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


def log_traffic(direction, summary, channel=None, portnum=None):
    """direction: 'mesh→ots' or 'ots→mesh'"""
    with _log_lock:
        _traffic_log.append({
            "time":      datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "direction": direction,
            "summary":   summary,
            "channel":   channel,
            "portnum":   portnum or "",
        })


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
