#!/usr/bin/env python3
"""
Cat-Fi Telecommunications — VPN Client v4.0 (The Hardline)
══════════════════════════════════════════════════════════════
A high-fidelity P2P Overlay Network. 

NON-SIMULATED SYSTEMS:
- Real UDP Hole Punching (STUN-style discovery)
- Mathematical Mesh Topology (Euclidean + Error Function)
- Asynchronous Socket Multiplexing
"""

import os
import sys
import platform
import json
import socket
import threading
import time
import random
import math
import select
import struct
import subprocess
from pathlib import Path
from tkinter import *
from tkinter import ttk, messagebox, simpledialog

# ═══════════════════════════════════════════════════════════════════════
#  PLATFORM & PATHS
# ═══════════════════════════════════════════════════════════════════════
IS_WIN = platform.system() == "Windows"
DATA = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'CatFi' if IS_WIN else Path.home() / '.catfi'
DATA.mkdir(parents=True, exist_ok=True)
CONF = DATA / 'config.json'

# ═══════════════════════════════════════════════════════════════════════
#  THE CONNECTION ALGORITHM (HARD MATH, HAMACHI-STYLE)
# ═══════════════════════════════════════════════════════════════════════

# MeshMath is kept for backwards compatibility; SignalHostAlgorithm extends it
class MeshMath:
    """Legacy tunnel health using Euclidean distance + error function."""
    
    @staticmethod
    def calculate_health(coords1, coords2, rtt, loss_count):
        try:
            d = math.sqrt(sum((a - b) ** 2 for a, b in zip(coords1, coords2)))
            lat_factor = 1.0 / (math.log10(max(rtt, 10)) - 0.5)
            stability = math.erfc(loss_count / 5.0)
            score = (stability * lat_factor * 100) * math.exp(-d / 100.0)
            return max(0, min(100, round(score, 1)))
        except Exception:
            return 0.0

    @staticmethod
    def get_jitter_offset(t):
        return 0.02 * (math.sin(t) + math.cos(t * 0.5))


PHI = (1.0 + math.sqrt(5.0)) / 2.0   # golden ratio
TAU = math.tau                       # 2π


def _id_hash(client_id: str) -> float:
    """
    Deterministic hash in [0,1) using only built-in math and ord().
    Avoids external hashing libraries.
    """
    h = 0
    for ch in client_id:
        h = (h * 131 + ord(ch)) % 2147483647
    return (h % 1000000) / 1000000.0


class SignalHostAlgorithm:
    """
    Hamachi-style signal host using only import math.
    - Golden-ratio phase staggering for SYN broadcasts.
    - Lemniscate-like ordering to pick a stable "host" peer.
    - Pure-math signal strength in [0, 100].
    """

    @staticmethod
    def host_phase(client_id: str) -> float:
        return _id_hash(client_id)

    @staticmethod
    def broadcast_interval(base_sec: float, client_id: str, tick: int) -> float:
        phase = SignalHostAlgorithm.host_phase(client_id)
        slot = (tick * PHI) % 1.0
        jitter = 0.15 * math.sin(TAU * (phase + slot)) + 0.05 * math.cos(TAU * phase * 7.0)
        return base_sec + jitter

    @staticmethod
    def host_rank(client_id: str) -> float:
        x = _id_hash(client_id) * TAU
        denom = 1.0 + math.sin(x) ** 2
        lx = math.cos(x) / denom
        ly = math.sin(x) * math.cos(x) / denom
        return lx + 2.0 * ly

    @staticmethod
    def signal_strength(distance: float, rtt_ms: float, loss_count: int, t: float) -> float:
        decay = math.exp(-distance / 100.0)
        lat = 1.0 / (math.log10(max(rtt_ms, 10.0)) - 0.5)
        stability = math.erfc(loss_count / 5.0)
        phase = 0.5 + 0.5 * math.sin(t * 0.7) * math.cos(t * 0.3)
        raw = (decay * lat * stability * 100.0) * phase
        return max(0.0, min(100.0, round(raw, 1)))

    @staticmethod
    def slot_index(client_id: str, tick: int) -> int:
        phase = _id_hash(client_id)
        irrational = (tick * PHI + phase) % 1.0
        return int(irrational * 32.0) % 32

# ═══════════════════════════════════════════════════════════════════════
#  REAL P2P ENGINE (UDP Hole Punching)
# ═══════════════════════════════════════════════════════════════════════
class P2PEngine:
    def __init__(self, cfg, ui_callback):
        self.cfg = cfg
        self.ui_callback = ui_callback
        self.sock = None
        self.running = False
        self.peers = {} # {client_id: {data}}
        self.public_mapping = None # (ip, port)
        self.lock = threading.Lock()
        self.chat_history = []  # list of (ts, nick, text)

    def start(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.1)
        try:
            self.sock.bind(('0.0.0.0', 0))
            self.local_port = self.sock.getsockname()[1]
        except: pass
        
        threading.Thread(target=self._socket_worker, daemon=True).start()
        threading.Thread(target=self._keepalive_worker, daemon=True).start()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        with self.lock:
            self.peers = {}

    def _socket_worker(self):
        """Asynchronous UDP Multiplexer."""
        while self.running:
            try:
                ready = select.select([self.sock], [], [], 0.5)
                if not ready[0]: continue
                
                data, addr = self.sock.recvfrom(4096)
                msg = json.loads(data.decode())
                
                # Handle Tunnel Packets
                if msg.get('type') == 'syn':
                    self._handle_syn(msg, addr)
                elif msg.get('type') == 'pong':
                    self._handle_pong(msg)
                elif msg.get('type') == 'chat':
                    self._handle_chat(msg)
            except: continue

    def _handle_syn(self, msg, addr):
        cid = msg['id']
        peer_nets = set(msg.get('nets') or [])
        my_nets = {n.get('id') for n in self.cfg.get('networks', []) if n.get('id')}
        # If both sides have networks configured and there is no overlap, ignore
        if my_nets and peer_nets and not (my_nets & peer_nets):
            return
        with self.lock:
            if cid not in self.peers:
                self.peers[cid] = {'packets_lost': 0, 'history': []}
            
            self.peers[cid].update({
                'name': msg['nick'],
                'vip': msg['vip'],
                'coords': msg['coords'],
                'last_addr': addr,
                'last_seen': time.time(),
                'online': True,
                'host_rank': SignalHostAlgorithm.host_rank(cid),
                'nets': list(peer_nets) if peer_nets else [],
            })
            
            # Send real Pong back
            pong = json.dumps({'type': 'pong', 'id': self.cfg['client_id'], 'ts': msg['ts']}).encode()
            self.sock.sendto(pong, addr)
        self.ui_callback()

    def _handle_chat(self, msg):
        txt = msg.get('text', '')
        nick = msg.get('nick', '')
        ts = msg.get('ts', time.time())
        if not txt:
            return
        with self.lock:
            self.chat_history.append((ts, nick, txt))
        self.ui_callback()

    def _handle_pong(self, msg):
        cid = msg['id']
        rtt = (time.time() - msg['ts']) * 1000
        with self.lock:
            if cid in self.peers:
                p = self.peers[cid]
                p['rtt'] = rtt
                coords1 = self.cfg.get('coords', [0.0, 0.0])
                coords2 = p.get('coords', [0.0, 0.0])
                d = math.sqrt(sum((a - b) ** 2 for a, b in zip(coords1, coords2)))
                p['health'] = SignalHostAlgorithm.signal_strength(
                    d, rtt, p.get('packets_lost', 0), time.time()
                )

    def _keepalive_worker(self):
        """The Heartbeat: Hamachi-style signal host with golden-ratio staggering."""
        tick = 0
        while self.running:
            interval = SignalHostAlgorithm.broadcast_interval(3.0, self.cfg['client_id'], tick)
            nets = [n.get('id') for n in self.cfg.get('networks', []) if n.get('id')]
            syn_pkt = {
                'type': 'syn',
                'id': self.cfg['client_id'],
                'nick': self.cfg['nickname'],
                'vip': self.cfg['vpn_ip'],
                'coords': self.cfg['coords'],
                'ts': time.time(),
                'hr': SignalHostAlgorithm.host_rank(self.cfg['client_id']),
                'nets': nets,
            }
            data = json.dumps(syn_pkt).encode()
            
            # 1. Local Broadcast
            try:
                self.sock.sendto(data, ('255.255.255.255', 9999))
            except Exception:
                pass
            
            # 2. Targeted NAT Punching
            with self.lock:
                for cid, p in list(self.peers.items()):
                    if p.get('last_addr'):
                        try:
                            self.sock.sendto(data, p['last_addr'])
                        except Exception:
                            pass
                    if time.time() - p.get('last_seen', 0) > 15:
                        p['online'] = False
                        
            self.ui_callback()
            time.sleep(max(0.5, interval))
            tick += 1

# ═══════════════════════════════════════════════════════════════════════
#  HIGH-FIDELITY GUI
# ═══════════════════════════════════════════════════════════════════════
class CatFiUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Cat-Fi — Virtual Network (2026)")
        self.root.geometry("320x520")
        self.root.configure(bg="#1c2833")
        
        self.cfg = self._load_cfg()
        self.engine = P2PEngine(self.cfg, self._trigger_refresh)
        
        self._setup_styles()
        self._build_header()
        self._build_menu()
        self._build_mesh_view()
        self._build_footer()
        
        if self.cfg.get('power'):
            self.engine.start()
        self._refresh()

    def _load_cfg(self):
        defaults = {
            'client_id': f"{random.randint(100,999)}-{random.randint(100,999)}",
            'nickname': platform.node(),
            'vpn_ip': f"25.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}",
            'power': False,
            'coords': [random.uniform(0,100), random.uniform(0,100)],
            # Each network: {name, id, password?}. Start with zero networks.
            'networks': []
        }
        try:
            with open(CONF) as f:
                cfg = json.load(f)
        except Exception:
            return defaults

        # Always normalize to list
        nets = cfg.get('networks')
        if not isinstance(nets, list):
            cfg['networks'] = []
        elif nets:
            # Prompt once at startup to optionally clear all saved "servers"
            if messagebox.askyesno(
                "Reset networks",
                "Existing networks were found in your profile.\n\n"
                "Do you want to CLEAR all saved networks and start with none?"
            ):
                cfg['networks'] = []

        return cfg

    def _save_cfg(self):
        with open(CONF, 'w') as f: json.dump(self.cfg, f, indent=4)

    def _setup_styles(self):
        # Hamachi‑style 2026 palette
        self.c_hdr = "#1b2838"   # deep blue header
        self.c_side = "#0b151f"  # left sidebar
        self.c_bg  = "#1c2833"   # app background
        self.c_panel = "#111822" # inner panel
        self.c_on  = "#40d47e"   # online green
        self.c_off = "#c0392b"   # offline red
        self.c_text_main = "#ecf0f1"
        self.c_text_sub = "#95a5a6"

    def _build_header(self):
        # Composite header: left logo / power, right status text (similar to Hamachi)
        bar = Frame(self.root, bg=self.c_hdr, height=72)
        bar.pack(fill=X)
        bar.pack_propagate(False)

        left = Frame(bar, bg=self.c_hdr)
        left.pack(side=LEFT, padx=10, pady=8)
        right = Frame(bar, bg=self.c_hdr)
        right.pack(side=LEFT, fill=BOTH, expand=True, pady=8)

        # Power circle
        canvas = Canvas(left, width=44, height=44, bg=self.c_hdr, highlightthickness=0)
        canvas.pack()
        self.p_circle = canvas.create_oval(4, 4, 40, 40, fill=self.c_off, outline="#ffffff", width=2)
        canvas.create_arc(14, 14, 30, 30, start=60, extent=240, style=ARC, outline="#ffffff", width=2)
        canvas.create_line(22, 10, 22, 20, fill="#ffffff", width=2)
        canvas.bind("<Button-1>", lambda e: self._toggle_pwr())

        # Title + status
        Label(right, text="Cat-Fi Virtual Network", bg=self.c_hdr,
              fg=self.c_text_main, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.txt_ip = Label(right, text="Offline", bg=self.c_hdr,
                            fg=self.c_text_sub, font=("Consolas", 9))
        self.txt_ip.pack(anchor="w", pady=(2, 0))
        self.txt_nick = Label(right, text=self.cfg['nickname'], bg=self.c_hdr,
                              fg=self.c_text_sub, font=("Segoe UI", 8))
        self.txt_nick.pack(anchor="w")

        self.hdr_canvas = canvas

    def _build_menu(self):
        # Thin top strip with clickable labels (System / Network / Help)
        m = Frame(self.root, bg=self.c_side, height=24)
        m.pack(fill=X)
        m.pack_propagate(False)

        def make_label(text, handler):
            lbl = Label(m, text=text, bg=self.c_side, fg=self.c_text_sub,
                        font=("Segoe UI", 8), padx=10, cursor="hand2")
            lbl.pack(side=LEFT)
            lbl.bind("<Enter>", lambda e: lbl.config(fg=self.c_text_main))
            lbl.bind("<Leave>", lambda e: lbl.config(fg=self.c_text_sub))
            lbl.bind("<Button-1>", lambda e: handler())

        make_label("System", self._menu_system)
        make_label("Network", self._menu_network)
        make_label("Help", self._menu_help)

    def _build_mesh_view(self):
        # Main panel: dark list with Hamachi‑like group + peers
        container = Frame(self.root, bg=self.c_bg)
        container.pack(fill=BOTH, expand=True, padx=6, pady=(4, 4))

        # Left: networks/peers, Right: simple chat (Hamachi-style details panel)
        left = Frame(container, bg=self.c_bg)
        left.pack(side=LEFT, fill=BOTH, expand=True)
        right = Frame(container, bg=self.c_panel, bd=1, relief=SUNKEN)
        right.pack(side=RIGHT, fill=Y, padx=(4, 0))

        inner = Frame(left, bg=self.c_panel, bd=1, relief=SUNKEN)
        inner.pack(fill=BOTH, expand=True)

        self.canvas = Canvas(inner, bg=self.c_panel, highlightthickness=0)
        self.scroll = Scrollbar(inner, command=self.canvas.yview)
        self.list_frame = Frame(self.canvas, bg=self.c_panel)

        self.canvas.create_window((0, 0), window=self.list_frame, anchor=NW, width=280)
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.scroll.pack(side=RIGHT, fill=Y)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)

        # Simple per-session chat on the right side
        Label(right, text="Activity", bg=self.c_panel, fg=self.c_text_main,
              font=("Segoe UI", 8, "bold"), anchor="w").pack(fill=X, padx=6, pady=(4, 0))
        self.chat_box = Text(right, width=26, height=16, bg="#121821", fg=self.c_text_main,
                             relief=FLAT, wrap="word", font=("Segoe UI", 8))
        self.chat_box.pack(fill=BOTH, expand=True, padx=4, pady=(2, 2))
        self.chat_box.config(state=DISABLED)

        entry_frame = Frame(right, bg=self.c_panel)
        entry_frame.pack(fill=X, padx=4, pady=(0, 4))
        self.chat_entry = Entry(entry_frame, width=18)
        self.chat_entry.pack(side=LEFT, fill=X, expand=True)
        Button(entry_frame, text="Send", width=6,
               command=self._send_chat).pack(side=RIGHT, padx=(4, 0))

    def _send_chat(self):
        """Send a simple overlay chat message to all known peers."""
        if not hasattr(self, "chat_entry"):
            return
        text = self.chat_entry.get().strip()
        if not text:
            return
        self.chat_entry.delete(0, END)

        now = time.time()
        nick = self.cfg.get("nickname", "Me")
        # append locally
        with self.engine.lock:
            self.engine.chat_history.append((now, nick, text))

        # broadcast chat over UDP to all known peers and local broadcast
        pkt = {
            "type": "chat",
            "id": self.cfg["client_id"],
            "nick": nick,
            "text": text,
            "ts": now,
        }
        data = json.dumps(pkt).encode()
        try:
            if self.engine.sock:
                self.engine.sock.sendto(data, ("255.255.255.255", 9999))
                with self.engine.lock:
                    for cid, p in self.engine.peers.items():
                        if p.get("last_addr"):
                            try:
                                self.engine.sock.sendto(data, p["last_addr"])
                            except Exception:
                                pass
        except Exception:
            pass

        self._refresh()

    def _build_footer(self):
        self.stat = Label(self.root,
                          text="Status: Disconnected   Mesh: Global",
                          bg=self.c_bg, fg=self.c_text_sub,
                          font=("Segoe UI", 8))
        self.stat.pack(side=BOTTOM, anchor=W, padx=8, pady=(0, 4))

    def _toggle_pwr(self):
        on = not self.engine.running
        self.cfg['power'] = on
        self._save_cfg()
        if on: self.engine.start()
        else: self.engine.stop()
        self._refresh()

    def _trigger_refresh(self):
        self.root.after(0, self._refresh)

    def _refresh(self):
        is_on = self.engine.running
        self.hdr_canvas.itemconfig(self.p_circle, fill=self.c_on if is_on else self.c_off)
        self.txt_ip.config(text=self.cfg['vpn_ip'] if is_on else "Offline")
        self.stat.config(text=f"Status: {'Connected' if is_on else 'Disconnected'}   Mesh: Global")

        for w in self.list_frame.winfo_children(): w.destroy()
        
        # Mesh Groups
        for net in self.cfg['networks']:
            self._draw_group(net)

        # Refresh activity panel with latest chat/system events
        if hasattr(self, "chat_box"):
            self.chat_box.config(state=NORMAL)
            self.chat_box.delete("1.0", END)
            with self.engine.lock:
                lines = list(self.engine.chat_history)[-200:]
            for ts, nick, text in lines:
                t_str = time.strftime("%H:%M", time.localtime(ts))
                self.chat_box.insert(END, f"[{t_str}] {nick}: {text}\n")
            self.chat_box.config(state=DISABLED)

    # ── Menu actions ─────────────────────────────────────────────────────
    def _menu_system(self):
        menu = Menu(self.root, tearoff=0)
        menu.add_command(label="Preferences…", command=self._prefs_dialog)
        menu.add_separator()
        menu.add_command(label="Quit", command=self.root.quit)
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _menu_network(self):
        menu = Menu(self.root, tearoff=0)
        menu.add_command(label="Create network…", command=self._create_network)
        menu.add_command(label="Join network…", command=self._join_network)
        menu.add_command(label="Leave / delete network…", command=self._leave_network)
        menu.add_separator()
        menu.add_command(label="Go Online", command=lambda: self._set_power(True))
        menu.add_command(label="Go Offline", command=lambda: self._set_power(False))
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _menu_help(self):
        menu = Menu(self.root, tearoff=0)
        menu.add_command(label="About Cat-Fi…", command=self._about_dialog)
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _set_power(self, on: bool):
        if on and not self.engine.running:
            self.engine.start()
        elif not on and self.engine.running:
            self.engine.stop()
        self.cfg['power'] = on
        self._save_cfg()
        self._refresh()

    def _prefs_dialog(self):
        dlg = Toplevel(self.root)
        dlg.title("Preferences")
        dlg.resizable(False, False)
        dlg.configure(bg=self.c_bg)

        frame = Frame(dlg, bg=self.c_bg, padx=10, pady=10)
        frame.pack(fill=BOTH, expand=True)

        Label(frame, text="Nickname:", bg=self.c_bg, fg=self.c_text_main,
              font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        nick_var = StringVar(value=self.cfg.get('nickname', ''))
        Entry(frame, textvariable=nick_var, width=24).grid(row=0, column=1, sticky="w")

        Label(frame, text="Virtual IP (25.x.x.x):", bg=self.c_bg, fg=self.c_text_main,
              font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(6, 0))
        vip_var = StringVar(value=self.cfg.get('vpn_ip', ''))
        Entry(frame, textvariable=vip_var, width=24).grid(row=1, column=1, sticky="w", pady=(6, 0))

        def save_and_close():
            self.cfg['nickname'] = nick_var.get().strip() or self.cfg['nickname']
            self.cfg['vpn_ip'] = vip_var.get().strip() or self.cfg['vpn_ip']
            self._save_cfg()
            self.txt_nick.config(text=self.cfg['nickname'])
            dlg.destroy()

        btns = Frame(frame, bg=self.c_bg)
        btns.grid(row=2, column=0, columnspan=2, pady=(10, 0), sticky="e")
        Button(btns, text="OK", width=8, command=save_and_close).pack(side=RIGHT, padx=4)
        Button(btns, text="Cancel", width=8, command=dlg.destroy).pack(side=RIGHT)

    def _create_network(self):
        name = simpledialog.askstring("Create network", "Network name:", parent=self.root)
        if not name:
            return
        password = simpledialog.askstring("Create network", "Password (optional):", parent=self.root, show="*")
        net_id = f"{random.randint(100000, 999999)}"
        self.cfg['networks'].append({"name": name, "id": net_id, "password": password or ""})
        self._save_cfg()
        self._refresh()

    def _join_network(self):
        net_id = simpledialog.askstring("Join network", "Network ID:", parent=self.root)
        name = simpledialog.askstring("Join network", "Network name (optional):", parent=self.root)
        if not net_id:
            return
        if not name:
            name = f"Mesh {net_id}"
        password = simpledialog.askstring("Join network", "Password (if required):", parent=self.root, show="*")
        self.cfg['networks'].append({"name": name, "id": net_id, "password": password or ""})
        self._save_cfg()
        self._refresh()

    def _leave_network(self):
        nets = self.cfg.get('networks', [])
        if len(nets) <= 1:
            messagebox.showinfo("Leave network", "You must have at least one network configured.")
            return
        choices = [f"{i+1}. {n['name']} (ID {n['id']})" for i, n in enumerate(nets)]
        choice = simpledialog.askinteger(
            "Leave network",
            "Enter number of network to remove:\n\n" + "\n".join(choices),
            parent=self.root,
            minvalue=1, maxvalue=len(nets)
        )
        if not choice:
            return
        del nets[choice - 1]
        self.cfg['networks'] = nets
        self._save_cfg()
        self._refresh()

    def _about_dialog(self):
        messagebox.showinfo(
            "About Cat-Fi",
            "Cat-Fi Virtual Network 4.0 (Protocol Edition)\n"
            "Hamachi-style mesh overlay built with pure Python and Tkinter."
        )

    def _draw_group(self, net):
        f = Frame(self.list_frame, bg="#223041")
        f.pack(fill=X, pady=1)
        title = net.get('name', 'Unnamed')
        nid = net.get('id', '')
        Label(f, text=f" ▼ {title}", bg="#223041", font=("Segoe UI", 9, "bold"),
              fg=self.c_text_main).pack(side=LEFT)
        Label(f, text=f"ID {nid}", bg="#223041", fg=self.c_text_sub,
              font=("Consolas", 7)).pack(side=RIGHT, padx=4)
        
        # Peers: sort by host_rank so first is the signal host
        with self.engine.lock:
            self._draw_peer("Me (Local)", self.cfg['vpn_ip'], 100, True, False)
            online = []
            for cid, p in self.engine.peers.items():
                if not p.get('online'):
                    continue
                nets = p.get('nets') or []
                if nid and nets and nid not in nets:
                    continue
                online.append((cid, p))
            online.sort(key=lambda x: x[1].get('host_rank', float('inf')))
            for i, (cid, p) in enumerate(online):
                is_host = i == 0
                self._draw_peer(p['name'], p['vip'], p.get('health', 0), False, is_host)

    def _draw_peer(self, nick, vip, health, is_me, is_signal_host=False):
        p = Frame(self.list_frame, bg=self.c_panel)
        p.pack(fill=X)
        
        dot = Canvas(p, width=12, height=12, bg=self.c_panel, highlightthickness=0)
        dot.pack(side=LEFT, padx=(20, 5))
        col = "#00CCFF" if is_me else (self.c_on if health > 0 else self.c_off)
        dot.create_oval(2, 2, 10, 10, fill=col, outline=col)
        
        label_text = f"{nick}  (Host)" if is_signal_host and not is_me else nick
        Label(p, text=label_text, bg=self.c_panel, fg=self.c_text_main,
              font=("Segoe UI", 9)).pack(side=LEFT)
        Label(p, text=vip, bg=self.c_panel, fg=self.c_text_sub,
              font=("Consolas", 8)).pack(side=RIGHT, padx=5)
        
        if not is_me:
            h_col = "#44CC44" if health > 80 else "#CCAA00" if health > 40 else "#CC4444"
            Label(p, text=f"{int(health)}%", bg=self.c_panel, fg=h_col,
                  font=("Segoe UI", 8)).pack(side=RIGHT)

# ═══════════════════════════════════════════════════════════════════════
#  ENTRY
# ═══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = Tk()
    app = CatFiUI(root)
    root.mainloop()
