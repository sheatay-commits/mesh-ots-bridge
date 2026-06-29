#!/usr/bin/env python3
"""
mesh-ots-bridge daemon
Bridges Meshtastic serial device ↔ OpenTakServer TCP CoT stream.
Runs as a systemd service; exposes a Flask REST API on localhost:5199
for the GUI.
"""

import logging
import signal
import sys
import time

import api
import config
import cot
from ots_client import OTSClient
from serial_iface import SerialIface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("daemon")

_serial: SerialIface = None
_ots: OTSClient = None


# ---------------------------------------------------------------------------
# Meshtastic → OTS
# ---------------------------------------------------------------------------

def on_mesh_receive(packet):
    cfg     = config.get()
    enabled = config.enabled_channel_indices()
    filters = cfg.get("packet_filters", {})

    channel_idx = packet.get("channel", 0)
    if channel_idx not in enabled:
        return

    ch_name  = _channel_name(channel_idx, cfg)
    decoded  = packet.get("decoded", {})
    portnum  = decoded.get("portnum", "")
    node_id  = packet.get("fromId", "unknown")
    callsign = _resolve_callsign(node_id)

    # Always update telemetry store regardless of filter (for GUI display)
    _handle_telemetry_update(packet, node_id, callsign, decoded, portnum)

    xml     = None
    summary = None

    if portnum == "POSITION_APP" and filters.get("position", True):
        xml     = cot.position_to_cot(packet, callsign)
        summary = f"PLI from {callsign}"

    elif portnum == "TEXT_MESSAGE_APP" and filters.get("text", True):
        text    = decoded.get("text", "")
        xml     = cot.text_to_cot(packet, callsign, text)
        summary = f'Text "{text[:40]}" from {callsign}'

    elif portnum == "TELEMETRY_APP" and filters.get("telemetry", True):
        summary = _telemetry_summary(decoded, callsign)

    elif portnum == "NODEINFO_APP" and filters.get("nodeinfo", True):
        user    = decoded.get("user", {})
        summary = f"NodeInfo: {user.get('longName') or user.get('shortName') or node_id}"

    elif portnum == "ATAK_PLUGIN":
        xml, summary = cot.atak_plugin_to_cot(packet, decoded)

    elif portnum == "ATAK_FORWARDER":
        xml, summary = cot.atak_forwarder_to_cot(packet, decoded)

    elif portnum and filters.get("other", True):
        # Catch-all: routing pings, range tests, admin, etc. — still shows "last heard"
        label = portnum.replace("_APP", "").replace("_", " ").title()
        summary = f"{label} from {callsign}"

    if summary:
        api.log_traffic("mesh→ots", summary, channel=ch_name, portnum=portnum,
                        node_id=node_id, callsign=callsign)
        logger.debug("mesh→ots [ch%d] %s", channel_idx, summary)

    if xml and _ots:
        _ots.send_cot(xml)


def _handle_telemetry_update(packet, node_id, callsign, decoded, portnum):
    """Extract metrics from any packet type and push to the telemetry store."""
    data = {}

    if portnum == "TELEMETRY_APP":
        metrics = decoded.get("telemetry", {}).get("deviceMetrics", {})
        env     = decoded.get("telemetry", {}).get("environmentMetrics", {})
        data = {
            "battery":  metrics.get("batteryLevel"),
            "voltage":  metrics.get("voltage"),
            "air_util": metrics.get("airUtilTx"),
            "ch_util":  metrics.get("channelUtilization"),
            "uptime":   metrics.get("uptimeSeconds"),
            "temp":     env.get("temperature"),
        }

    elif portnum == "POSITION_APP":
        pos = decoded.get("position", {})
        data = {
            "lat": pos.get("latitudeI", 0) / 1e7 if pos.get("latitudeI") else None,
            "lon": pos.get("longitudeI", 0) / 1e7 if pos.get("longitudeI") else None,
        }

    elif portnum == "NODEINFO_APP":
        user = decoded.get("user", {})
        callsign = user.get("longName") or user.get("shortName") or callsign

    # SNR/RSSI come on the packet wrapper for any received packet
    data["snr"]  = packet.get("rxSnr")
    data["rssi"] = packet.get("rxRssi")

    if any(v is not None for v in data.values()):
        api.update_telemetry(node_id, callsign, data)


def _telemetry_summary(decoded, callsign):
    metrics = decoded.get("telemetry", {}).get("deviceMetrics", {})
    bat     = metrics.get("batteryLevel")
    air     = metrics.get("airUtilTx")
    parts   = [f"Telemetry from {callsign}"]
    if bat  is not None: parts.append(f"bat={bat}%")
    if air  is not None: parts.append(f"air={air:.1f}%")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# OTS → Meshtastic
# ---------------------------------------------------------------------------

def on_ots_receive(xml_str):
    cfg              = config.get()
    allowed_prefixes = cfg.get("cot_types_allowed", ["a-f-", "b-t-f", "b-m-p-"])
    filters          = cfg.get("packet_filters", {})
    callsign         = cfg.get("callsign", "MESH-GW")
    enabled          = config.enabled_channel_indices()

    parsed = cot.parse_cot(xml_str)
    if not parsed:
        return

    cot_type = parsed["raw_type"]
    if not cot.is_allowed(cot_type, allowed_prefixes):
        return

    summary = None

    if cot_type.startswith("a-f-") and filters.get("position", True):
        lat, lon, alt = parsed["lat"], parsed["lon"], parsed["alt"]
        for ch_idx in enabled:
            _serial.send_position(lat, lon, alt, channel_index=ch_idx)
        summary = f"PLI → mesh ({lat:.4f},{lon:.4f})"

    elif cot_type.startswith("b-t-f") and filters.get("text", True):
        text = parsed.get("text") or ""
        if text:
            for ch_idx in enabled:
                _serial.send_text(text, channel_index=ch_idx)
            summary = f'GeoChat "{text[:40]}" → mesh'

    elif cot_type.startswith("b-m-p-") and filters.get("markers", True):
        marker_text = cot.cot_to_marker_text(parsed, callsign)
        for ch_idx in enabled:
            _serial.send_text(marker_text, channel_index=ch_idx)
        summary = f"Marker {parsed.get('marker_name', '')} → mesh"

    if summary:
        api.log_traffic("ots→mesh", summary)
        logger.debug("ots→mesh %s", summary)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_callsign(node_id):
    if _serial:
        for n in _serial.get_nodes():
            user = n.get("user", {})
            if n.get("num") == int(node_id.lstrip("!"), 16):
                return user.get("longName") or user.get("shortName") or node_id
    return node_id


def _channel_name(idx, cfg):
    for ch in cfg.get("mesh_channels", []):
        if ch["index"] == idx:
            return ch.get("name", f"Ch{idx}")
    return f"Ch{idx}"


def _update_status():
    while True:
        node_count = len(_serial.get_nodes()) if _serial else 0
        api.update_state(
            serial_connected=_serial.is_connected if _serial else False,
            ots_connected=_ots.is_connected if _ots else False,
            node_count=node_count,
        )
        time.sleep(5)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global _serial, _ots

    cfg = config.load()
    logger.info("Bridge starting. Serial mode: %s, OTS: %s:%s",
                cfg["serial_mode"], cfg["ots_host"], cfg["ots_port"])

    _serial = SerialIface(
        port=config.active_port(),
        on_receive=on_mesh_receive,
        on_connect=lambda: api.update_state(serial_connected=True),
        on_disconnect=lambda: api.update_state(serial_connected=False),
    )

    _ots = OTSClient(
        host=cfg["ots_host"],
        port=cfg["ots_port"],
        use_ssl=cfg.get("ots_ssl", False),
        on_receive=on_ots_receive,
        on_connect=lambda: api.update_state(ots_connected=True),
        on_disconnect=lambda: api.update_state(ots_connected=False),
    )

    api.set_interfaces(_serial, _ots)
    api.start()

    _serial.start()
    _ots.start()

    import threading
    threading.Thread(target=_update_status, daemon=True).start()

    def shutdown(sig, frame):
        logger.info("Shutting down...")
        _serial.stop()
        _ots.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Bridge running. API on localhost:5199")
    signal.pause()


if __name__ == "__main__":
    main()
