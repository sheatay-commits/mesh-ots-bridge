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

# ── Colour palette ──────────────────────────────────────────────────────────
BG       = "#1e1e2e"
PANEL    = "#2a2a3e"
ACCENT   = "#7c6af7"
GREEN    = "#50fa7b"
RED      = "#ff5555"
BLUE     = "#8be9fd"
YELLOW   = "#f1fa8c"
TEXT     = "#cdd6f4"
SUBTEXT  = "#6c7086"
BORDER   = "#45475a"


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
        self.title("Mesh ↔ OTS Bridge")
        self.configure(bg=BG)
        self.geometry("960x640")
        self.minsize(800, 540)

        self._mono = tkfont.Font(family="Courier New", size=9)
        self._bold = tkfont.Font(family="TkDefaultFont", size=9, weight="bold")

        self._build_ui()
        self._poll()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top status bar
        self._status_frame = tk.Frame(self, bg=PANEL, pady=4)
        self._status_frame.pack(fill=tk.X, side=tk.TOP)

        self._lbl_serial = tk.Label(self._status_frame, text="● Serial: --", bg=PANEL,
                                    fg=SUBTEXT, font=self._bold, padx=12)
        self._lbl_serial.pack(side=tk.LEFT)

        self._lbl_ots = tk.Label(self._status_frame, text="● OTS: --", bg=PANEL,
                                 fg=SUBTEXT, font=self._bold, padx=12)
        self._lbl_ots.pack(side=tk.LEFT)

        self._lbl_nodes = tk.Label(self._status_frame, text="Nodes: 0", bg=PANEL,
                                   fg=SUBTEXT, padx=12)
        self._lbl_nodes.pack(side=tk.RIGHT)

        # Notebook (tabs)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=TEXT,
                        padding=[12, 4], borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])

        self._nb = ttk.Notebook(self)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._tab_main    = tk.Frame(self._nb, bg=BG)
        self._tab_channels = tk.Frame(self._nb, bg=BG)
        self._tab_nodes   = tk.Frame(self._nb, bg=BG)
        self._tab_config  = tk.Frame(self._nb, bg=BG)

        self._nb.add(self._tab_main,     text="  Traffic  ")
        self._nb.add(self._tab_channels, text="  Channels  ")
        self._nb.add(self._tab_nodes,    text="  Nodes  ")
        self._nb.add(self._tab_config,   text="  Config  ")

        self._build_main_tab()
        self._build_channels_tab()
        self._build_nodes_tab()
        self._build_config_tab()

    # ── Traffic tab ───────────────────────────────────────────────────────────

    def _build_main_tab(self):
        tab = self._tab_main

        # Left panel: service controls + serial mode
        left = tk.Frame(tab, bg=PANEL, width=220)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4), pady=0)
        left.pack_propagate(False)

        _section(left, "Serial Mode")
        self._serial_mode = tk.StringVar(value="usb")
        for val, lbl in [
            ("usb",  "USB  (/dev/ttyUSB0)"),
            ("uart", "UART (/dev/ttyAMA0)"),
            ("tcp",  "TCP  (meshtasticd)"),
        ]:
            tk.Radiobutton(left, text=lbl, variable=self._serial_mode, value=val,
                           bg=PANEL, fg=TEXT, selectcolor=ACCENT,
                           activebackground=PANEL, activeforeground=TEXT,
                           command=self._on_mode_change).pack(anchor=tk.W, padx=16, pady=2)

        _section(left, "Service")
        for lbl, action in [("▶  Start", "start"), ("■  Stop", "stop"), ("↺  Restart", "restart")]:
            tk.Button(left, text=lbl, bg=ACCENT, fg="white", relief=tk.FLAT,
                      activebackground=BORDER, cursor="hand2",
                      command=lambda a=action: self._svc_action(a)
                      ).pack(fill=tk.X, padx=16, pady=3)

        # Right panel: traffic log
        right = tk.Frame(tab, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        hdr = tk.Frame(right, bg=BG)
        hdr.pack(fill=tk.X, padx=4, pady=(4, 0))
        tk.Label(hdr, text="Traffic Log", bg=BG, fg=ACCENT, font=self._bold).pack(side=tk.LEFT)
        tk.Button(hdr, text="Clear", bg=PANEL, fg=SUBTEXT, relief=tk.FLAT,
                  cursor="hand2", command=self._clear_log).pack(side=tk.RIGHT, padx=4)

        self._traffic_text = tk.Text(right, bg=PANEL, fg=TEXT, font=self._mono,
                                     state=tk.DISABLED, relief=tk.FLAT,
                                     wrap=tk.NONE, height=20)
        self._traffic_text.tag_configure("mesh", foreground=GREEN)
        self._traffic_text.tag_configure("ots",  foreground=BLUE)
        self._traffic_text.tag_configure("time", foreground=SUBTEXT)
        self._traffic_text.tag_configure("chan", foreground=YELLOW)

        sb = tk.Scrollbar(right, command=self._traffic_text.yview, bg=PANEL)
        self._traffic_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._traffic_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._last_traffic_count = 0

    # ── Channels tab ─────────────────────────────────────────────────────────

    def _build_channels_tab(self):
        tab = self._tab_channels
        self._channel_rows = []

        hdr = tk.Frame(tab, bg=BG)
        hdr.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(hdr, text="Mesh Channel Configuration", bg=BG, fg=ACCENT,
                 font=self._bold).pack(side=tk.LEFT)
        tk.Button(hdr, text="↺ Refresh from Device", bg=PANEL, fg=TEXT,
                  relief=tk.FLAT, cursor="hand2",
                  command=self._refresh_channels).pack(side=tk.RIGHT, padx=4)
        tk.Button(hdr, text="✓ Apply Channels", bg=ACCENT, fg="white",
                  relief=tk.FLAT, cursor="hand2",
                  command=self._apply_channels).pack(side=tk.RIGHT, padx=4)

        note = tk.Label(tab, text="PSK = base64-encoded pre-shared key  |  "
                        "Only 'enabled' channels are bridged",
                        bg=BG, fg=SUBTEXT, font=("TkDefaultFont", 8))
        note.pack(fill=tk.X, padx=8)

        # Column headers
        col_frame = tk.Frame(tab, bg=PANEL)
        col_frame.pack(fill=tk.X, padx=8, pady=(4, 0))
        for txt, w in [("Idx", 4), ("Name", 18), ("PSK", 28), ("Show", 5), ("Bridge?", 8)]:
            tk.Label(col_frame, text=txt, bg=PANEL, fg=SUBTEXT, width=w,
                     anchor=tk.W).pack(side=tk.LEFT, padx=4)

        self._ch_scroll_frame = tk.Frame(tab, bg=BG)
        self._ch_scroll_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._ch_canvas = tk.Canvas(self._ch_scroll_frame, bg=BG, highlightthickness=0)
        ch_sb = tk.Scrollbar(self._ch_scroll_frame, orient=tk.VERTICAL,
                             command=self._ch_canvas.yview)
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

        for ch in channels:
            row_frame = tk.Frame(self._ch_inner, bg=BG, pady=3)
            row_frame.pack(fill=tk.X)

            enabled_var = tk.BooleanVar(value=ch.get("enabled", False))
            name_var    = tk.StringVar(value=ch.get("name", ""))
            psk_var     = tk.StringVar(value=ch.get("psk", "AQ=="))
            idx         = ch.get("index", 0)

            tk.Label(row_frame, text=str(idx), bg=BG, fg=TEXT, width=4).pack(side=tk.LEFT, padx=4)

            tk.Entry(row_frame, textvariable=name_var, bg=PANEL, fg=TEXT,
                     insertbackground=TEXT, relief=tk.FLAT, width=18).pack(side=tk.LEFT, padx=4)

            psk_entry = tk.Entry(row_frame, textvariable=psk_var, bg=PANEL, fg=TEXT,
                                 insertbackground=TEXT, relief=tk.FLAT, width=28, show="•")
            psk_entry.pack(side=tk.LEFT, padx=4)

            show_var = tk.BooleanVar(value=False)
            def toggle_show(e=psk_entry, v=show_var):
                e.config(show="" if v.get() else "•")
            tk.Checkbutton(row_frame, variable=show_var, bg=BG, activebackground=BG,
                           command=toggle_show, width=5).pack(side=tk.LEFT, padx=4)

            tk.Checkbutton(row_frame, variable=enabled_var, bg=BG,
                           activebackground=BG, width=8).pack(side=tk.LEFT, padx=4)

            self._channel_rows.append({
                "index": idx, "name": name_var,
                "psk": psk_var, "enabled": enabled_var,
            })

    def _refresh_channels(self):
        def fetch():
            data = _api_get("/channels")
            if data is not None:
                self.after(0, lambda: self._populate_channels(data))
        threading.Thread(target=fetch, daemon=True).start()

    def _apply_channels(self):
        channels = [
            {
                "index": r["index"],
                "name": r["name"].get(),
                "psk": r["psk"].get(),
                "enabled": r["enabled"].get(),
            }
            for r in self._channel_rows
        ]
        def post():
            _api_post("/channels", channels)
            _api_post("/service/restart")
        threading.Thread(target=post, daemon=True).start()
        messagebox.showinfo("Applied", "Channel config saved. Service restarting.")

    # ── Nodes tab ─────────────────────────────────────────────────────────────

    def _build_nodes_tab(self):
        tab = self._tab_nodes

        hdr = tk.Frame(tab, bg=BG)
        hdr.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(hdr, text="Meshtastic Nodes", bg=BG, fg=ACCENT,
                 font=self._bold).pack(side=tk.LEFT)

        cols = ("ID", "Callsign", "Last Heard", "SNR")
        self._node_tree = ttk.Treeview(tab, columns=cols, show="headings", height=20)
        for col in cols:
            self._node_tree.heading(col, text=col)
            self._node_tree.column(col, width=180)
        style = ttk.Style()
        style.configure("Treeview", background=PANEL, foreground=TEXT,
                        fieldbackground=PANEL, rowheight=24)
        style.configure("Treeview.Heading", background=BORDER, foreground=TEXT)

        sb = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self._node_tree.yview)
        self._node_tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8))
        self._node_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

    def _update_nodes(self, nodes):
        self._node_tree.delete(*self._node_tree.get_children())
        for n in nodes:
            last = n.get("lastHeard")
            if last:
                import datetime
                try:
                    dt = datetime.datetime.fromtimestamp(last)
                    last = dt.strftime("%H:%M:%S")
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

        fields = [
            ("OTS Host",     "ots_host",         "127.0.0.1"),
            ("OTS Port",     "ots_port",          "8088"),
            ("OTS SSL",      "ots_ssl",           False),
            ("Callsign",     "callsign",          "MESH-GW"),
            ("USB Port",     "serial_port_usb",   "/dev/ttyUSB0"),
            ("UART Port",    "serial_port_uart",  "/dev/ttyAMA0"),
            ("TCP Address",  "serial_port_tcp",   "localhost:4403"),
            ("Log Buffer",   "log_buffer_size",   "500"),
            ("CoT Allowed",  "cot_types_allowed", "a-f-,b-t-f,b-m-p-"),
        ]

        frm = tk.Frame(tab, bg=BG)
        frm.pack(padx=32, pady=24, anchor=tk.NW)

        for row, (label, key, default) in enumerate(fields):
            tk.Label(frm, text=label, bg=BG, fg=TEXT, width=16,
                     anchor=tk.W).grid(row=row, column=0, pady=6, sticky=tk.W)
            if isinstance(default, bool):
                var = tk.BooleanVar(value=default)
                tk.Checkbutton(frm, variable=var, bg=BG,
                               activebackground=BG).grid(row=row, column=1, sticky=tk.W, padx=8)
            else:
                var = tk.StringVar(value=str(default))
                tk.Entry(frm, textvariable=var, bg=PANEL, fg=TEXT,
                         insertbackground=TEXT, relief=tk.FLAT,
                         width=36).grid(row=row, column=1, sticky=tk.W, padx=8)
            self._cfg_vars[key] = var

        tk.Button(frm, text="✓ Apply & Restart", bg=ACCENT, fg="white",
                  relief=tk.FLAT, cursor="hand2", padx=12,
                  command=self._apply_config).grid(row=len(fields), column=0,
                                                   columnspan=2, pady=16, sticky=tk.W)

        note = tk.Label(tab,
            text="CoT Allowed: comma-separated type prefixes. Changes restart the service.",
            bg=BG, fg=SUBTEXT, font=("TkDefaultFont", 8))
        note.pack(padx=32, anchor=tk.W)

    def _populate_config(self, cfg):
        mapping = {
            "ots_host":         cfg.get("ots_host", ""),
            "ots_port":         str(cfg.get("ots_port", 8088)),
            "ots_ssl":          cfg.get("ots_ssl", False),
            "callsign":         cfg.get("callsign", ""),
            "serial_port_usb":  cfg.get("serial_port_usb", ""),
            "serial_port_uart": cfg.get("serial_port_uart", ""),
            "serial_port_tcp":  cfg.get("serial_port_tcp", "localhost:4403"),
            "log_buffer_size":  str(cfg.get("log_buffer_size", 500)),
            "cot_types_allowed": ",".join(cfg.get("cot_types_allowed", [])),
        }
        for key, val in mapping.items():
            if key in self._cfg_vars:
                v = self._cfg_vars[key]
                if isinstance(v, tk.BooleanVar):
                    v.set(bool(val))
                else:
                    v.set(str(val))

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
        raw_allowed = self._cfg_vars["cot_types_allowed"].get()
        current["cot_types_allowed"] = [x.strip() for x in raw_allowed.split(",") if x.strip()]

        def post():
            _api_post("/config", current)
            _api_post("/service/restart")
        threading.Thread(target=post, daemon=True).start()
        messagebox.showinfo("Applied", "Config saved. Service restarting.")

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll(self):
        threading.Thread(target=self._fetch_all, daemon=True).start()
        self.after(POLL_MS, self._poll)

    def _fetch_all(self):
        status  = _api_get("/status")
        traffic = _api_get(f"/traffic?limit=100")
        nodes   = _api_get("/nodes")
        cfg     = _api_get("/config")
        self.after(0, lambda: self._apply_updates(status, traffic, nodes, cfg))

    def _apply_updates(self, status, traffic, nodes, cfg):
        self._update_status_bar(status)
        if traffic is not None:
            self._update_traffic(traffic)
        if nodes is not None:
            self._update_nodes(nodes)
        if cfg is not None:
            self._populate_config(cfg)
            if self._serial_mode.get() != cfg.get("serial_mode", "usb"):
                self._serial_mode.set(cfg.get("serial_mode", "usb"))

    def _update_status_bar(self, status):
        if not status:
            self._lbl_serial.config(text="● Serial: daemon offline", fg=RED)
            self._lbl_ots.config(text="● OTS: --", fg=SUBTEXT)
            return
        if status.get("serial_connected"):
            port = status.get("serial_port", "")
            mode = status.get("serial_mode", "usb").upper()
            self._lbl_serial.config(
                text=f"● Serial: {mode} {port}", fg=GREEN)
        else:
            self._lbl_serial.config(text="● Serial: disconnected", fg=RED)

        if status.get("ots_connected"):
            h = status.get("ots_host", "")
            p = status.get("ots_port", "")
            self._lbl_ots.config(text=f"● OTS: {h}:{p}", fg=GREEN)
        else:
            self._lbl_ots.config(text="● OTS: disconnected", fg=RED)

        self._lbl_nodes.config(
            text=f"Nodes: {status.get('node_count', 0)}", fg=TEXT)

    def _update_traffic(self, entries):
        if len(entries) == self._last_traffic_count:
            return
        self._last_traffic_count = len(entries)
        self._traffic_text.config(state=tk.NORMAL)
        self._traffic_text.delete("1.0", tk.END)
        for e in entries:
            direction = e.get("direction", "")
            tag = "mesh" if direction.startswith("mesh") else "ots"
            arrow = "▲" if tag == "mesh" else "▼"
            ch = f" [{e['channel']}]" if e.get("channel") else ""
            self._traffic_text.insert(tk.END, e.get("time", ""), "time")
            self._traffic_text.insert(tk.END, f" {arrow} ", tag)
            self._traffic_text.insert(tk.END, ch, "chan")
            self._traffic_text.insert(tk.END, f" {e.get('summary', '')}\n", tag)
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


# ── Utility ───────────────────────────────────────────────────────────────────

def _section(parent, text):
    tk.Label(parent, text=text, bg=PANEL, fg=ACCENT,
             font=("TkDefaultFont", 8, "bold"), pady=6).pack(anchor=tk.W, padx=12)


if __name__ == "__main__":
    app = App()
    # Load channels on startup
    app.after(500, app._refresh_channels)
    app.mainloop()
