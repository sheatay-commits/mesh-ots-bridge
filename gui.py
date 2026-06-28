#!/usr/bin/env python3
"""
mesh-ots-bridge GUI
tkinter desktop app that talks to the daemon via localhost:5199.
Auto-launched on Pi desktop login via /etc/xdg/autostart/.
"""

import json
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
import urllib.request
import urllib.error

API = "http://127.0.0.1:5199"
POLL_MS = 2000

# ── Colour palette — Tactical (black / gray / cyan) ─────────────────────────
BG      = "#0a0a0a"   # near-black background
PANEL   = "#141414"   # slightly lighter panels
ACCENT  = "#00e5ff"   # cyan highlight
GREEN   = "#00ff9c"   # active / connected
RED     = "#ff2d2d"   # warning / disconnected
BLUE    = "#00bcd4"   # OTS traffic
YELLOW  = "#ffd600"   # channel labels / airtime warning
ORANGE  = "#ff6d00"   # telemetry entries
TEXT    = "#d0d0d0"   # primary text
SUBTEXT = "#505050"   # secondary / dim text
BORDER  = "#2a2a2a"   # borders / separators
DIM     = "#1c1c1c"   # alternating row / inset areas


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _api_get(path):
    try:
        with urllib.request.urlopen(f"{API}{path}", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _api_post(path, data=None):
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(
        f"{API}{path}", data=body,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


# ── Main App ─────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("▣  MESH ↔ OTS BRIDGE  ▣")
        self.configure(bg=BG)
        self.geometry("1100x700")
        self.minsize(900, 560)

        self._mono      = tkfont.Font(family="Courier New",    size=9)
        self._mono_bold = tkfont.Font(family="Courier New",    size=9,  weight="bold")
        self._bold      = tkfont.Font(family="Courier New",    size=10, weight="bold")
        self._heading   = tkfont.Font(family="Courier New",    size=11, weight="bold")
        self._small     = tkfont.Font(family="Courier New",    size=8)

        self._filter_vars = {}   # packet filter checkboxes
        self._last_traffic_count = 0

        self._build_ui()
        self._poll()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Top status bar ──────────────────────────────────────────────────
        self._status_frame = tk.Frame(self, bg=PANEL, pady=5,
                                      highlightbackground=ACCENT,
                                      highlightthickness=1)
        self._status_frame.pack(fill=tk.X, side=tk.TOP)

        # Title badge
        tk.Label(self._status_frame, text=" ▣ MESH-OTS ", bg=ACCENT, fg=BG,
                 font=self._bold, padx=6).pack(side=tk.LEFT, padx=(8, 16))

        self._lbl_serial = tk.Label(self._status_frame, text="◈ SERIAL: --",
                                    bg=PANEL, fg=SUBTEXT, font=self._mono_bold, padx=10)
        self._lbl_serial.pack(side=tk.LEFT)

        tk.Label(self._status_frame, text="│", bg=PANEL, fg=BORDER).pack(side=tk.LEFT)

        self._lbl_ots = tk.Label(self._status_frame, text="◈ OTS: --",
                                 bg=PANEL, fg=SUBTEXT, font=self._mono_bold, padx=10)
        self._lbl_ots.pack(side=tk.LEFT)

        tk.Label(self._status_frame, text="│", bg=PANEL, fg=BORDER).pack(side=tk.LEFT)

        self._lbl_airtime = tk.Label(self._status_frame, text="AIRTIME: --%  PEAK: --%",
                                     bg=PANEL, fg=YELLOW, font=self._mono_bold, padx=10)
        self._lbl_airtime.pack(side=tk.LEFT)

        self._lbl_nodes = tk.Label(self._status_frame, text="NODES: 0",
                                   bg=PANEL, fg=ACCENT, font=self._mono_bold, padx=12)
        self._lbl_nodes.pack(side=tk.RIGHT, padx=8)

        # ── Notebook ────────────────────────────────────────────────────────
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BORDER, foreground=SUBTEXT,
                        padding=[14, 5], borderwidth=0,
                        font=("Courier New", 9, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", DIM)],
                  foreground=[("selected", ACCENT)])

        self._nb = ttk.Notebook(self)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._tab_main      = tk.Frame(self._nb, bg=BG)
        self._tab_telemetry = tk.Frame(self._nb, bg=BG)
        self._tab_channels  = tk.Frame(self._nb, bg=BG)
        self._tab_nodes     = tk.Frame(self._nb, bg=BG)
        self._tab_config    = tk.Frame(self._nb, bg=BG)

        self._nb.add(self._tab_main,      text="  Traffic  ")
        self._nb.add(self._tab_telemetry, text="  Telemetry  ")
        self._nb.add(self._tab_channels,  text="  Channels  ")
        self._nb.add(self._tab_nodes,     text="  Nodes  ")
        self._nb.add(self._tab_config,    text="  Config  ")

        self._build_main_tab()
        self._build_telemetry_tab()
        self._build_channels_tab()
        self._build_nodes_tab()
        self._build_config_tab()

    # ── Traffic tab ───────────────────────────────────────────────────────────

    def _build_main_tab(self):
        tab = self._tab_main

        # ── Left panel ──────────────────────────────────────────────────────
        left = tk.Frame(tab, bg=PANEL, width=230,
                        highlightbackground=BORDER, highlightthickness=1)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 2))
        left.pack_propagate(False)

        _section(left, "[ SERIAL MODE ]")
        self._serial_mode = tk.StringVar(value="usb")
        for val, lbl in [
            ("usb",  "USB  /dev/ttyUSB0"),
            ("uart", "UART /dev/ttyAMA0"),
            ("tcp",  "TCP  meshtasticd"),
        ]:
            tk.Radiobutton(left, text=lbl, variable=self._serial_mode, value=val,
                           bg=PANEL, fg=TEXT, selectcolor=BG,
                           activebackground=PANEL, activeforeground=ACCENT,
                           font=self._mono,
                           command=self._on_mode_change).pack(anchor=tk.W, padx=16, pady=2)

        _separator(left)
        _section(left, "[ SERVICE ]")
        btn_defs = [
            ("▶  START",   "start",   GREEN),
            ("■  STOP",    "stop",    RED),
            ("↺  RESTART", "restart", ACCENT),
        ]
        for lbl, action, color in btn_defs:
            tk.Button(left, text=lbl, bg=DIM, fg=color, relief=tk.FLAT,
                      activebackground=BORDER, activeforeground=color,
                      font=self._mono_bold, cursor="hand2", bd=0,
                      highlightbackground=BORDER, highlightthickness=1,
                      command=lambda a=action: self._svc_action(a)
                      ).pack(fill=tk.X, padx=16, pady=3, ipady=4)

        _separator(left)
        _section(left, "[ PACKET FILTERS ]")
        filter_defs = [
            ("position",  "Position / PLI"),
            ("text",      "Text Messages"),
            ("telemetry", "Telemetry"),
            ("nodeinfo",  "Node Info"),
            ("markers",   "Map Markers"),
        ]
        for key, label in filter_defs:
            var = tk.BooleanVar(value=True)
            self._filter_vars[key] = var
            tk.Checkbutton(left, text=label, variable=var,
                           bg=PANEL, fg=TEXT, selectcolor=BG,
                           activebackground=PANEL, activeforeground=ACCENT,
                           font=self._mono,
                           command=self._apply_filters).pack(anchor=tk.W, padx=16, pady=1)

        tk.Button(left, text="APPLY FILTERS", bg=DIM, fg=ACCENT,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  highlightbackground=ACCENT, highlightthickness=1,
                  command=self._apply_filters
                  ).pack(fill=tk.X, padx=16, pady=(6, 4), ipady=3)

        # ── Right panel: traffic log ─────────────────────────────────────────
        right = tk.Frame(tab, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        hdr = tk.Frame(right, bg=DIM, pady=4,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X, padx=0, pady=(0, 2))
        tk.Label(hdr, text="◈ TRAFFIC LOG", bg=DIM, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(side=tk.LEFT)
        tk.Button(hdr, text="[ CLEAR ]", bg=DIM, fg=SUBTEXT, relief=tk.FLAT,
                  font=self._small, cursor="hand2",
                  activeforeground=RED, command=self._clear_log
                  ).pack(side=tk.RIGHT, padx=8)

        self._traffic_text = tk.Text(right, bg=BG, fg=TEXT, font=self._mono,
                                     state=tk.DISABLED, relief=tk.FLAT,
                                     wrap=tk.NONE, height=20,
                                     insertbackground=ACCENT,
                                     selectbackground=BORDER)
        self._traffic_text.tag_configure("mesh",   foreground=GREEN)
        self._traffic_text.tag_configure("ots",    foreground=BLUE)
        self._traffic_text.tag_configure("telem",  foreground=ORANGE)
        self._traffic_text.tag_configure("info",   foreground=SUBTEXT)
        self._traffic_text.tag_configure("time",   foreground=SUBTEXT)
        self._traffic_text.tag_configure("chan",   foreground=YELLOW)
        self._traffic_text.tag_configure("arrow",  foreground=ACCENT)

        sb = tk.Scrollbar(right, command=self._traffic_text.yview,
                          bg=DIM, troughcolor=BG, width=10)
        self._traffic_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._traffic_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    # ── Telemetry tab ─────────────────────────────────────────────────────────

    def _build_telemetry_tab(self):
        tab = self._tab_telemetry

        hdr = tk.Frame(tab, bg=DIM, pady=5,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="◈ NODE TELEMETRY", bg=DIM, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(side=tk.LEFT)
        tk.Label(hdr, text="live · updates every 2s",
                 bg=DIM, fg=SUBTEXT, font=self._small, padx=4).pack(side=tk.LEFT)

        cols = ("Node ID", "Callsign", "Battery", "Voltage",
                "Air Util", "Ch Util", "SNR", "RSSI", "Uptime", "Last Heard")
        col_widths = (100, 130, 70, 75, 75, 75, 65, 75, 85, 95)

        style = ttk.Style()
        style.configure("Telem.Treeview",
                        background=BG, foreground=TEXT,
                        fieldbackground=BG, rowheight=28,
                        font=("Courier New", 9))
        style.configure("Telem.Treeview.Heading",
                        background=DIM, foreground=ACCENT,
                        font=("Courier New", 9, "bold"))
        style.map("Telem.Treeview",
                  background=[("selected", BORDER)],
                  foreground=[("selected", ACCENT)])

        frame = tk.Frame(tab, bg=BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._telem_tree = ttk.Treeview(frame, columns=cols, show="headings",
                                        style="Telem.Treeview")
        for col, w in zip(cols, col_widths):
            self._telem_tree.heading(col, text=col.upper())
            self._telem_tree.column(col, width=w, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL,   command=self._telem_tree.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self._telem_tree.xview)
        self._telem_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._telem_tree.pack(fill=tk.BOTH, expand=True)

        self._lbl_bat_warn = tk.Label(tab, text="", bg=BG, fg=RED,
                                      font=self._mono_bold, padx=8)
        self._lbl_bat_warn.pack(anchor=tk.W, pady=(2, 0))

    def _update_telemetry(self, nodes):
        self._telem_tree.delete(*self._telem_tree.get_children())
        warnings = []
        for n in nodes:
            bat  = n.get("battery")
            volt = n.get("voltage")
            air  = n.get("air_util")
            ch   = n.get("ch_util")
            snr  = n.get("snr")
            rssi = n.get("rssi")
            up   = n.get("uptime")

            bat_str  = f"{bat}%"   if bat  is not None else "--"
            volt_str = f"{volt:.2f}V" if volt is not None else "--"
            air_str  = f"{air:.1f}%" if air  is not None else "--"
            ch_str   = f"{ch:.1f}%"  if ch   is not None else "--"
            snr_str  = f"{snr} dB"   if snr  is not None else "--"
            rssi_str = f"{rssi} dBm" if rssi is not None else "--"
            up_str   = _fmt_uptime(up) if up is not None else "--"

            tag = "low_bat" if bat is not None and bat < 20 else ""
            self._telem_tree.insert("", tk.END, tags=(tag,), values=(
                n.get("node_id", "?"),
                n.get("callsign", "?"),
                bat_str, volt_str, air_str, ch_str,
                snr_str, rssi_str, up_str,
                n.get("last_heard", "--"),
            ))
            if bat is not None and bat < 20:
                warnings.append(f"{n.get('callsign', n.get('node_id'))} battery {bat}%")

        self._telem_tree.tag_configure("low_bat", foreground=RED)
        if warnings:
            self._lbl_bat_warn.config(text="⚠ Low battery: " + ", ".join(warnings))
        else:
            self._lbl_bat_warn.config(text="")

    # ── Channels tab ─────────────────────────────────────────────────────────

    def _build_channels_tab(self):
        tab = self._tab_channels
        self._channel_rows = []

        hdr = tk.Frame(tab, bg=DIM, pady=5,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="◈ MESH CHANNELS", bg=DIM, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(side=tk.LEFT)
        tk.Button(hdr, text="[ APPLY ]", bg=DIM, fg=GREEN,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  activeforeground=GREEN,
                  command=self._apply_channels).pack(side=tk.RIGHT, padx=8)
        tk.Button(hdr, text="[ REFRESH ]", bg=DIM, fg=ACCENT,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  activeforeground=ACCENT,
                  command=self._refresh_channels).pack(side=tk.RIGHT, padx=4)

        tk.Label(tab, text="  PSK = base64 pre-shared key  |  only ENABLED channels are bridged",
                 bg=BG, fg=SUBTEXT, font=self._small).pack(fill=tk.X, pady=2)

        col_frame = tk.Frame(tab, bg=DIM,
                             highlightbackground=BORDER, highlightthickness=1)
        col_frame.pack(fill=tk.X, padx=4, pady=(2, 0))
        for txt, w in [("IDX", 4), ("NAME", 18), ("PSK", 28), ("SHOW", 5), ("BRIDGE?", 8)]:
            tk.Label(col_frame, text=txt, bg=DIM, fg=ACCENT,
                     font=self._mono_bold, width=w,
                     anchor=tk.W).pack(side=tk.LEFT, padx=4, pady=4)

        scroll_frame = tk.Frame(tab, bg=BG)
        scroll_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._ch_canvas = tk.Canvas(scroll_frame, bg=BG, highlightthickness=0)
        ch_sb = tk.Scrollbar(scroll_frame, orient=tk.VERTICAL, command=self._ch_canvas.yview)
        self._ch_inner = tk.Frame(self._ch_canvas, bg=BG)
        self._ch_inner.bind("<Configure>",
            lambda e: self._ch_canvas.configure(scrollregion=self._ch_canvas.bbox("all")))
        self._ch_canvas.create_window((0, 0), window=self._ch_inner, anchor=tk.NW)
        self._ch_canvas.configure(yscrollcommand=ch_sb.set)
        ch_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._ch_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _populate_channels(self, channels):
        for w in self._ch_inner.winfo_children():
            w.destroy()
        self._channel_rows = []
        for i, ch in enumerate(channels):
            row_bg = BG if i % 2 == 0 else DIM
            row = tk.Frame(self._ch_inner, bg=row_bg, pady=4)
            row.pack(fill=tk.X)
            enabled_var = tk.BooleanVar(value=ch.get("enabled", False))
            name_var    = tk.StringVar(value=ch.get("name", ""))
            psk_var     = tk.StringVar(value=ch.get("psk", "AQ=="))
            idx         = ch.get("index", 0)
            tk.Label(row, text=str(idx), bg=row_bg, fg=ACCENT,
                     font=self._mono_bold, width=4).pack(side=tk.LEFT, padx=4)
            tk.Entry(row, textvariable=name_var, bg=BORDER, fg=TEXT,
                     insertbackground=ACCENT, relief=tk.FLAT,
                     font=self._mono, width=18).pack(side=tk.LEFT, padx=4)
            psk_e = tk.Entry(row, textvariable=psk_var, bg=BORDER, fg=SUBTEXT,
                             insertbackground=ACCENT, relief=tk.FLAT,
                             font=self._mono, width=28, show="•")
            psk_e.pack(side=tk.LEFT, padx=4)
            show_var = tk.BooleanVar(value=False)
            def _toggle(e=psk_e, v=show_var): e.config(show="" if v.get() else "•")
            tk.Checkbutton(row, variable=show_var, bg=row_bg, fg=TEXT,
                           selectcolor=BG, activebackground=row_bg,
                           command=_toggle, width=5).pack(side=tk.LEFT, padx=4)
            tk.Checkbutton(row, variable=enabled_var, bg=row_bg, fg=ACCENT,
                           selectcolor=BG, activebackground=row_bg,
                           width=8).pack(side=tk.LEFT, padx=4)
            self._channel_rows.append({"index": idx, "name": name_var,
                                       "psk": psk_var, "enabled": enabled_var})

    def _refresh_channels(self):
        def fetch():
            data = _api_get("/channels")
            if data is not None:
                self.after(0, lambda: self._populate_channels(data))
        threading.Thread(target=fetch, daemon=True).start()

    def _apply_channels(self):
        channels = [{"index": r["index"], "name": r["name"].get(),
                     "psk": r["psk"].get(), "enabled": r["enabled"].get()}
                    for r in self._channel_rows]
        def post():
            _api_post("/channels", channels)
            _api_post("/service/restart")
        threading.Thread(target=post, daemon=True).start()
        messagebox.showinfo("Applied", "Channel config saved. Service restarting.")

    # ── Nodes tab ─────────────────────────────────────────────────────────────

    def _build_nodes_tab(self):
        tab = self._tab_nodes

        hdr = tk.Frame(tab, bg=DIM, pady=5,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="◈ MESHTASTIC NODES", bg=DIM, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(side=tk.LEFT)

        cols = ("ID", "Callsign", "Last Heard", "SNR")
        style = ttk.Style()
        style.configure("Node.Treeview",
                        background=BG, foreground=TEXT,
                        fieldbackground=BG, rowheight=26,
                        font=("Courier New", 9))
        style.configure("Node.Treeview.Heading",
                        background=DIM, foreground=ACCENT,
                        font=("Courier New", 9, "bold"))
        style.map("Node.Treeview",
                  background=[("selected", BORDER)],
                  foreground=[("selected", ACCENT)])

        self._node_tree = ttk.Treeview(tab, columns=cols, show="headings",
                                       height=20, style="Node.Treeview")
        for col in cols:
            self._node_tree.heading(col, text=col.upper())
            self._node_tree.column(col, width=220, anchor=tk.CENTER)

        sb = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self._node_tree.yview)
        self._node_tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 4))
        self._node_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def _update_nodes(self, nodes):
        self._node_tree.delete(*self._node_tree.get_children())
        for n in nodes:
            last = n.get("lastHeard")
            if last:
                import datetime
                try:
                    last = datetime.datetime.fromtimestamp(last).strftime("%H:%M:%S")
                except Exception:
                    pass
            self._node_tree.insert("", tk.END, values=(
                f"!{n.get('id', '?')}",
                n.get("callsign", "?"),
                last or "never",
                f"{n.get('snr', '?')} dB" if n.get("snr") is not None else "?",
            ))

    # ── Config tab ────────────────────────────────────────────────────────────

    def _build_config_tab(self):
        tab = self._tab_config
        self._cfg_vars = {}

        hdr = tk.Frame(tab, bg=DIM, pady=5,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="◈ CONFIGURATION", bg=DIM, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(side=tk.LEFT)

        fields = [
            ("OTS HOST",    "ots_host",         "127.0.0.1"),
            ("OTS PORT",    "ots_port",          "8088"),
            ("OTS SSL",     "ots_ssl",           False),
            ("CALLSIGN",    "callsign",          "MESH-GW"),
            ("USB PORT",    "serial_port_usb",   "/dev/ttyUSB0"),
            ("UART PORT",   "serial_port_uart",  "/dev/ttyAMA0"),
            ("TCP ADDRESS", "serial_port_tcp",   "localhost:4403"),
            ("LOG BUFFER",  "log_buffer_size",   "500"),
            ("COT ALLOWED", "cot_types_allowed", "a-f-,b-t-f,b-m-p-"),
        ]

        frm = tk.Frame(tab, bg=BG)
        frm.pack(padx=32, pady=20, anchor=tk.NW)

        for row, (label, key, default) in enumerate(fields):
            tk.Label(frm, text=label, bg=BG, fg=SUBTEXT,
                     font=self._mono, width=16,
                     anchor=tk.W).grid(row=row, column=0, pady=5, sticky=tk.W)
            tk.Label(frm, text="›", bg=BG, fg=ACCENT,
                     font=self._mono).grid(row=row, column=1, padx=4)
            if isinstance(default, bool):
                var = tk.BooleanVar(value=default)
                tk.Checkbutton(frm, variable=var, bg=BG, fg=ACCENT,
                               selectcolor=BG, activebackground=BG,
                               font=self._mono
                               ).grid(row=row, column=2, sticky=tk.W, padx=4)
            else:
                var = tk.StringVar(value=str(default))
                tk.Entry(frm, textvariable=var, bg=BORDER, fg=TEXT,
                         insertbackground=ACCENT, relief=tk.FLAT,
                         font=self._mono, width=36,
                         highlightbackground=SUBTEXT,
                         highlightthickness=1
                         ).grid(row=row, column=2, sticky=tk.W, padx=4)
            self._cfg_vars[key] = var

        tk.Button(frm, text="[ APPLY & RESTART ]", bg=DIM, fg=ACCENT,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  highlightbackground=ACCENT, highlightthickness=1,
                  padx=12, ipady=4,
                  command=self._apply_config
                  ).grid(row=len(fields), column=0, columnspan=3,
                         pady=20, sticky=tk.W)

        tk.Label(tab,
                 text="  COT ALLOWED: comma-separated type prefixes  |  changes trigger service restart",
                 bg=BG, fg=SUBTEXT, font=self._small).pack(anchor=tk.W, padx=32)

    def _populate_config(self, cfg):
        mapping = {
            "ots_host":          cfg.get("ots_host", ""),
            "ots_port":          str(cfg.get("ots_port", 8088)),
            "ots_ssl":           cfg.get("ots_ssl", False),
            "callsign":          cfg.get("callsign", ""),
            "serial_port_usb":   cfg.get("serial_port_usb", ""),
            "serial_port_uart":  cfg.get("serial_port_uart", ""),
            "serial_port_tcp":   cfg.get("serial_port_tcp", "localhost:4403"),
            "log_buffer_size":   str(cfg.get("log_buffer_size", 500)),
            "cot_types_allowed": ",".join(cfg.get("cot_types_allowed", [])),
        }
        for key, val in mapping.items():
            if key in self._cfg_vars:
                v = self._cfg_vars[key]
                if isinstance(v, tk.BooleanVar):
                    v.set(bool(val))
                else:
                    v.set(str(val))
        # Sync filter checkboxes
        filters = cfg.get("packet_filters", {})
        for key, var in self._filter_vars.items():
            var.set(filters.get(key, True))

    def _apply_config(self):
        current = _api_get("/config") or {}
        current["ots_host"]          = self._cfg_vars["ots_host"].get()
        current["ots_port"]          = int(self._cfg_vars["ots_port"].get() or 8088)
        current["ots_ssl"]           = self._cfg_vars["ots_ssl"].get()
        current["callsign"]          = self._cfg_vars["callsign"].get()
        current["serial_port_usb"]   = self._cfg_vars["serial_port_usb"].get()
        current["serial_port_uart"]  = self._cfg_vars["serial_port_uart"].get()
        current["serial_port_tcp"]   = self._cfg_vars["serial_port_tcp"].get()
        current["log_buffer_size"]   = int(self._cfg_vars["log_buffer_size"].get() or 500)
        raw = self._cfg_vars["cot_types_allowed"].get()
        current["cot_types_allowed"] = [x.strip() for x in raw.split(",") if x.strip()]
        def post():
            _api_post("/config", current)
            _api_post("/service/restart")
        threading.Thread(target=post, daemon=True).start()
        messagebox.showinfo("Applied", "Config saved. Service restarting.")

    def _apply_filters(self):
        def post():
            cfg = _api_get("/config") or {}
            cfg["packet_filters"] = {k: v.get() for k, v in self._filter_vars.items()}
            _api_post("/config", cfg)
        threading.Thread(target=post, daemon=True).start()

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll(self):
        threading.Thread(target=self._fetch_all, daemon=True).start()
        self.after(POLL_MS, self._poll)

    def _fetch_all(self):
        status   = _api_get("/status")
        traffic  = _api_get("/traffic?limit=200")
        nodes    = _api_get("/nodes")
        cfg      = _api_get("/config")
        telemetry = _api_get("/telemetry")
        self.after(0, lambda: self._apply_updates(status, traffic, nodes, cfg, telemetry))

    def _apply_updates(self, status, traffic, nodes, cfg, telemetry):
        self._update_status_bar(status)
        if traffic  is not None: self._update_traffic(traffic)
        if nodes    is not None: self._update_nodes(nodes)
        if cfg      is not None:
            self._populate_config(cfg)
            if self._serial_mode.get() != cfg.get("serial_mode", "usb"):
                self._serial_mode.set(cfg.get("serial_mode", "usb"))
        if telemetry is not None: self._update_telemetry(telemetry)

    def _update_status_bar(self, status):
        if not status:
            self._lbl_serial.config(text="◈ SERIAL: OFFLINE", fg=RED)
            self._lbl_ots.config(text="◈ OTS: --", fg=SUBTEXT)
            self._lbl_airtime.config(text="AIRTIME: --%  PEAK: --%", fg=SUBTEXT)
            return

        if status.get("serial_connected"):
            port = status.get("serial_port", "")
            mode = status.get("serial_mode", "usb").upper()
            self._lbl_serial.config(text=f"◈ SERIAL: {mode} {port}", fg=GREEN)
        else:
            self._lbl_serial.config(text="◈ SERIAL: DISCONNECTED", fg=RED)

        if status.get("ots_connected"):
            h, p = status.get("ots_host", ""), status.get("ots_port", "")
            self._lbl_ots.config(text=f"◈ OTS: {h}:{p}", fg=GREEN)
        else:
            self._lbl_ots.config(text="◈ OTS: DISCONNECTED", fg=RED)

        cur  = status.get("airtime_current", 0)
        peak = status.get("airtime_peak", 0)
        color = RED if cur > 20 else YELLOW if cur > 10 else ACCENT
        self._lbl_airtime.config(
            text=f"AIRTIME: {cur:.1f}%  PEAK: {peak:.1f}%", fg=color)

        self._lbl_nodes.config(
            text=f"NODES: {status.get('node_count', 0)}", fg=ACCENT)

    def _update_traffic(self, entries):
        if len(entries) == self._last_traffic_count:
            return
        self._last_traffic_count = len(entries)
        self._traffic_text.config(state=tk.NORMAL)
        self._traffic_text.delete("1.0", tk.END)
        for e in entries:
            direction = e.get("direction", "")
            portnum   = e.get("portnum", "")
            is_mesh   = direction.startswith("mesh")
            arrow     = " ▲ " if is_mesh else " ▼ "

            if portnum == "TELEMETRY_APP":
                tag = "telem"
            elif portnum == "NODEINFO_APP":
                tag = "info"
            elif is_mesh:
                tag = "mesh"
            else:
                tag = "ots"

            ch = f"[{e['channel']}] " if e.get("channel") else ""
            self._traffic_text.insert(tk.END, f"{e.get('time', ''):>8} ", "time")
            self._traffic_text.insert(tk.END, arrow, "arrow")
            self._traffic_text.insert(tk.END, ch, "chan")
            self._traffic_text.insert(tk.END, f"{e.get('summary', '')}\n", tag)

        self._traffic_text.see(tk.END)
        self._traffic_text.config(state=tk.DISABLED)

    def _clear_log(self):
        self._traffic_text.config(state=tk.NORMAL)
        self._traffic_text.delete("1.0", tk.END)
        self._traffic_text.config(state=tk.DISABLED)
        self._last_traffic_count = 0

    # ── Controls ──────────────────────────────────────────────────────────────

    def _on_mode_change(self):
        mode = self._serial_mode.get()
        def post():
            cfg = _api_get("/config") or {}
            cfg["serial_mode"] = mode
            _api_post("/config", cfg)
            _api_post("/service/restart")
        threading.Thread(target=post, daemon=True).start()

    def _svc_action(self, action):
        threading.Thread(
            target=lambda: _api_post(f"/service/{action}"), daemon=True
        ).start()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section(parent, text):
    tk.Label(parent, text=text, bg=PANEL, fg=ACCENT,
             font=("Courier New", 8, "bold"), pady=6).pack(anchor=tk.W, padx=12)


def _separator(parent):
    tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X, padx=8, pady=4)


def _fmt_uptime(seconds):
    if seconds is None:
        return "--"
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


if __name__ == "__main__":
    app = App()
    app.after(500, app._refresh_channels)
    app.mainloop()
