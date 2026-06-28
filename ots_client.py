"""OpenTakServer TCP CoT streaming client."""

import logging
import socket
import ssl
import threading
import time

logger = logging.getLogger(__name__)


class OTSClient:
    def __init__(self, host, port, use_ssl, on_receive, on_connect=None, on_disconnect=None):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self._on_receive = on_receive
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

        self._sock = None
        self._connected = False
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self._backoff_seq = [5, 5, 10, 10]
        self._backoff_idx = 0

    @property
    def is_connected(self):
        return self._connected

    def start(self):
        t = threading.Thread(target=self._connect_loop, daemon=True)
        t.start()

    def stop(self):
        self._stop.set()
        self._close()

    def send_cot(self, xml_str):
        with self._lock:
            if self._sock and self._connected:
                try:
                    data = (xml_str.strip() + "\n").encode("utf-8")
                    self._sock.sendall(data)
                    return True
                except Exception as e:
                    logger.error("OTS send failed: %s", e)
                    self._connected = False
        return False

    # ------------------------------------------------------------------

    def _connect_loop(self):
        while not self._stop.is_set():
            try:
                self._connect()
            except Exception as e:
                logger.error("OTS connection error: %s", e)
                self._close()

            if self._stop.is_set():
                break
            delay = self._backoff_seq[min(self._backoff_idx, len(self._backoff_seq) - 1)]
            self._backoff_idx += 1
            logger.info("OTS reconnecting in %ds...", delay)
            self._stop.wait(delay)

    def _connect(self):
        logger.info("Connecting to OTS %s:%s (ssl=%s)", self.host, self.port, self.use_ssl)
        raw = socket.create_connection((self.host, self.port), timeout=10)

        if self.use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(raw, server_hostname=self.host)
        else:
            sock = raw

        with self._lock:
            self._sock = sock
        self._connected = True
        self._backoff_idx = 0
        logger.info("OTS connected")
        if self._on_connect:
            self._on_connect()

        buf = b""
        while not self._stop.is_set():
            try:
                sock.settimeout(2.0)
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except Exception as e:
                logger.warning("OTS recv error: %s", e)
                break

            if not chunk:
                logger.warning("OTS connection closed by server")
                break

            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                xml = line.decode("utf-8", errors="replace").strip()
                if xml:
                    try:
                        self._on_receive(xml)
                    except Exception as e:
                        logger.error("OTS receive handler error: %s", e)

        self._close()
        self._connected = False
        if self._on_disconnect:
            self._on_disconnect()

    def _close(self):
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
        self._connected = False
