#!/usr/bin/env python3
"""
Cat's VPN  Wifi 1.x
══════════════════════════════════════════════════════════════════════════
Hamachi-2026-faithful overlay network client.
P2P engine uses ONLY  import math  (golden-ratio staggering, lemniscate
host-rank, error-function stability, Euclidean mesh distance).
"""

# ─── stdlib only ──────────────────────────────────────────────────────
import os, sys, platform, json, socket, threading, time, random
import math, select, struct, subprocess
from pathlib import Path
from tkinter import *
from tkinter import ttk, messagebox, simpledialog

# ══════════════════════════════════════════════════════════════════════
#  PATHS
# ══════════════════════════════════════════════════════════════════════
IS_WIN = platform.system() == "Windows"
DATA   = (
    Path(os.environ.get("LOCALAPPDATA", Path.home()/"AppData"/"Local")) / "CatsVPN"
    if IS_WIN else Path.home() / ".catsvpn"
)
DATA.mkdir(parents=True, exist_ok=True)
CONF = DATA / "config.json"

APP_TITLE   = "Cat's VPN  Wifi 1.x"
APP_VERSION = "1.0 (Mesh Edition)"

# ══════════════════════════════════════════════════════════════════════
#  PURE-MATH BACKEND  (import math only)
# ══════════════════════════════════════════════════════════════════════
PHI = (1.0 + math.sqrt(5.0)) / 2.0   # golden ratio
TAU = math.tau                         # 2π


def _id_hash(uid: str) -> float:
    """Deterministic float in [0,1) — no hashlib."""
    h = 0
    for ch in uid:
        h = (h * 131 + ord(ch)) % 2_147_483_647
    return (h % 1_000_000) / 1_000_000.0


class MeshMath:
    """Euclidean distance + erfc stability (legacy compat)."""

    @staticmethod
    def health(coords1, coords2, rtt, loss):
        try:
            d   = math.sqrt(sum((a-b)**2 for a,b in zip(coords1, coords2)))
            lat = 1.0 / (math.log10(max(rtt, 10)) - 0.5)
            stb = math.erfc(loss / 5.0)
            return max(0, min(100, round((stb * lat * 100) * math.exp(-d / 100.0), 1)))
        except Exception:
            return 0.0

    @staticmethod
    def jitter(t: float) -> float:
        return 0.02 * (math.sin(t) + math.cos(t * 0.5))


class SignalHost:
    """Hamachi-style signal-host selection via pure math."""

    @staticmethod
    def phase(uid: str) -> float:
        return _id_hash(uid)

    @staticmethod
    def broadcast_interval(base: float, uid: str, tick: int) -> float:
        phase = SignalHost.phase(uid)
        slot  = (tick * PHI) % 1.0
        j = 0.15 * math.sin(TAU * (phase + slot)) + 0.05 * math.cos(TAU * phase * 7.0)
        return base + j

    @staticmethod
    def host_rank(uid: str) -> float:
        x = _id_hash(uid) * TAU
        d = 1.0 + math.sin(x) ** 2
        return math.cos(x)/d + 2.0 * math.sin(x)*math.cos(x)/d

    @staticmethod
    def signal(dist: float, rtt: float, loss: int, t: float) -> float:
        decay = math.exp(-dist / 100.0)
        lat   = 1.0 / (math.log10(max(rtt, 10.0)) - 0.5)
        stb   = math.erfc(loss / 5.0)
        phase = 0.5 + 0.5 * math.sin(t * 0.7) * math.cos(t * 0.3)
        return max(0.0, min(100.0, round(decay * lat * stb * 100.0 * phase, 1)))

    @staticmethod
    def slot(uid: str, tick: int) -> int:
        return int(((tick * PHI + _id_hash(uid)) % 1.0) * 32) % 32


# ══════════════════════════════════════════════════════════════════════
#  P2P ENGINE  (real UDP hole-punching)
# ══════════════════════════════════════════════════════════════════════
class P2PEngine:
    def __init__(self, cfg, refresh_cb):
        self.cfg        = cfg
        self.refresh_cb = refresh_cb
        self.sock       = None
        self.running    = False
        self.peers      : dict = {}      # {cid: {...}}
        self.chat_log   : list = []      # [(ts, nick, text)]
        self.lock       = threading.Lock()
        self.local_port = 0

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self):
        self.running = True
        self.sock    = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.1)
        try:
            self.sock.bind(("0.0.0.0", 0))
            self.local_port = self.sock.getsockname()[1]
        except Exception:
            pass
        threading.Thread(target=self._rx_loop,        daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass
        with self.lock:
            self.peers.clear()

    # ── workers ───────────────────────────────────────────────────────
    def _rx_loop(self):
        while self.running:
            try:
                r, _, _ = select.select([self.sock], [], [], 0.5)
                if not r:
                    continue
                raw, addr = self.sock.recvfrom(4096)
                msg = json.loads(raw.decode())
                t   = msg.get("type")
                if   t == "syn":  self._on_syn(msg, addr)
                elif t == "pong": self._on_pong(msg)
                elif t == "chat": self._on_chat(msg)
            except Exception:
                continue

    def _heartbeat_loop(self):
        tick = 0
        while self.running:
            iv  = SignalHost.broadcast_interval(3.0, self.cfg["client_id"], tick)
            nets = [n.get("id") for n in self.cfg.get("networks", []) if n.get("id")]
            pkt  = json.dumps({
                "type":   "syn",
                "id":     self.cfg["client_id"],
                "nick":   self.cfg["nickname"],
                "vip":    self.cfg["vpn_ip"],
                "coords": self.cfg["coords"],
                "ts":     time.time(),
                "hr":     SignalHost.host_rank(self.cfg["client_id"]),
                "nets":   nets,
            }).encode()

            try:
                self.sock.sendto(pkt, ("255.255.255.255", 9999))
            except Exception:
                pass

            with self.lock:
                for cid, p in list(self.peers.items()):
                    if p.get("last_addr"):
                        try:
                            self.sock.sendto(pkt, p["last_addr"])
                        except Exception:
                            pass
                    if time.time() - p.get("last_seen", 0) > 15:
                        p["online"] = False

            self.refresh_cb()
            time.sleep(max(0.5, iv))
            tick += 1

    # ── handlers ──────────────────────────────────────────────────────
    def _on_syn(self, msg, addr):
        cid       = msg["id"]
        peer_nets = set(msg.get("nets") or [])
        my_nets   = {n.get("id") for n in self.cfg.get("networks", []) if n.get("id")}
        if my_nets and peer_nets and not (my_nets & peer_nets):
            return
        with self.lock:
            if cid not in self.peers:
                self.peers[cid] = {"packets_lost": 0, "history": [], "rtt": 0}
            self.peers[cid].update({
                "name":       msg["nick"],
                "vip":        msg["vip"],
                "coords":     msg["coords"],
                "last_addr":  addr,
                "last_seen":  time.time(),
                "online":     True,
                "host_rank":  SignalHost.host_rank(cid),
                "nets":       list(peer_nets),
            })
            pong = json.dumps({"type": "pong", "id": self.cfg["client_id"], "ts": msg["ts"]}).encode()
            self.sock.sendto(pong, addr)
        self.refresh_cb()

    def _on_pong(self, msg):
        cid = msg["id"]
        rtt = (time.time() - msg["ts"]) * 1000
        with self.lock:
            if cid in self.peers:
                p  = self.peers[cid]
                p["rtt"] = rtt
                c1 = self.cfg.get("coords", [0.0, 0.0])
                c2 = p.get("coords", [0.0, 0.0])
                d  = math.sqrt(sum((a-b)**2 for a,b in zip(c1, c2)))
                p["health"] = SignalHost.signal(d, rtt, p.get("packets_lost", 0), time.time())

    def _on_chat(self, msg):
        text = msg.get("text", "").strip()
        if not text:
            return
        with self.lock:
            self.chat_log.append((msg.get("ts", time.time()), msg.get("nick", "?"), text))
        self.refresh_cb()

    def send_chat(self, text: str):
        if not text:
            return
        nick = self.cfg.get("nickname", "Me")
        ts   = time.time()
        with self.lock:
            self.chat_log.append((ts, nick, text))
        pkt = json.dumps({"type": "chat", "id": self.cfg["client_id"],
                          "nick": nick, "text": text, "ts": ts}).encode()
        try:
            self.sock.sendto(pkt, ("255.255.255.255", 9999))
            with self.lock:
                for p in self.peers.values():
                    if p.get("last_addr"):
                        try:
                            self.sock.sendto(pkt, p["last_addr"])
                        except Exception:
                            pass
        except Exception:
            pass
        self.refresh_cb()


# ══════════════════════════════════════════════════════════════════════
#  PALETTE  (Hamachi 2026 dark theme)
# ══════════════════════════════════════════════════════════════════════
C = {
    "bg":          "#1e2530",   # main window
    "hdr":         "#161c27",   # top header
    "menubar":     "#0f141d",   # menu strip
    "panel":       "#111722",   # peer-list area
    "group":       "#1a2236",   # network group header
    "row":         "#141b27",   # peer row
    "row_hover":   "#1f2b3e",
    "sidebar":     "#0e1420",   # right sidebar
    "divider":     "#2a3448",
    "on":          "#27ae60",   # online green
    "on_bright":   "#2ecc71",
    "off":         "#7f8c8d",   # offline grey
    "warn":        "#e67e22",
    "accent":      "#3498db",   # blue accent (Cat's VPN brand)
    "accent2":     "#9b59b6",   # purple
    "txt_h":       "#ecf0f1",   # primary text
    "txt_s":       "#8fa3b8",   # secondary text
    "txt_d":       "#4a5568",   # disabled/dim
    "btn":         "#253447",
    "btn_hover":   "#2e4060",
    "entry_bg":    "#0d1420",
    "scrollbar":   "#1f2e42",
    "status_bar":  "#0c1219",
}

FONT_UI    = ("Segoe UI",   9)
FONT_UIsm  = ("Segoe UI",   8)
FONT_UIb   = ("Segoe UI",   9, "bold")
FONT_UIlg  = ("Segoe UI",  11, "bold")
FONT_MONO  = ("Consolas",   8)
FONT_MONOs = ("Consolas",   7)
FONT_TITLE = ("Segoe UI",  10, "bold")


# ══════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════
def _signal_bars(canvas, x, y, pct, w=16, h=10):
    """Draw 4 Hamachi-style signal bars."""
    bar_w = 3
    gap   = 1
    heights = [h*0.3, h*0.5, h*0.7, h]
    thresholds = [0, 25, 55, 80]
    for i, (bh, th) in enumerate(zip(heights, thresholds)):
        bx = x + i*(bar_w+gap)
        by = y + h - bh
        col = C["on_bright"] if pct > th else C["txt_d"]
        canvas.create_rectangle(bx, by, bx+bar_w, y+h, fill=col, outline="")

def _status_dot(canvas, x, y, online: bool, is_me=False, r=5):
    col = C["accent"] if is_me else (C["on_bright"] if online else C["off"])
    canvas.create_oval(x-r, y-r, x+r, y+r, fill=col, outline="")
    if online and not is_me:
        canvas.create_oval(x-r+1, y-r+1, x+r-1, y+r-1, fill="", outline=C["on"], width=1)


# ══════════════════════════════════════════════════════════════════════
#  MAIN UI
# ══════════════════════════════════════════════════════════════════════
class CatsVPNUI:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("780x560")
        self.root.minsize(640, 460)
        self.root.configure(bg=C["bg"])

        self.cfg    = self._load_cfg()
        self.engine = P2PEngine(self.cfg, self._schedule_refresh)

        # selected peer / network for detail panel
        self.sel_peer = None
        self.sel_net  = None

        self._build_ui()

        if self.cfg.get("power"):
            self.engine.start()

        self._refresh()
        self._tick()

    # ── config I/O ────────────────────────────────────────────────────
    def _load_cfg(self):
        defaults = {
            "client_id": f"cat-{random.randint(10000,99999)}",
            "nickname":   platform.node(),
            "vpn_ip":    f"25.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}",
            "power":      False,
            "coords":    [random.uniform(0,100), random.uniform(0,100)],
            "networks":  [],
        }
        try:
            with open(CONF) as f:
                cfg = json.load(f)
            if not isinstance(cfg.get("networks"), list):
                cfg["networks"] = []
            return cfg
        except Exception:
            return defaults

    def _save_cfg(self):
        with open(CONF, "w") as f:
            json.dump(self.cfg, f, indent=2)

    # ── build UI ──────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_menubar()
        self._build_header()
        self._build_body()
        self._build_statusbar()

    # ── menu bar ──────────────────────────────────────────────────────
    def _build_menubar(self):
        mb = Frame(self.root, bg=C["menubar"], height=22)
        mb.pack(fill=X, side=TOP)
        mb.pack_propagate(False)

        def _item(label, cmd):
            lbl = Label(mb, text=label, bg=C["menubar"], fg=C["txt_s"],
                        font=FONT_UIsm, padx=12, cursor="hand2")
            lbl.pack(side=LEFT, fill=Y)
            lbl.bind("<Enter>",   lambda e: lbl.config(fg=C["txt_h"], bg=C["btn"]))
            lbl.bind("<Leave>",   lambda e: lbl.config(fg=C["txt_s"], bg=C["menubar"]))
            lbl.bind("<Button-1>",lambda e: cmd())

        _item("System",  self._menu_system)
        _item("Network", self._menu_network)
        _item("View",    self._menu_view)
        _item("Help",    self._menu_help)

    # ── header ────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = Frame(self.root, bg=C["hdr"], height=68)
        hdr.pack(fill=X)
        hdr.pack_propagate(False)

        # ── left: power button ────────────────────────────────────────
        pwr_frame = Frame(hdr, bg=C["hdr"], width=80)
        pwr_frame.pack(side=LEFT, fill=Y, padx=(14,0))
        pwr_frame.pack_propagate(False)

        self.pwr_cv = Canvas(pwr_frame, width=52, height=52,
                             bg=C["hdr"], highlightthickness=0, cursor="hand2")
        self.pwr_cv.pack(expand=True)
        self.pwr_cv.bind("<Button-1>", lambda e: self._toggle_power())
        self._draw_power_btn(False)

        # ── centre: logo + VPN info ───────────────────────────────────
        mid = Frame(hdr, bg=C["hdr"])
        mid.pack(side=LEFT, fill=Y, padx=12, expand=True)

        # logo row
        logo_row = Frame(mid, bg=C["hdr"])
        logo_row.pack(anchor="w", pady=(10,2))

        # cat paw logo (drawn on tiny canvas)
        logo_cv = Canvas(logo_row, width=22, height=22,
                         bg=C["hdr"], highlightthickness=0)
        logo_cv.pack(side=LEFT, padx=(0,6))
        # paw: big pad + 4 toes
        logo_cv.create_oval(5,10,17,20, fill=C["accent"],  outline="")
        logo_cv.create_oval(2,4,8,10,   fill=C["accent2"], outline="")
        logo_cv.create_oval(9,2,14,8,   fill=C["accent2"], outline="")
        logo_cv.create_oval(14,4,20,10, fill=C["accent2"], outline="")

        Label(logo_row, text=APP_TITLE, bg=C["hdr"],
              fg=C["txt_h"], font=FONT_TITLE).pack(side=LEFT)

        self.lbl_ip   = Label(mid, text="―  Not connected", bg=C["hdr"],
                              fg=C["txt_s"], font=FONT_MONO)
        self.lbl_ip.pack(anchor="w")
        self.lbl_nick = Label(mid, text=self.cfg["nickname"], bg=C["hdr"],
                              fg=C["txt_d"], font=FONT_UIsm)
        self.lbl_nick.pack(anchor="w")

        # ── right: connection quality canvas ──────────────────────────
        right = Frame(hdr, bg=C["hdr"], width=160)
        right.pack(side=RIGHT, fill=Y, padx=14)
        right.pack_propagate(False)

        self.quality_cv = Canvas(right, width=140, height=52,
                                 bg=C["hdr"], highlightthickness=0)
        self.quality_cv.pack(expand=True)
        self._draw_quality_idle()

    def _draw_power_btn(self, on: bool):
        cv = self.pwr_cv
        cv.delete("all")
        ring_col  = C["on_bright"] if on else C["off"]
        inner_col = "#1a2a1a" if on else "#1c1c1c"
        cv.create_oval(4, 4, 48, 48, fill=inner_col, outline=ring_col, width=2)
        # arc gap at top
        cv.create_arc(10,10, 42,42, start=60, extent=240,
                      style=ARC, outline=ring_col, width=3)
        # stem
        cv.create_line(26, 8, 26, 22, fill=ring_col, width=3, capstyle=ROUND)

    def _draw_quality_idle(self):
        cv = self.quality_cv
        cv.delete("all")
        cv.create_text(70, 26, text="OFFLINE", fill=C["txt_d"],
                       font=("Segoe UI", 8, "bold"))

    def _draw_quality(self, peer_count: int, avg_health: float):
        cv = self.quality_cv
        cv.delete("all")
        _signal_bars(cv, 4, 16, avg_health, w=18, h=18)
        cv.create_text(30, 10, text=f"{int(avg_health)}%",
                       fill=C["on_bright"] if avg_health>60 else C["warn"],
                       font=("Segoe UI", 8, "bold"), anchor="w")
        cv.create_text(4, 36, text=f"{peer_count} peer{'s' if peer_count!=1 else ''} online",
                       fill=C["txt_s"], font=FONT_UIsm, anchor="w")

    # ── body (left list + right detail) ───────────────────────────────
    def _build_body(self):
        body = Frame(self.root, bg=C["bg"])
        body.pack(fill=BOTH, expand=True)

        # ── LEFT: network / peer list ─────────────────────────────────
        left = Frame(body, bg=C["panel"], width=320)
        left.pack(side=LEFT, fill=Y)
        left.pack_propagate(False)

        # tiny header
        lh = Frame(left, bg=C["group"], height=24)
        lh.pack(fill=X)
        lh.pack_propagate(False)
        Label(lh, text="My Networks", bg=C["group"], fg=C["txt_s"],
              font=FONT_UIsm, padx=8).pack(side=LEFT, fill=Y)
        add_btn = Label(lh, text="＋", bg=C["group"], fg=C["accent"],
                        font=FONT_UIsm, padx=8, cursor="hand2")
        add_btn.pack(side=RIGHT, fill=Y)
        add_btn.bind("<Button-1>", lambda e: self._create_network())

        # scrollable list
        sf = Frame(left, bg=C["panel"])
        sf.pack(fill=BOTH, expand=True)

        self.list_cv   = Canvas(sf, bg=C["panel"], highlightthickness=0,
                                yscrollincrement=1)
        vsb = Scrollbar(sf, orient=VERTICAL, command=self.list_cv.yview,
                        bg=C["scrollbar"], troughcolor=C["panel"],
                        relief=FLAT, bd=0, width=8)
        self.list_frame = Frame(self.list_cv, bg=C["panel"])
        self._list_win  = self.list_cv.create_window(
            (0,0), window=self.list_frame, anchor="nw")

        self.list_cv.configure(yscrollcommand=vsb.set)
        vsb.pack(side=RIGHT, fill=Y)
        self.list_cv.pack(side=LEFT, fill=BOTH, expand=True)

        self.list_frame.bind("<Configure>",
            lambda e: self.list_cv.configure(
                scrollregion=self.list_cv.bbox("all")))
        self.list_cv.bind("<Configure>",
            lambda e: self.list_cv.itemconfig(
                self._list_win, width=e.width))

        # mouse-wheel scroll
        def _wheel(e):
            self.list_cv.yview_scroll(int(-1*(e.delta/120)), "units")
        self.list_cv.bind_all("<MouseWheel>", _wheel)

        # ── VERTICAL DIVIDER ─────────────────────────────────────────
        Frame(body, bg=C["divider"], width=1).pack(side=LEFT, fill=Y)

        # ── RIGHT: detail + activity ──────────────────────────────────
        right = Frame(body, bg=C["sidebar"])
        right.pack(side=LEFT, fill=BOTH, expand=True)

        # tab strip
        tab_row = Frame(right, bg=C["menubar"], height=28)
        tab_row.pack(fill=X)
        tab_row.pack_propagate(False)

        self._active_tab = StringVar(value="activity")

        def _tab(label, key):
            lbl = Label(tab_row, text=label, bg=C["menubar"],
                        fg=C["txt_s"], font=FONT_UIsm, padx=14, cursor="hand2")
            lbl.pack(side=LEFT, fill=Y)
            def _click(e, k=key, l=lbl):
                self._active_tab.set(k)
                self._refresh_detail()
            lbl.bind("<Button-1>", _click)
            self._active_tab.trace_add("write",
                lambda *a, k=key, l=lbl: l.config(
                    fg=C["accent"] if self._active_tab.get()==k else C["txt_s"],
                    bg=C["btn"]    if self._active_tab.get()==k else C["menubar"]))

        _tab("Activity", "activity")
        _tab("Peer Info","info")

        # ── detail canvas (top half) ──────────────────────────────────
        self.detail_cv = Canvas(right, bg=C["sidebar"], height=160,
                                highlightthickness=0)
        self.detail_cv.pack(fill=X, padx=0, pady=0)

        Frame(right, bg=C["divider"], height=1).pack(fill=X)

        # ── chat / activity (bottom half) ─────────────────────────────
        self.chat_txt = Text(right, bg=C["panel"], fg=C["txt_h"],
                             relief=FLAT, wrap=WORD, font=FONT_UIsm,
                             state=DISABLED, padx=8, pady=4,
                             selectbackground=C["btn"])
        self.chat_txt.pack(fill=BOTH, expand=True)

        Frame(right, bg=C["divider"], height=1).pack(fill=X)

        # entry row
        er = Frame(right, bg=C["sidebar"], height=32)
        er.pack(fill=X, padx=8, pady=4)
        er.pack_propagate(False)

        self.chat_var = StringVar()
        ent = Entry(er, textvariable=self.chat_var, bg=C["entry_bg"],
                    fg=C["txt_h"], relief=FLAT, font=FONT_UIsm,
                    insertbackground=C["txt_h"])
        ent.pack(side=LEFT, fill=BOTH, expand=True, ipady=4, padx=(0,6))
        ent.bind("<Return>", lambda e: self._send_chat())

        send_btn = Label(er, text="Send", bg=C["accent"], fg="white",
                         font=FONT_UIsm, padx=10, cursor="hand2")
        send_btn.pack(side=LEFT, fill=Y)
        send_btn.bind("<Button-1>", lambda e: self._send_chat())

    # ── status bar ────────────────────────────────────────────────────
    def _build_statusbar(self):
        sb = Frame(self.root, bg=C["status_bar"], height=20)
        sb.pack(fill=X, side=BOTTOM)
        sb.pack_propagate(False)

        self.lbl_status = Label(sb, text="Disconnected", bg=C["status_bar"],
                                fg=C["txt_d"], font=FONT_UIsm, padx=8)
        self.lbl_status.pack(side=LEFT, fill=Y)

        Label(sb, text="│", bg=C["status_bar"], fg=C["txt_d"],
              font=FONT_UIsm).pack(side=LEFT)

        self.lbl_peers_sb = Label(sb, text="0 peers", bg=C["status_bar"],
                                  fg=C["txt_d"], font=FONT_UIsm, padx=8)
        self.lbl_peers_sb.pack(side=LEFT, fill=Y)

        Label(sb, text=APP_VERSION, bg=C["status_bar"], fg=C["txt_d"],
              font=FONT_UIsm, padx=8).pack(side=RIGHT, fill=Y)

    # ── refresh engine ────────────────────────────────────────────────
    def _schedule_refresh(self):
        self.root.after(0, self._refresh)

    def _tick(self):
        """Animate quality display every second."""
        self._refresh_header_quality()
        self.root.after(1000, self._tick)

    def _refresh(self):
        self._refresh_header()
        self._refresh_list()
        self._refresh_detail()
        self._refresh_chat()
        self._refresh_statusbar()

    def _refresh_header(self):
        on = self.engine.running
        self._draw_power_btn(on)
        self.lbl_ip.config(
            text=f"  {self.cfg['vpn_ip']}" if on else "―  Not connected",
            fg=C["on_bright"] if on else C["txt_s"])

    def _refresh_header_quality(self):
        with self.engine.lock:
            peers = [p for p in self.engine.peers.values() if p.get("online")]
        if not peers or not self.engine.running:
            self._draw_quality_idle()
            return
        avg = sum(p.get("health", 0) for p in peers) / max(len(peers), 1)
        self._draw_quality(len(peers), avg)

    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()

        if not self.cfg.get("networks"):
            lbl = Label(self.list_frame,
                        text="No networks.\nUse Network → Create or Join.",
                        bg=C["panel"], fg=C["txt_d"], font=FONT_UIsm,
                        justify=CENTER)
            lbl.pack(expand=True, pady=30)
            return

        for net in self.cfg["networks"]:
            self._draw_network_group(net)

    def _draw_network_group(self, net):
        nid   = net.get("id", "")
        nname = net.get("name", "Unnamed")

        # group header
        gh = Frame(self.list_frame, bg=C["group"], cursor="hand2")
        gh.pack(fill=X, pady=(0,1))

        # triangle indicator
        tri = Label(gh, text="▼", bg=C["group"], fg=C["txt_s"],
                    font=("Segoe UI", 7), padx=4)
        tri.pack(side=LEFT)

        Label(gh, text=nname, bg=C["group"], fg=C["txt_h"],
              font=FONT_UIb, padx=2).pack(side=LEFT, pady=4)

        Label(gh, text=f"ID: {nid}", bg=C["group"], fg=C["txt_d"],
              font=FONT_MONOs, padx=6).pack(side=RIGHT)

        # peer rows
        with self.engine.lock:
            # always show "Me"
            self._draw_peer_row(nname, "Me (this computer)",
                                self.cfg["vpn_ip"], 100, True, False)

            # filter peers by network
            visible = []
            for cid, p in self.engine.peers.items():
                if not p.get("online"):
                    continue
                pnets = p.get("nets") or []
                if nid and pnets and nid not in pnets:
                    continue
                visible.append((cid, p))

            visible.sort(key=lambda x: x[1].get("host_rank", 1e9))
            for i, (cid, p) in enumerate(visible):
                self._draw_peer_row(nname, p["name"], p["vip"],
                                    p.get("health", 0), False, i == 0,
                                    p.get("rtt", 0))

        Frame(self.list_frame, bg=C["divider"], height=1).pack(fill=X)

    def _draw_peer_row(self, net_name, nick, vip, health, is_me,
                       is_host=False, rtt=0):
        row = Frame(self.list_frame, bg=C["row"], cursor="hand2")
        row.pack(fill=X)
        row.bind("<Enter>", lambda e: row.config(bg=C["row_hover"]))
        row.bind("<Leave>", lambda e: row.config(bg=C["row"]))

        # status dot
        dot_cv = Canvas(row, width=16, height=32, bg=C["row"],
                        highlightthickness=0)
        dot_cv.pack(side=LEFT, padx=(14, 4))
        dot_cv.bind("<Enter>", lambda e: dot_cv.config(bg=C["row_hover"]))
        dot_cv.bind("<Leave>", lambda e: dot_cv.config(bg=C["row"]))
        online = health > 0
        _status_dot(dot_cv, 8, 16, online, is_me)

        # name + vip
        info = Frame(row, bg=C["row"])
        info.pack(side=LEFT, fill=BOTH, expand=True, pady=3)
        info.bind("<Enter>", lambda e: [w.config(bg=C["row_hover"])
                                        for w in info.winfo_children()] or
                                       info.config(bg=C["row_hover"]))
        info.bind("<Leave>", lambda e: [w.config(bg=C["row"])
                                        for w in info.winfo_children()] or
                                       info.config(bg=C["row"]))

        suffix = "  [Host]" if is_host and not is_me else ""
        Label(info, text=nick + suffix, bg=C["row"], fg=C["txt_h"],
              font=FONT_UI, anchor="w").pack(fill=X)
        Label(info, text=vip, bg=C["row"], fg=C["txt_d"],
              font=FONT_MONO, anchor="w").pack(fill=X)

        # signal bars + health%
        if not is_me:
            right = Frame(row, bg=C["row"])
            right.pack(side=RIGHT, padx=10)
            sig_cv = Canvas(right, width=24, height=32, bg=C["row"],
                            highlightthickness=0)
            sig_cv.pack()
            _signal_bars(sig_cv, 0, 10, health, w=24, h=14)
            h_col = C["on_bright"] if health>70 else C["warn"] if health>35 else C["off"]
            sig_cv.create_text(12, 28, text=f"{int(health)}%",
                               fill=h_col, font=("Segoe UI", 7), anchor="center")

        # click → select
        def _select(e, n=nick, v=vip, h=health, r=rtt, net=net_name):
            self.sel_peer = {"nick": n, "vip": v, "health": h, "rtt": r, "net": net}
            self._active_tab.set("info")
            self._refresh_detail()

        for w in [row, info] + list(info.winfo_children()) + [dot_cv]:
            w.bind("<Button-1>", _select)

    # ── detail panel ──────────────────────────────────────────────────
    def _refresh_detail(self):
        cv = self.detail_cv
        cv.delete("all")
        tab = self._active_tab.get()
        W = cv.winfo_width() or 400
        H = 160

        if tab == "info" and self.sel_peer:
            p = self.sel_peer
            cv.create_text(16, 18, text=p["nick"], fill=C["txt_h"],
                           font=FONT_UIlg, anchor="w")
            cv.create_text(16, 40, text=f"VPN IP:  {p['vip']}", fill=C["txt_s"],
                           font=FONT_MONO, anchor="w")
            cv.create_text(16, 56, text=f"Network: {p['net']}", fill=C["txt_s"],
                           font=FONT_MONO, anchor="w")
            cv.create_text(16, 72, text=f"RTT:     {int(p['rtt'])} ms",
                           fill=C["txt_s"], font=FONT_MONO, anchor="w")
            cv.create_text(16, 88, text=f"Health:  {int(p['health'])}%",
                           fill=C["on_bright"] if p["health"]>60 else C["warn"],
                           font=FONT_MONO, anchor="w")
            # big signal bars
            _signal_bars(cv, W-80, 40, p["health"], w=60, h=40)
        else:
            cv.create_text(W//2, H//2, text="Activity Feed ↓",
                           fill=C["txt_d"], font=FONT_UIb)

    # ── chat / activity ───────────────────────────────────────────────
    def _refresh_chat(self):
        self.chat_txt.config(state=NORMAL)
        self.chat_txt.delete("1.0", END)
        with self.engine.lock:
            lines = list(self.engine.chat_log)[-300:]
        for ts, nick, text in lines:
            t = time.strftime("%H:%M", time.localtime(ts))
            self.chat_txt.insert(END, f"[{t}] ", "dim")
            self.chat_txt.insert(END, f"{nick}: ", "name")
            self.chat_txt.insert(END, f"{text}\n", "body")
        self.chat_txt.tag_config("dim",  foreground=C["txt_d"])
        self.chat_txt.tag_config("name", foreground=C["accent"])
        self.chat_txt.tag_config("body", foreground=C["txt_h"])
        self.chat_txt.see(END)
        self.chat_txt.config(state=DISABLED)

    def _send_chat(self):
        text = self.chat_var.get().strip()
        if not text:
            return
        self.chat_var.set("")
        self.engine.send_chat(text)

    # ── status bar ────────────────────────────────────────────────────
    def _refresh_statusbar(self):
        on = self.engine.running
        with self.engine.lock:
            cnt = sum(1 for p in self.engine.peers.values() if p.get("online"))
        self.lbl_status.config(
            text="Connected" if on else "Disconnected",
            fg=C["on_bright"] if on else C["txt_d"])
        self.lbl_peers_sb.config(
            text=f"{cnt} peer{'s' if cnt!=1 else ''} online",
            fg=C["txt_s"] if cnt>0 else C["txt_d"])

    # ── power ─────────────────────────────────────────────────────────
    def _toggle_power(self):
        if self.engine.running:
            self.engine.stop()
        else:
            self.engine.start()
        self.cfg["power"] = self.engine.running
        self._save_cfg()
        self._refresh()

    # ── menus ─────────────────────────────────────────────────────────
    def _popup(self, items):
        m = Menu(self.root, tearoff=0, bg=C["hdr"], fg=C["txt_h"],
                 activebackground=C["btn_hover"], activeforeground=C["txt_h"],
                 relief=FLAT, bd=0)
        for label, cmd in items:
            if label == "---":
                m.add_separator()
            else:
                m.add_command(label=label, command=cmd)
        try:
            m.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            m.grab_release()

    def _menu_system(self):
        self._popup([
            ("Preferences…",  self._prefs_dialog),
            ("---",           None),
            ("Quit",          self.root.quit),
        ])

    def _menu_network(self):
        self._popup([
            ("Create network…",       self._create_network),
            ("Join network…",         self._join_network),
            ("Leave / delete network…", self._leave_network),
            ("---",                   None),
            ("Go Online",  lambda: self._set_power(True)),
            ("Go Offline", lambda: self._set_power(False)),
        ])

    def _menu_view(self):
        self._popup([
            ("Show Activity",  lambda: (self._active_tab.set("activity"),
                                        self._refresh_detail())),
            ("Show Peer Info", lambda: (self._active_tab.set("info"),
                                        self._refresh_detail())),
        ])

    def _menu_help(self):
        self._popup([
            ("About Cat's VPN…", self._about),
        ])

    # ── dialogs ───────────────────────────────────────────────────────
    def _about(self):
        messagebox.showinfo(APP_TITLE,
            f"{APP_TITLE}\n{APP_VERSION}\n\n"
            "Hamachi-style mesh overlay — pure Python + Tkinter\n"
            "P2P engine: real UDP hole-punching\n"
            "Math: golden-ratio staggering · lemniscate host-rank\n"
            "       erfc stability · Euclidean mesh distance")

    def _prefs_dialog(self):
        dlg = Toplevel(self.root)
        dlg.title("Preferences")
        dlg.resizable(False, False)
        dlg.configure(bg=C["bg"])

        fr = Frame(dlg, bg=C["bg"], padx=16, pady=16)
        fr.pack(fill=BOTH)

        def row(r, label, var):
            Label(fr, text=label, bg=C["bg"], fg=C["txt_h"],
                  font=FONT_UI, anchor="w").grid(
                row=r, column=0, sticky="w", pady=4, padx=(0,12))
            Entry(fr, textvariable=var, width=28,
                  bg=C["entry_bg"], fg=C["txt_h"], insertbackground=C["txt_h"],
                  relief=FLAT).grid(row=r, column=1, sticky="ew")

        nv = StringVar(value=self.cfg.get("nickname",""))
        iv = StringVar(value=self.cfg.get("vpn_ip",""))
        row(0, "Nickname:",         nv)
        row(1, "VPN IP (25.x.x.x):", iv)

        def ok():
            self.cfg["nickname"] = nv.get().strip() or self.cfg["nickname"]
            self.cfg["vpn_ip"]   = iv.get().strip() or self.cfg["vpn_ip"]
            self._save_cfg()
            self.lbl_nick.config(text=self.cfg["nickname"])
            self._refresh()
            dlg.destroy()

        bf = Frame(fr, bg=C["bg"])
        bf.grid(row=2, column=0, columnspan=2, pady=(12,0), sticky="e")
        Button(bf, text="OK",     width=9, command=ok,
               bg=C["accent"], fg="white", relief=FLAT).pack(side=RIGHT, padx=4)
        Button(bf, text="Cancel", width=9, command=dlg.destroy,
               bg=C["btn"], fg=C["txt_h"], relief=FLAT).pack(side=RIGHT)

    def _create_network(self):
        name = simpledialog.askstring("Create Network", "Network name:",
                                      parent=self.root)
        if not name:
            return
        pw = simpledialog.askstring("Create Network", "Password (optional):",
                                    parent=self.root, show="*")
        nid = str(random.randint(100_000, 999_999))
        self.cfg["networks"].append({"name": name, "id": nid, "password": pw or ""})
        self._save_cfg()
        # log it
        with self.engine.lock:
            self.engine.chat_log.append(
                (time.time(), "System", f"Network '{name}' created — ID {nid}"))
        self._refresh()

    def _join_network(self):
        nid = simpledialog.askstring("Join Network", "Network ID:",
                                     parent=self.root)
        if not nid:
            return
        name = simpledialog.askstring("Join Network",
                                      "Network name (leave blank for auto):",
                                      parent=self.root) or f"Mesh {nid}"
        pw = simpledialog.askstring("Join Network", "Password (if required):",
                                    parent=self.root, show="*")
        self.cfg["networks"].append({"name": name, "id": nid, "password": pw or ""})
        self._save_cfg()
        with self.engine.lock:
            self.engine.chat_log.append(
                (time.time(), "System", f"Joined network '{name}' (ID {nid})"))
        self._refresh()

    def _leave_network(self):
        nets = self.cfg.get("networks", [])
        if not nets:
            messagebox.showinfo("Leave Network", "No networks to remove.")
            return
        choices = "\n".join(f"{i+1}. {n['name']} (ID {n['id']})"
                            for i, n in enumerate(nets))
        choice = simpledialog.askinteger(
            "Leave Network",
            f"Enter number to remove:\n\n{choices}",
            parent=self.root, minvalue=1, maxvalue=len(nets))
        if not choice:
            return
        removed = nets.pop(choice - 1)
        self.cfg["networks"] = nets
        self._save_cfg()
        with self.engine.lock:
            self.engine.chat_log.append(
                (time.time(), "System", f"Left network '{removed['name']}'"))
        self._refresh()

    def _set_power(self, on: bool):
        if on and not self.engine.running:
            self.engine.start()
        elif not on and self.engine.running:
            self.engine.stop()
        self.cfg["power"] = on
        self._save_cfg()
        self._refresh()


# ══════════════════════════════════════════════════════════════════════
#  ENTRY
# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = Tk()
    root.resizable(True, True)
    app = CatsVPNUI(root)
    root.mainloop()
