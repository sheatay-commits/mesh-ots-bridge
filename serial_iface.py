"""Meshtastic serial connection wrapper with auto-reconnect."""

import logging
import threading
import time
import traceback

logger = logging.getLogger(__name__)

try:
    import meshtastic.serial_interface
    import meshtastic.tcp_interface
    from pubsub import pub
    _MESHTASTIC_AVAILABLE = True
except ImportError:
    _MESHTASTIC_AVAILABLE = False
    logger.warning("meshtastic package not installed")


class SerialIface:
    """
    Connects to a Meshtastic device via one of three modes:
      - 'usb'  / 'uart' : serial port path e.g. /dev/ttyUSB0, /dev/ttyAMA0
      - 'tcp'            : TCP to meshtasticd, port is 'host:port' e.g. 'localhost:4403'

    Connection detection does NOT use pubsub events — it checks iface.myInfo
    directly after init, which is reliable across firmware versions.
    """

    def __init__(self, port, on_receive, on_connect=None, on_disconnect=None):
        self.port = port
        self._on_receive = on_receive
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

        self._iface = None
        self._connected = False
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self._backoff_seq = [5, 5, 10, 10]
        self._backoff_idx = 0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def is_connected(self):
        return self._connected

    def start(self):
        t = threading.Thread(target=self._connect_loop, daemon=True)
        t.start()

    def stop(self):
        self._stop.set()
        self._close()

    def send_text(self, text, channel_index=0):
        with self._lock:
            if self._iface and self._connected:
                try:
                    self._iface.sendText(text, channelIndex=channel_index)
                    return True
                except Exception as e:
                    logger.error("sendText failed: %s", e)
        return False

    def send_position(self, lat, lon, alt=0, channel_index=0):
        with self._lock:
            if self._iface and self._connected:
                try:
                    self._iface.sendPosition(lat, lon, alt, channelIndex=channel_index)
                    return True
                except Exception as e:
                    logger.error("sendPosition failed: %s", e)
        return False

    def get_nodes(self):
        with self._lock:
            if self._iface and self._connected:
                try:
                    return list(self._iface.nodes.values()) if self._iface.nodes else []
                except Exception:
                    pass
        return []

    def get_info(self):
        """Return identity + live metrics for the locally connected node."""
        with self._lock:
            if not self._iface or not self._connected:
                return None
            try:
                my_num = self._iface.myInfo.my_node_num
                user = {}
                node_entry = {}
                if self._iface.nodes:
                    for n in self._iface.nodes.values():
                        if n.get("num") == my_num:
                            user = n.get("user", {})
                            node_entry = n
                            break
                meta = getattr(self._iface, "metadata", None)
                dm = node_entry.get("deviceMetrics", {})
                return {
                    "node_id":    f"!{my_num:08x}",
                    "long_name":  user.get("longName", ""),
                    "short_name": user.get("shortName", ""),
                    "hw_model":   user.get("hwModel", ""),
                    "firmware":   meta.firmware_version if meta else "",
                    "battery":    dm.get("batteryLevel"),
                    "voltage":    dm.get("voltage"),
                    "uptime":     dm.get("uptimeSeconds"),
                    "air_util":   dm.get("airUtilTx"),
                    "snr":        node_entry.get("snr"),
                }
            except Exception as e:
                logger.error("get_info failed: %s", e)
                return None

    def get_local_config(self):
        """Return all writable localConfig fields as a plain dict."""
        with self._lock:
            if not self._iface or not self._connected:
                return None
            try:
                cfg = self._iface.localNode.localConfig
                lora = cfg.lora
                dev  = cfg.device
                pos  = cfg.position
                pwr  = cfg.power
                bt   = cfg.bluetooth
                disp = cfg.display

                def _enum_name(pb_enum, val):
                    try:
                        return pb_enum.Name(val)
                    except Exception:
                        return str(val)

                try:
                    from meshtastic.protobuf import config_pb2 as _cpb
                except ImportError:
                    try:
                        from meshtastic import config_pb2 as _cpb
                    except ImportError:
                        _cpb = None

                def _rn(v):
                    return _enum_name(_cpb.Config.LoRaConfig.RegionCode,  v) if _cpb else str(v)
                def _mn(v):
                    return _enum_name(_cpb.Config.LoRaConfig.ModemPreset, v) if _cpb else str(v)
                def _dn(v):
                    return _enum_name(_cpb.Config.DeviceConfig.Role,      v) if _cpb else str(v)
                def _bn(v):
                    return _enum_name(_cpb.Config.BluetoothConfig.PairingMode, v) if _cpb else str(v)

                return {
                    "lora": {
                        "region":       lora.region, "region_name": _rn(lora.region),
                        "modem_preset": lora.modem_preset, "modem_preset_name": _mn(lora.modem_preset),
                        "hop_limit":    lora.hop_limit,
                        "tx_power":     lora.tx_power,
                        "use_preset":   lora.use_preset,
                        "bandwidth":    lora.bandwidth,
                        "spread_factor":lora.spread_factor,
                        "coding_rate":  lora.coding_rate,
                    },
                    "device": {
                        "role": dev.role, "role_name": _dn(dev.role),
                        "serial_enabled":    dev.serial_enabled,
                        "debug_log_enabled": dev.debug_log_enabled,
                        "rebroadcast_mode":  dev.rebroadcast_mode,
                    },
                    "position": {
                        "gps_enabled":                pos.gps_enabled,
                        "gps_update_interval":        pos.gps_update_interval,
                        "position_broadcast_secs":    pos.position_broadcast_secs,
                        "smart_position_enabled":     pos.smart_position_enabled,
                        "broadcast_smart_minimum_interval_secs":
                            pos.position_broadcast_smart_minimum_interval_secs,
                        "broadcast_smart_minimum_distance":
                            pos.position_broadcast_smart_minimum_distance,
                    },
                    "power": {
                        "is_power_saving":              pwr.is_power_saving,
                        "on_battery_shutdown_after_secs": pwr.on_battery_shutdown_after_secs,
                        "wait_bluetooth_secs":          pwr.wait_bluetooth_secs,
                        "ls_secs":                      pwr.ls_secs,
                        "min_wake_secs":                pwr.min_wake_secs,
                    },
                    "bluetooth": {
                        "enabled":   bt.enabled,
                        "mode":      bt.mode, "mode_name": _bn(bt.mode),
                        "fixed_pin": bt.fixed_pin,
                    },
                    "display": {
                        "screen_on_secs":              disp.screen_on_secs,
                        "auto_screen_carousel_secs":   disp.auto_screen_carousel_secs,
                        "flip_screen":                 disp.flip_screen,
                        "units":                       disp.units,
                    },
                }
            except Exception as e:
                logger.error("get_local_config failed: %s", e)
                return None

    def set_local_config(self, section, updates):
        """Write a subset of fields in one localConfig section and push to device."""
        with self._lock:
            if not self._iface or not self._connected:
                return False
            try:
                sec = getattr(self._iface.localNode.localConfig, section)
                for field, value in updates.items():
                    if hasattr(sec, field):
                        setattr(sec, field, value)
                self._iface.localNode.writeConfig(section)
                return True
            except Exception as e:
                logger.error("set_local_config(%s) failed: %s", section, e)
                return False

    def set_owner(self, long_name, short_name):
        with self._lock:
            if not self._iface or not self._connected:
                return False
            try:
                self._iface.localNode.setOwner(long_name=long_name, short_name=short_name)
                return True
            except Exception as e:
                logger.error("set_owner failed: %s", e)
                return False

    def reboot_node(self):
        with self._lock:
            if not self._iface or not self._connected:
                return False
            try:
                self._iface.localNode.reboot()
                return True
            except Exception as e:
                logger.error("reboot_node failed: %s", e)
                return False

    def shutdown_node(self):
        with self._lock:
            if not self._iface or not self._connected:
                return False
            try:
                self._iface.localNode.shutdown()
                return True
            except Exception as e:
                logger.error("shutdown_node failed: %s", e)
                return False

    def get_channels(self):
        with self._lock:
            if self._iface and self._connected:
                try:
                    node = self._iface.localNode
                    channels = []
                    for i in range(8):
                        ch = node.getChannelByChannelIndex(i)
                        if ch and ch.role != 0:
                            channels.append({
                                "index": i,
                                "name": ch.settings.name or f"Channel {i}",
                                "role": ch.role,
                            })
                    return channels
                except Exception as e:
                    logger.error("get_channels failed: %s", e)
        return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect_loop(self):
        while not self._stop.is_set():
            try:
                self._connect()
                self._backoff_idx = 0
                self._watch()          # blocks until disconnected or stopped
            except Exception:
                logger.error("Connection error:\n%s", traceback.format_exc())
            finally:
                self._close()

            if self._stop.is_set():
                break
            delay = self._backoff_seq[min(self._backoff_idx, len(self._backoff_seq) - 1)]
            self._backoff_idx += 1
            logger.info("Reconnecting in %ds...", delay)
            self._stop.wait(delay)

    def _connect(self):
        if not _MESHTASTIC_AVAILABLE:
            raise RuntimeError("meshtastic not installed")

        logger.info("Connecting to Meshtastic on %s", self.port)

        if ":" in self.port and not self.port.startswith("/"):
            host, _, tcp_port = self.port.partition(":")
            iface = meshtastic.tcp_interface.TCPInterface(
                hostname=host, portNumber=int(tcp_port))
        else:
            iface = meshtastic.serial_interface.SerialInterface(devPath=self.port)

        # Verify connection by checking myInfo — same check the CLI test uses
        if not iface.myInfo:
            iface.close()
            raise RuntimeError("SerialInterface created but myInfo is empty — device not ready")

        logger.info("Meshtastic connected on %s (node num: %s)",
                    self.port, iface.myInfo.my_node_num)

        # Subscribe to incoming packets
        def on_receive(packet, interface):
            try:
                self._on_receive(packet)
            except Exception:
                logger.error("on_receive error:\n%s", traceback.format_exc())

        try:
            pub.subscribe(on_receive, "meshtastic.receive")
        except Exception:
            pass  # already subscribed from a previous attempt is fine

        with self._lock:
            self._iface = iface
            self._receive_cb = on_receive   # keep reference so we can unsubscribe

        self._connected = True
        if self._on_connect:
            self._on_connect()

    def _watch(self):
        """Block until the device disconnects or stop is requested."""
        while not self._stop.is_set():
            with self._lock:
                iface = self._iface
            if iface is None:
                break
            try:
                # SerialInterface: background send thread dying = disconnect
                send_thread = getattr(iface, "_sendThread", None)
                if send_thread is not None and not send_thread.is_alive():
                    logger.warning("Meshtastic send thread died — disconnected")
                    break
                # TCPInterface / StreamInterface: stream closed = disconnect
                stream = getattr(iface, "stream", None)
                if stream and hasattr(stream, "closed") and stream.closed:
                    logger.warning("Meshtastic stream closed — disconnected")
                    break
            except Exception:
                break
            time.sleep(2)

        if self._connected:
            self._connected = False
            logger.warning("Meshtastic disconnected")
            if self._on_disconnect:
                self._on_disconnect()

    def _close(self):
        cb = None
        with self._lock:
            cb = getattr(self, "_receive_cb", None)
            if self._iface:
                try:
                    self._iface.close()
                except Exception:
                    pass
                self._iface = None
        if cb:
            try:
                pub.unsubscribe(cb, "meshtastic.receive")
            except Exception:
                pass
        self._connected = False
