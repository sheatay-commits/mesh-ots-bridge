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

        self._filter_vars = {}
        self._last_traffic_count = 0
        self._journal_last_line  = ""
        self._prev_serial_connected = False
        self._spin_running = False
        self._spin_idx     = 0

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

        tk.Label(self._status_frame, text="│", bg=PANEL, fg=BORDER).pack(side=tk.RIGHT)

        self._lbl_utc = tk.Label(self._status_frame, text="UTC --:--:--",
                                 bg=PANEL, fg=SUBTEXT, font=self._mono_bold, padx=8)
        self._lbl_utc.pack(side=tk.RIGHT)

        self._lbl_local = tk.Label(self._status_frame, text="LCL --:--:--",
                                   bg=PANEL, fg=TEXT, font=self._mono_bold, padx=8)
        self._lbl_local.pack(side=tk.RIGHT)

        self._tick_clock()

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
        self._tab_mesh_cfg  = tk.Frame(self._nb, bg=BG)
        self._tab_ots       = tk.Frame(self._nb, bg=BG)
        self._tab_datalog   = tk.Frame(self._nb, bg=BG)
        self._tab_config    = tk.Frame(self._nb, bg=BG)

        self._nb.add(self._tab_main,      text="  Traffic  ")
        self._nb.add(self._tab_telemetry, text="  Telemetry  ")
        self._nb.add(self._tab_channels,  text="  Channels  ")
        self._nb.add(self._tab_nodes,     text="  Nodes  ")
        self._nb.add(self._tab_mesh_cfg,  text="  Node Config  ")
        self._nb.add(self._tab_ots,       text="  OTS  ")
        self._nb.add(self._tab_datalog,   text="  System Log  ")
        self._nb.add(self._tab_config,    text="  Config  ")

        self._build_main_tab()
        self._build_telemetry_tab()
        self._build_channels_tab()
        self._build_nodes_tab()
        self._build_mesh_config_tab()
        self._build_ots_tab()
        self._build_datalog_tab()
        self._build_config_tab()

    # ── Traffic / Home tab ────────────────────────────────────────────────────

    _SPIN_FRAMES = ("◐", "◓", "◑", "◒")

    def _build_main_tab(self):
        tab = self._tab_main

        # ── Top home panels ──────────────────────────────────────────────────
        top = tk.Frame(tab, bg=BG)
        top.pack(fill=tk.X, pady=(0, 2))

        dp = tk.Frame(top, bg=PANEL, highlightbackground=ACCENT, highlightthickness=1)
        dp.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 2))
        self._build_device_panel(dp)

        op = tk.Frame(top, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        op.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 2))
        self._build_home_ots_panel(op)

        sp = tk.Frame(top, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        sp.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_send_panel(sp)

        # ── Bottom: controls + traffic log ───────────────────────────────────
        bottom = tk.Frame(tab, bg=BG)
        bottom.pack(fill=tk.BOTH, expand=True)

        # ── Left panel ──────────────────────────────────────────────────────
        left = tk.Frame(bottom, bg=PANEL, width=230,
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
        for lbl, action, color in [("▶  START","start",GREEN),
                                    ("■  STOP","stop",RED),
                                    ("↺  RESTART","restart",ACCENT)]:
            tk.Button(left, text=lbl, bg=DIM, fg=color, relief=tk.FLAT,
                      activebackground=BORDER, activeforeground=color,
                      font=self._mono_bold, cursor="hand2", bd=0,
                      highlightbackground=BORDER, highlightthickness=1,
                      command=lambda a=action: self._svc_action(a)
                      ).pack(fill=tk.X, padx=16, pady=3, ipady=4)

        _separator(left)
        _section(left, "[ PACKET FILTERS ]")
        for key, label in [("position","Position / PLI"),("text","Text Messages"),
                            ("telemetry","Telemetry"),("nodeinfo","Node Info"),
                            ("markers","Map Markers")]:
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
        right = tk.Frame(bottom, bg=BG)
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
        self._traffic_text.tag_configure("mesh",  foreground=GREEN)
        self._traffic_text.tag_configure("ots",   foreground=BLUE)
        self._traffic_text.tag_configure("telem", foreground=ORANGE)
        self._traffic_text.tag_configure("info",  foreground=SUBTEXT)
        self._traffic_text.tag_configure("time",  foreground=SUBTEXT)
        self._traffic_text.tag_configure("chan",  foreground=YELLOW)
        self._traffic_text.tag_configure("arrow", foreground=ACCENT)

        sb = tk.Scrollbar(right, command=self._traffic_text.yview,
                          bg=DIM, troughcolor=BG, width=10)
        self._traffic_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._traffic_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    def _build_device_panel(self, frm):
        tk.Label(frm, text="◈ DEVICE STATUS", bg=PANEL, fg=ACCENT,
                 font=self._mono_bold, padx=8, pady=4).pack(anchor=tk.W)

        # Spinner + name row
        top_row = tk.Frame(frm, bg=PANEL)
        top_row.pack(fill=tk.X, padx=10, pady=(0,4))
        self._home_spin = tk.Label(top_row, text="◌", bg=PANEL, fg=SUBTEXT,
                                   font=tkfont.Font(family="Courier New", size=16, weight="bold"))
        self._home_spin.pack(side=tk.LEFT, padx=(0, 8))
        self._home_name = tk.Label(top_row, text="--", bg=PANEL, fg=TEXT,
                                   font=self._mono_bold)
        self._home_name.pack(side=tk.LEFT)

        # Stats grid
        grid = tk.Frame(frm, bg=PANEL)
        grid.pack(fill=tk.X, padx=10, pady=2)
        self._home_labels = {}
        fields = [("firmware","Firmware"),("battery","Battery"),
                  ("voltage","Voltage"),("uptime","Uptime")]
        for i, (key, label) in enumerate(fields):
            tk.Label(grid, text=f"{label}:", bg=PANEL, fg=SUBTEXT,
                     font=self._small, width=9, anchor=tk.W).grid(
                         row=i//2, column=(i%2)*2, sticky=tk.W, pady=1)
            lbl = tk.Label(grid, text="--", bg=PANEL, fg=TEXT, font=self._mono,
                           width=12, anchor=tk.W)
            lbl.grid(row=i//2, column=(i%2)*2+1, sticky=tk.W)
            self._home_labels[key] = lbl

        # Mini node control buttons
        ctrl = tk.Frame(frm, bg=PANEL)
        ctrl.pack(fill=tk.X, padx=10, pady=(6, 4))
        tk.Button(ctrl, text="↺ REBOOT", bg=DIM, fg=YELLOW, relief=tk.FLAT,
                  cursor="hand2", font=self._small,
                  highlightbackground=YELLOW, highlightthickness=1, padx=6,
                  command=self._mesh_reboot).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(ctrl, text="⏻ SHUTDOWN", bg=DIM, fg=RED, relief=tk.FLAT,
                  cursor="hand2", font=self._small,
                  highlightbackground=RED, highlightthickness=1, padx=6,
                  command=self._mesh_shutdown).pack(side=tk.LEFT)

    def _build_home_ots_panel(self, frm):
        tk.Label(frm, text="◈ OPENTAK SERVER", bg=PANEL, fg=ACCENT,
                 font=self._mono_bold, padx=8, pady=4).pack(anchor=tk.W)
        grid = tk.Frame(frm, bg=PANEL)
        grid.pack(fill=tk.X, padx=10, pady=4)
        self._home_ots = {}
        fields = [("status","Status"),("host","Host"),("port","Port"),
                  ("ssl","SSL"),("ots_rx","CoT RX"),("mesh_rx","Mesh RX")]
        for i, (key, label) in enumerate(fields):
            tk.Label(grid, text=f"{label}:", bg=PANEL, fg=SUBTEXT,
                     font=self._small, width=9, anchor=tk.W).grid(
                         row=i, column=0, sticky=tk.W, pady=1, padx=(0,4))
            lbl = tk.Label(grid, text="--", bg=PANEL, fg=TEXT, font=self._mono)
            lbl.grid(row=i, column=1, sticky=tk.W)
            self._home_ots[key] = lbl

    def _build_send_panel(self, frm):
        tk.Label(frm, text="◈ SEND TEST MESSAGE", bg=PANEL, fg=ACCENT,
                 font=self._mono_bold, padx=8, pady=4).pack(anchor=tk.W)

        inner = tk.Frame(frm, bg=PANEL)
        inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        tk.Label(inner, text="Message:", bg=PANEL, fg=SUBTEXT,
                 font=self._small).pack(anchor=tk.W)
        self._send_text_var = tk.StringVar()
        self._send_entry = tk.Entry(inner, textvariable=self._send_text_var,
                                    bg=BORDER, fg=TEXT, insertbackground=ACCENT,
                                    relief=tk.FLAT, font=self._mono,
                                    highlightbackground=SUBTEXT, highlightthickness=1)
        self._send_entry.pack(fill=tk.X, pady=(2, 6))
        self._send_entry.bind("<Return>", lambda _: self._send_message())

        ch_row = tk.Frame(inner, bg=PANEL)
        ch_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(ch_row, text="Channel:", bg=PANEL, fg=SUBTEXT,
                 font=self._small).pack(side=tk.LEFT)
        self._send_ch_var = tk.StringVar(value="0")
        self._send_ch_spin = tk.Spinbox(ch_row, textvariable=self._send_ch_var,
                                        from_=0, to=7, width=4,
                                        bg=BORDER, fg=TEXT, buttonbackground=DIM,
                                        insertbackground=ACCENT, relief=tk.FLAT,
                                        font=self._mono)
        self._send_ch_spin.pack(side=tk.LEFT, padx=6)

        tk.Button(inner, text="[ SEND ]", bg=DIM, fg=GREEN,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  highlightbackground=GREEN, highlightthickness=1, padx=10,
                  command=self._send_message).pack(anchor=tk.W)

        self._send_status = tk.Label(inner, text="", bg=PANEL, fg=SUBTEXT,
                                     font=self._small)
        self._send_status.pack(anchor=tk.W, pady=(4, 0))

    # ── Home panel updates ────────────────────────────────────────────────────

    def _update_home_panels(self, status, cfg, stats):
        # Device status
        serial_on = status.get("serial_connected", False) if status else False
        if serial_on:
            if not self._spin_running:
                self._spin_start()
        else:
            self._spin_stop()

        # OTS panel (home + OTS tab)
        if status:
            connected = status.get("ots_connected", False)
            ots_color = GREEN if connected else RED
            ots_text  = "● CONNECTED" if connected else "● DISCONNECTED"
            self._home_ots["status"].config(text=ots_text, fg=ots_color)
            self._home_ots["host"].config(text=status.get("ots_host","--"))
            self._home_ots["port"].config(text=str(status.get("ots_port","--")))
        if cfg:
            self._home_ots["ssl"].config(text="YES" if cfg.get("ots_ssl") else "NO")
        if stats:
            self._home_ots["ots_rx"].config(text=f"{stats.get('ots_rx',0):,}")
            self._home_ots["mesh_rx"].config(text=f"{stats.get('mesh_rx',0):,}")

    def _update_home_device(self, info):
        if not info:
            return
        bat  = info.get("battery")
        volt = info.get("voltage")
        up   = info.get("uptime")
        self._home_name.config(text=info.get("long_name","--") or "--")
        self._home_labels["firmware"].config(text=info.get("firmware","--") or "--")
        self._home_labels["battery"].config(
            text=f"{bat}%" if bat is not None else "--",
            fg=RED if bat is not None and bat < 20 else TEXT)
        self._home_labels["voltage"].config(
            text=f"{volt:.2f}V" if volt is not None else "--")
        self._home_labels["uptime"].config(text=_fmt_uptime(up))

    def _spin_start(self):
        self._spin_running = True
        self._spin_tick()

    def _spin_stop(self):
        self._spin_running = False
        self._home_spin.config(text="✗", fg=RED)

    def _spin_tick(self):
        if not self._spin_running:
            return
        self._home_spin.config(
            text=self._SPIN_FRAMES[self._spin_idx % len(self._SPIN_FRAMES)],
            fg=GREEN)
        self._spin_idx += 1
        self.after(220, self._spin_tick)

    def _send_message(self):
        text = self._send_text_var.get().strip()
        if not text:
            return
        ch = int(self._send_ch_var.get() or 0)
        self._send_status.config(text="Sending...", fg=YELLOW)
        def post():
            result = _api_post("/mesh/send_text", {"text": text, "channel": ch})
            ok = result.get("ok", False) if result else False
            def update():
                if ok:
                    self._send_text_var.set("")
                    self._send_status.config(text=f"✓ Sent on Ch{ch}", fg=GREEN)
                else:
                    err = result.get("error","failed") if result else "no response"
                    self._send_status.config(text=f"✗ {err}", fg=RED)
                self.after(3000, lambda: self._send_status.config(text="", fg=SUBTEXT))
            self.after(0, update)
        threading.Thread(target=post, daemon=True).start()

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

    # ── Node Config tab ───────────────────────────────────────────────────────

    # Enum option lists (index == protobuf int value)
    _REGIONS = ["UNSET","US","EU_433","EU_868","CN","JP","ANZ","KR","TW","RU",
                "IN","NZ_865","TH","LORA_24","UA_433","UA_868","MY_433","MY_919","SG_923"]
    _MODEM_PRESETS = ["LONG_FAST","LONG_SLOW","VERY_LONG_SLOW","MEDIUM_SLOW",
                      "MEDIUM_FAST","SHORT_SLOW","SHORT_FAST","LONG_MODERATE"]
    _DEVICE_ROLES  = ["CLIENT","CLIENT_MUTE","ROUTER","ROUTER_CLIENT","REPEATER",
                      "TRACKER","SENSOR","TAK","CLIENT_HIDDEN","LOST_AND_FOUND","TAK_TRACKER"]
    _BT_MODES      = ["RANDOM_PIN","FIXED_PIN","NO_PIN"]

    def _build_mesh_config_tab(self):
        tab = self._tab_mesh_cfg
        self._mesh_cfg_vars = {}   # keyed "section.field"

        # ── Info bar ────────────────────────────────────────────────────────
        info = tk.Frame(tab, bg=DIM, pady=6,
                        highlightbackground=ACCENT, highlightthickness=1)
        info.pack(fill=tk.X)
        tk.Label(info, text="◈ MESHTASTIC NODE CONFIG", bg=DIM, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(side=tk.LEFT)
        tk.Button(info, text="[ REFRESH ]", bg=DIM, fg=ACCENT, relief=tk.FLAT,
                  cursor="hand2", font=self._mono_bold,
                  command=self._mesh_cfg_refresh).pack(side=tk.RIGHT, padx=8)

        # Live info labels
        self._mesh_info_labels = {}
        info_fields = [("node_id","NODE ID"),("hw_model","HW"),("firmware","FW"),
                       ("battery","BAT"),("uptime","UPTIME")]
        ibar = tk.Frame(tab, bg=PANEL, pady=4,
                        highlightbackground=BORDER, highlightthickness=1)
        ibar.pack(fill=tk.X)
        for key, label in info_fields:
            tk.Label(ibar, text=f" {label}:", bg=PANEL, fg=SUBTEXT,
                     font=self._small).pack(side=tk.LEFT, padx=(8,0))
            lbl = tk.Label(ibar, text="--", bg=PANEL, fg=TEXT, font=self._mono_bold)
            lbl.pack(side=tk.LEFT, padx=(2,8))
            self._mesh_info_labels[key] = lbl

        # ── Scrollable config area ───────────────────────────────────────────
        outer = tk.Frame(tab, bg=BG)
        outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = tk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview,
                           bg=DIM, troughcolor=BG)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        self._mesh_inner = inner
        self._mesh_canvas = canvas

        # Build sections
        self._mesh_build_section(inner, "IDENTITY", [
            ("long_name",  "Long Name",  "entry",    "identity"),
            ("short_name", "Short Name", "entry",    "identity"),
        ])
        self._mesh_build_section(inner, "LoRa RADIO", [
            ("region_name",       "Region",       "combo",    "lora", self._REGIONS),
            ("modem_preset_name", "Modem Preset", "combo",    "lora", self._MODEM_PRESETS),
            ("hop_limit",         "Hop Limit",    "spin",     "lora", (1, 7)),
            ("tx_power",          "TX Power dBm", "spin",     "lora", (1, 30)),
            ("use_preset",        "Use Preset",   "check",    "lora"),
        ])
        self._mesh_build_section(inner, "DEVICE", [
            ("role_name",          "Role",              "combo", "device", self._DEVICE_ROLES),
            ("serial_enabled",     "Serial Enabled",    "check", "device"),
            ("debug_log_enabled",  "Debug Log",         "check", "device"),
        ])
        self._mesh_build_section(inner, "POSITION", [
            ("gps_enabled",               "GPS Enabled",          "check", "position"),
            ("gps_update_interval",       "GPS Update (s)",       "entry", "position"),
            ("position_broadcast_secs",   "Broadcast Interval (s)","entry","position"),
            ("smart_position_enabled",    "Smart Position",       "check", "position"),
            ("broadcast_smart_minimum_interval_secs", "Smart Min Interval (s)", "entry", "position"),
            ("broadcast_smart_minimum_distance",      "Smart Min Distance (m)", "entry", "position"),
        ])
        self._mesh_build_section(inner, "POWER", [
            ("is_power_saving",               "Power Saving",          "check", "power"),
            ("wait_bluetooth_secs",           "BT Timeout (s)",        "entry", "power"),
            ("ls_secs",                       "Light Sleep (s)",       "entry", "power"),
            ("on_battery_shutdown_after_secs","Battery Shutdown (s)",  "entry", "power"),
        ])
        self._mesh_build_section(inner, "BLUETOOTH", [
            ("enabled",   "Enabled",     "check", "bluetooth"),
            ("mode_name", "Pairing Mode","combo",  "bluetooth", self._BT_MODES),
            ("fixed_pin", "Fixed PIN",   "entry",  "bluetooth"),
        ])
        self._mesh_build_section(inner, "DISPLAY", [
            ("screen_on_secs",            "Screen On (s)",      "entry", "display"),
            ("auto_screen_carousel_secs", "Carousel (s)",       "entry", "display"),
            ("flip_screen",               "Flip Screen",        "check", "display"),
        ])

        # ── Node control ────────────────────────────────────────────────────
        ctrl = tk.Frame(inner, bg=PANEL, pady=8,
                        highlightbackground=BORDER, highlightthickness=1)
        ctrl.pack(fill=tk.X, padx=8, pady=(4, 12))
        tk.Label(ctrl, text="◈ NODE CONTROL", bg=PANEL, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(anchor=tk.W)
        btn_row = tk.Frame(ctrl, bg=PANEL)
        btn_row.pack(padx=16, pady=6)
        tk.Button(btn_row, text="↺  REBOOT NODE", bg=DIM, fg=YELLOW,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  highlightbackground=YELLOW, highlightthickness=1, padx=12,
                  command=self._mesh_reboot).pack(side=tk.LEFT, padx=8)
        tk.Button(btn_row, text="⏻  SHUTDOWN NODE", bg=DIM, fg=RED,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  highlightbackground=RED, highlightthickness=1, padx=12,
                  command=self._mesh_shutdown).pack(side=tk.LEFT, padx=8)

    def _mesh_build_section(self, parent, title, fields):
        """Build a labeled config section with Apply button."""
        frm = tk.Frame(parent, bg=PANEL,
                       highlightbackground=BORDER, highlightthickness=1)
        frm.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(frm, text=f"◈ {title}", bg=PANEL, fg=ACCENT,
                 font=self._mono_bold, padx=8, pady=4).grid(
                     row=0, column=0, columnspan=3, sticky=tk.W)

        section_keys = set()
        for row_idx, field_def in enumerate(fields, start=1):
            key      = field_def[0]
            label    = field_def[1]
            ftype    = field_def[2]
            section  = field_def[3]
            var_key  = f"{section}.{key}"
            section_keys.add(section)

            tk.Label(frm, text=label, bg=PANEL, fg=TEXT,
                     font=self._mono, width=26, anchor=tk.W,
                     padx=16).grid(row=row_idx, column=0, pady=3, sticky=tk.W)
            tk.Label(frm, text="›", bg=PANEL, fg=ACCENT,
                     font=self._mono).grid(row=row_idx, column=1, padx=4)

            if ftype == "check":
                var = tk.BooleanVar()
                tk.Checkbutton(frm, variable=var, bg=PANEL, fg=ACCENT,
                               selectcolor=BG, activebackground=PANEL,
                               font=self._mono).grid(row=row_idx, column=2, sticky=tk.W)
            elif ftype == "combo":
                options = field_def[4]
                var = tk.StringVar()
                ttk.Combobox(frm, textvariable=var, values=options,
                             width=22, state="readonly",
                             font=self._mono).grid(row=row_idx, column=2, sticky=tk.W, padx=4)
            elif ftype == "spin":
                lo, hi = field_def[4]
                var = tk.IntVar()
                tk.Spinbox(frm, textvariable=var, from_=lo, to=hi, width=8,
                           bg=BORDER, fg=TEXT, buttonbackground=DIM,
                           insertbackground=ACCENT, relief=tk.FLAT,
                           font=self._mono).grid(row=row_idx, column=2, sticky=tk.W, padx=4)
            else:  # entry
                var = tk.StringVar()
                tk.Entry(frm, textvariable=var, bg=BORDER, fg=TEXT,
                         insertbackground=ACCENT, relief=tk.FLAT,
                         font=self._mono, width=24,
                         highlightbackground=SUBTEXT,
                         highlightthickness=1).grid(row=row_idx, column=2, sticky=tk.W, padx=4)

            self._mesh_cfg_vars[var_key] = {"var": var, "type": ftype, "section": section, "key": key}

        apply_row = len(fields) + 1
        sections = list(section_keys)
        tk.Button(frm, text="[ APPLY ]", bg=DIM, fg=GREEN,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  highlightbackground=GREEN, highlightthickness=1, padx=10,
                  command=lambda s=sections, t=title: self._mesh_cfg_apply(s, t)
                  ).grid(row=apply_row, column=0, columnspan=3,
                         pady=(6,10), padx=16, sticky=tk.W)

    def _mesh_cfg_refresh(self):
        def fetch():
            info = _api_get("/mesh/info")
            cfg  = _api_get("/mesh/config")
            self.after(0, lambda: self._mesh_cfg_populate(info, cfg))
            self.after(0, lambda: self._update_home_device(info))
        threading.Thread(target=fetch, daemon=True).start()

    def _mesh_cfg_populate(self, info, cfg):
        if info:
            bat = info.get("battery")
            up  = info.get("uptime")
            self._mesh_info_labels["node_id"].config(text=info.get("node_id","--"))
            self._mesh_info_labels["hw_model"].config(text=info.get("hw_model","--"))
            self._mesh_info_labels["firmware"].config(text=info.get("firmware","--"))
            self._mesh_info_labels["battery"].config(
                text=f"{bat}%" if bat is not None else "--",
                fg=RED if bat is not None and bat < 20 else GREEN)
            self._mesh_info_labels["uptime"].config(text=_fmt_uptime(up))

        if not cfg:
            return
        for var_key, meta in self._mesh_cfg_vars.items():
            section = meta["section"]
            key     = meta["key"]
            ftype   = meta["type"]
            var     = meta["var"]
            sec_data = cfg.get(section, {})
            if key not in sec_data:
                continue
            val = sec_data[key]
            try:
                if ftype == "check":
                    var.set(bool(val))
                elif ftype == "combo":
                    var.set(str(val))
                elif ftype == "spin":
                    var.set(int(val))
                else:
                    var.set(str(val))
            except Exception:
                pass

    def _mesh_cfg_apply(self, sections, title):
        # Collect updates per section
        updates_by_section = {}
        for var_key, meta in self._mesh_cfg_vars.items():
            if meta["section"] not in sections:
                continue
            section = meta["section"]
            key     = meta["key"]
            ftype   = meta["type"]
            var     = meta["var"]
            if section not in updates_by_section:
                updates_by_section[section] = {}
            try:
                val = var.get()
                # Convert name→int for enum fields
                if key == "region_name":
                    key = "region"; val = self._REGIONS.index(val)
                elif key == "modem_preset_name":
                    key = "modem_preset"; val = self._MODEM_PRESETS.index(val)
                elif key == "role_name":
                    key = "role"; val = self._DEVICE_ROLES.index(val)
                elif key == "mode_name":
                    key = "mode"; val = self._BT_MODES.index(val)
                elif ftype == "entry" and key not in ("long_name","short_name"):
                    val = int(val) if str(val).isdigit() else val
                updates_by_section[section][key] = val
            except Exception:
                pass

        # Handle identity separately
        if "identity" in sections:
            ln = self._mesh_cfg_vars.get("identity.long_name",  {}).get("var")
            sn = self._mesh_cfg_vars.get("identity.short_name", {}).get("var")
            def post_owner():
                _api_post("/mesh/owner", {
                    "long_name":  ln.get() if ln else "",
                    "short_name": sn.get() if sn else "",
                })
            threading.Thread(target=post_owner, daemon=True).start()
            messagebox.showinfo("Applied", f"Identity update sent to node.")
            return

        def post_all():
            for sec, updates in updates_by_section.items():
                _api_post(f"/mesh/config/{sec}", updates)
        threading.Thread(target=post_all, daemon=True).start()
        messagebox.showinfo("Applied", f"{title} config sent to node.")

    def _mesh_reboot(self):
        if messagebox.askyesno("Reboot Node",
                               "Reboot the connected Meshtastic node?"):
            threading.Thread(target=lambda: _api_post("/mesh/reboot"), daemon=True).start()

    def _mesh_shutdown(self):
        if messagebox.askyesno("Shutdown Node",
                               "Shut down the connected Meshtastic node?\n"
                               "It will need to be powered off and on to recover."):
            threading.Thread(target=lambda: _api_post("/mesh/shutdown"), daemon=True).start()

    # ── OTS tab ───────────────────────────────────────────────────────────────

    def _build_ots_tab(self):
        tab = self._tab_ots

        hdr = tk.Frame(tab, bg=DIM, pady=6,
                       highlightbackground=ACCENT, highlightthickness=1)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="◈ OPENTAK SERVER", bg=DIM, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(side=tk.LEFT)

        # ── Connection status panel ──────────────────────────────────────────
        conn = tk.Frame(tab, bg=PANEL, pady=12,
                        highlightbackground=BORDER, highlightthickness=1)
        conn.pack(fill=tk.X, padx=8, pady=8)
        tk.Label(conn, text="◈ CONNECTION", bg=PANEL, fg=ACCENT,
                 font=self._mono_bold, padx=8).grid(row=0, column=0, columnspan=2,
                                                     sticky=tk.W, pady=(0,6))
        ots_fields = [
            ("Status",    "_ots_status_lbl"),
            ("Host",      "_ots_host_lbl"),
            ("Port",      "_ots_port_lbl"),
            ("SSL",       "_ots_ssl_lbl"),
            ("CoT RX",    "_ots_rx_lbl"),
            ("Mesh RX",   "_ots_mesh_rx_lbl"),
        ]
        for i, (label, attr) in enumerate(ots_fields, start=1):
            tk.Label(conn, text=label, bg=PANEL, fg=SUBTEXT,
                     font=self._mono, width=16, anchor=tk.W,
                     padx=16).grid(row=i, column=0, pady=3, sticky=tk.W)
            lbl = tk.Label(conn, text="--", bg=PANEL, fg=TEXT, font=self._mono_bold)
            lbl.grid(row=i, column=1, sticky=tk.W, padx=8)
            setattr(self, attr, lbl)

        # ── Web UI panel ─────────────────────────────────────────────────────
        web = tk.Frame(tab, bg=PANEL, pady=12,
                       highlightbackground=BORDER, highlightthickness=1)
        web.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(web, text="◈ WEB INTERFACE", bg=PANEL, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(anchor=tk.W)

        self._ots_url_var = tk.StringVar(value="https://192.168.1.163/dashboard")
        url_row = tk.Frame(web, bg=PANEL)
        url_row.pack(fill=tk.X, padx=16, pady=6)
        tk.Label(url_row, text="URL:", bg=PANEL, fg=SUBTEXT, font=self._mono).pack(side=tk.LEFT)
        url_entry = tk.Entry(url_row, textvariable=self._ots_url_var,
                             bg=BORDER, fg=ACCENT, insertbackground=ACCENT,
                             relief=tk.FLAT, font=self._mono, width=40,
                             highlightbackground=SUBTEXT, highlightthickness=1)
        url_entry.pack(side=tk.LEFT, padx=8)
        tk.Button(url_row, text="[ OPEN IN BROWSER ]", bg=DIM, fg=GREEN,
                  relief=tk.FLAT, cursor="hand2", font=self._mono_bold,
                  highlightbackground=GREEN, highlightthickness=1, padx=10,
                  command=self._ots_open_browser).pack(side=tk.LEFT, padx=8)

        # ── CoT filter display ───────────────────────────────────────────────
        cot = tk.Frame(tab, bg=PANEL, pady=10,
                       highlightbackground=BORDER, highlightthickness=1)
        cot.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(cot, text="◈ CoT TYPE ALLOWLIST", bg=PANEL, fg=ACCENT,
                 font=self._mono_bold, padx=8).pack(anchor=tk.W)
        self._ots_cot_lbl = tk.Label(cot, text="--", bg=PANEL, fg=TEXT,
                                     font=self._mono, padx=24, pady=4)
        self._ots_cot_lbl.pack(anchor=tk.W)

    def _update_ots_tab(self, status, cfg, stats):
        if not status:
            return
        connected = status.get("ots_connected", False)
        self._ots_status_lbl.config(
            text="● CONNECTED" if connected else "● DISCONNECTED",
            fg=GREEN if connected else RED)
        self._ots_host_lbl.config(text=status.get("ots_host","--"))
        self._ots_port_lbl.config(text=str(status.get("ots_port","--")))
        if cfg:
            self._ots_ssl_lbl.config(text="YES" if cfg.get("ots_ssl") else "NO")
            prefixes = cfg.get("cot_types_allowed", [])
            self._ots_cot_lbl.config(text="  |  ".join(prefixes) if prefixes else "--")
        if stats:
            self._ots_rx_lbl.config(text=f"{stats.get('ots_rx',0):,} packets")
            self._ots_mesh_rx_lbl.config(text=f"{stats.get('mesh_rx',0):,} packets")

    def _ots_open_browser(self):
        import subprocess as sp
        url = self._ots_url_var.get()
        try:
            sp.Popen(["xdg-open", url])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open browser:\n{e}")

    # ── System Log tab ────────────────────────────────────────────────────────

    def _build_datalog_tab(self):
        tab = self._tab_datalog
        self._journal_autoscroll = tk.BooleanVar(value=True)

        hdr = tk.Frame(tab, bg=DIM, pady=5,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="◈ SYSTEM LOG  (journalctl -u mesh-ots-bridge)",
                 bg=DIM, fg=ACCENT, font=self._mono_bold, padx=8).pack(side=tk.LEFT)

        tk.Button(hdr, text="[ CLEAR VIEW ]", bg=DIM, fg=SUBTEXT,
                  relief=tk.FLAT, cursor="hand2", font=self._small,
                  activeforeground=RED,
                  command=self._journal_clear).pack(side=tk.RIGHT, padx=8)
        tk.Checkbutton(hdr, text="Auto-scroll", variable=self._journal_autoscroll,
                       bg=DIM, fg=TEXT, selectcolor=BG,
                       activebackground=DIM, activeforeground=ACCENT,
                       font=self._mono).pack(side=tk.RIGHT, padx=8)

        self._journal_text = tk.Text(tab, bg=BG, fg=TEXT, font=self._mono,
                                     state=tk.DISABLED, relief=tk.FLAT,
                                     wrap=tk.NONE,
                                     insertbackground=ACCENT,
                                     selectbackground=BORDER)
        self._journal_text.tag_configure("err",  foreground=RED)
        self._journal_text.tag_configure("warn", foreground=YELLOW)
        self._journal_text.tag_configure("info", foreground=TEXT)
        self._journal_text.tag_configure("dbg",  foreground=SUBTEXT)

        sb_v = tk.Scrollbar(tab, command=self._journal_text.yview,
                            bg=DIM, troughcolor=BG, width=10)
        sb_h = tk.Scrollbar(tab, orient=tk.HORIZONTAL,
                            command=self._journal_text.xview,
                            bg=DIM, troughcolor=BG)
        self._journal_text.configure(yscrollcommand=sb_v.set,
                                     xscrollcommand=sb_h.set)
        sb_v.pack(side=tk.RIGHT,  fill=tk.Y)
        sb_h.pack(side=tk.BOTTOM, fill=tk.X)
        self._journal_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    def _journal_update(self, lines):
        if not lines:
            return
        last = lines[-1]
        if last == self._journal_last_line:
            return
        self._journal_last_line = last
        self._journal_text.config(state=tk.NORMAL)
        self._journal_text.delete("1.0", tk.END)
        for line in lines:
            lo = line.lower()
            if "error" in lo or "traceback" in lo or "exception" in lo:
                tag = "err"
            elif "warning" in lo or "warn" in lo:
                tag = "warn"
            elif "debug" in lo:
                tag = "dbg"
            else:
                tag = "info"
            self._journal_text.insert(tk.END, line + "\n", tag)
        if self._journal_autoscroll.get():
            self._journal_text.see(tk.END)
        self._journal_text.config(state=tk.DISABLED)

    def _journal_clear(self):
        self._journal_text.config(state=tk.NORMAL)
        self._journal_text.delete("1.0", tk.END)
        self._journal_text.config(state=tk.DISABLED)
        self._journal_last_lines = 0

    def _datalog_refresh_dates(self):
        pass  # no-op; kept for startup call compatibility

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
                  padx=12,
                  command=self._apply_config
                  ).grid(row=len(fields), column=0, columnspan=3,
                         pady=20, ipady=4, sticky=tk.W)

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

    def _tick_clock(self):
        import datetime as _dt
        now_local = _dt.datetime.now()
        now_utc   = _dt.datetime.utcnow()
        self._lbl_local.config(text=f"LCL {now_local.strftime('%H:%M:%S')}")
        self._lbl_utc.config(  text=f"UTC {now_utc.strftime('%H:%M:%S')}")
        self.after(1000, self._tick_clock)

    def _poll(self):
        threading.Thread(target=self._fetch_all, daemon=True).start()
        self.after(POLL_MS, self._poll)

    def _fetch_all(self):
        status    = _api_get("/status")
        traffic   = _api_get("/traffic?limit=200")
        nodes     = _api_get("/nodes")
        cfg       = _api_get("/config")
        telemetry = _api_get("/telemetry")
        journal   = _api_get("/journal?lines=300")
        stats     = _api_get("/stats")
        self.after(0, lambda: self._apply_updates(
            status, traffic, nodes, cfg, telemetry, journal, stats))

    def _apply_updates(self, status, traffic, nodes, cfg, telemetry, journal, stats):
        self._update_status_bar(status)
        if traffic  is not None: self._update_traffic(traffic)
        if nodes    is not None: self._update_nodes(nodes)
        if cfg      is not None:
            self._populate_config(cfg)
            if self._serial_mode.get() != cfg.get("serial_mode", "usb"):
                self._serial_mode.set(cfg.get("serial_mode", "usb"))
        if telemetry is not None: self._update_telemetry(telemetry)
        if journal   is not None: self._journal_update(journal.get("lines", []))
        self._update_ots_tab(status, cfg, stats)
        self._update_home_panels(status, cfg, stats)

        # Auto-populate Node Config and home device panel on connect
        serial_now = status.get("serial_connected", False) if status else False
        if serial_now and not self._prev_serial_connected:
            self.after(600, self._mesh_cfg_refresh)
        self._prev_serial_connected = serial_now

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
    app.after(500,  app._refresh_channels)
    app.after(800,  app._datalog_refresh_dates)
    app.after(1200, app._mesh_cfg_refresh)
    app.mainloop()
