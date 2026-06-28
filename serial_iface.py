"""Meshtastic serial connection wrapper with auto-reconnect."""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Lazy import so the module loads on non-Pi systems without crashing
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

        self._backoff_seq = [5, 10, 30, 60]
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

    def get_channels(self):
        with self._lock:
            if self._iface and self._connected:
                try:
                    node = self._iface.localNode
                    channels = []
                    for i in range(8):
                        ch = node.getChannelByChannelIndex(i)
                        if ch and ch.role != 0:  # 0 = DISABLED
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
                # Block until disconnected or stopped
                while self._connected and not self._stop.is_set():
                    time.sleep(1)
            except Exception as e:
                logger.error("Connection error: %s", e)
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
        # TCP mode for meshtasticd (e.g. RAK HAT managed by the daemon)
        if ":" in self.port and not self.port.startswith("/"):
            host, _, tcp_port = self.port.partition(":")
            iface = meshtastic.tcp_interface.TCPInterface(hostname=host, portNumber=int(tcp_port))
        else:
            iface = meshtastic.serial_interface.SerialInterface(devPath=self.port)

        def on_receive(packet, interface):
            self._on_receive(packet)

        def on_connect(interface, topic=pub.AUTO_TOPIC):
            self._connected = True
            logger.info("Meshtastic connected on %s", self.port)
            if self._on_connect:
                self._on_connect()

        def on_disconnect(interface, topic=pub.AUTO_TOPIC):
            self._connected = False
            logger.warning("Meshtastic disconnected")
            if self._on_disconnect:
                self._on_disconnect()

        pub.subscribe(on_receive, "meshtastic.receive")
        pub.subscribe(on_connect, "meshtastic.connection.established")
        pub.subscribe(on_disconnect, "meshtastic.connection.lost")

        with self._lock:
            self._iface = iface

    def _close(self):
        with self._lock:
            if self._iface:
                try:
                    self._iface.close()
                except Exception:
                    pass
                self._iface = None
        self._connected = False
