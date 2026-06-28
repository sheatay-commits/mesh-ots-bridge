import json
import os
import threading

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

_lock = threading.Lock()
_config = {}


def load():
    global _config
    with _lock:
        with open(CONFIG_PATH, "r") as f:
            _config = json.load(f)
    return _config


def get():
    with _lock:
        return dict(_config)


def save(data):
    global _config
    with _lock:
        _config = data
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)


def update(partial):
    with _lock:
        _config.update(partial)
        with open(CONFIG_PATH, "w") as f:
            json.dump(_config, f, indent=2)


def active_port():
    cfg = get()
    mode = cfg.get("serial_mode", "usb")
    if mode == "uart":
        return cfg.get("serial_port_uart", "/dev/ttyAMA0")
    if mode == "tcp":
        return cfg.get("serial_port_tcp", "localhost:4403")
    return cfg.get("serial_port_usb", "/dev/ttyUSB0")


def enabled_channel_indices():
    cfg = get()
    return {ch["index"] for ch in cfg.get("mesh_channels", []) if ch.get("enabled")}
