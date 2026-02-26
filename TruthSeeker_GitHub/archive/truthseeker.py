"""
TruthSeeker — Video URL Discovery Engine
=========================================
Feed it a seed URL with a numbered filename (e.g. EFTA01648642.pdf).
It increments the number and checks for .mp4 / .mov variants, collecting
valid URLs as clickable links and optionally exporting them to PDF / HTML.

Dependencies: customtkinter, requests, fpdf2
"""

import itertools
import json
import os
import random
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from urllib.parse import urlparse, unquote

import requests
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox

# ── App directory (works for both script and frozen PyInstaller exe) ──────────
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "truthseeker_config.json")

# ── Rotating User-Agent Pool ──────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]

# ── Default config values ─────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "last_url":   "",
    "max_scan":   "500",
    "max_miss":   "50",
    "delay_min":  "3",
    "delay_max":  "7",
    "ext_mp4":    True,
    "ext_mov":    True,
}

# ── Appearance ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT   = "#e94560"
ACCENT2  = "#0f3460"
BG_DARK  = "#0a1628"
BG_MID   = "#0d1b3e"
BG_PANEL = "#16213e"
TEXT_DIM = "#888888"
TEXT_LNK = "#7ec8e3"


# ── Main Application ──────────────────────────────────────────────────────────
class TruthSeekerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TruthSeeker — Video URL Discovery Engine")
        self.geometry("1020x760")
        self.minsize(820, 620)
        self.configure(fg_color=BG_DARK)

        # State
        self.scanning   = False
        self.valid_urls: list[str] = []
        self.base_url   = ""
        self.prefix     = ""
        self.num_width  = 0
        self.base_num   = 0

        self._build_ui()
        self._load_config()   # populate fields from saved config

    # ── UI Construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        # ---- Header ----
        hdr = tk.Frame(self, bg="#111827")
        hdr.pack(fill="x")

        tk.Label(
            hdr, text="🔍  TruthSeeker",
            font=("Segoe UI", 26, "bold"),
            fg=ACCENT, bg="#111827"
        ).pack(side="left", padx=24, pady=14)

        tk.Label(
            hdr, text="Video URL Discovery Engine",
            font=("Segoe UI", 12),
            fg=TEXT_DIM, bg="#111827"
        ).pack(side="left", pady=14)

        self.lbl_config_saved = tk.Label(
            hdr, text="", font=("Segoe UI", 10),
            fg="#4caf50", bg="#111827"
        )
        self.lbl_config_saved.pack(side="right", padx=18)

        # ---- Body ----
        body = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=12)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # URL row
        url_row = ctk.CTkFrame(body, fg_color="transparent")
        url_row.pack(fill="x", padx=18, pady=(14, 4))

        ctk.CTkLabel(url_row, text="Seed URL", font=ctk.CTkFont(size=12),
                     text_color=TEXT_DIM).pack(anchor="w")

        inp_row = ctk.CTkFrame(url_row, fg_color="transparent")
        inp_row.pack(fill="x", pady=(3, 0))

        self.url_var = tk.StringVar()
        self.url_entry = ctk.CTkEntry(
            inp_row, textvariable=self.url_var, height=40,
            font=ctk.CTkFont(size=12),
            placeholder_text="https://example.gov/files/EFTA01648642.pdf"
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _: self.parse_url())

        # Right-click context menu for paste
        self._url_menu = tk.Menu(self, tearoff=0, bg="#1e293b", fg="white",
                                 activebackground=ACCENT, activeforeground="white",
                                 borderwidth=1, relief="flat")
        self._url_menu.add_command(label="Paste", command=self._paste_url)
        self._url_menu.add_command(label="Clear", command=lambda: self.url_var.set(""))
        self.url_entry.bind("<Button-3>", self._show_url_menu)

        ctk.CTkButton(
            inp_row, text="Parse", width=90, height=40,
            fg_color=ACCENT2, hover_color=ACCENT,
            command=self.parse_url
        ).pack(side="right")

        # ---- Options panel ----
        opts = ctk.CTkFrame(body, fg_color="transparent")
        opts.pack(fill="x", padx=18, pady=(6, 6))

        # Left — scan settings
        lf = ctk.CTkFrame(opts, fg_color=BG_MID, corner_radius=8)
        lf.pack(side="left", fill="x", expand=True, padx=(0, 8))

        for c in range(10):
            lf.grid_columnconfigure(c, weight=0)

        ctk.CTkLabel(lf, text="Detected base:", text_color=TEXT_DIM,
                     font=ctk.CTkFont(size=11)).grid(
            row=0, column=0, padx=12, pady=(10, 2), sticky="w")
        self.base_lbl = ctk.CTkLabel(lf, text="—", text_color=TEXT_LNK,
                                     font=ctk.CTkFont(size=11))
        self.base_lbl.grid(row=0, column=1, columnspan=9, padx=4, pady=(10, 2), sticky="w")

        # Row 1 — numeric fields
        num_fields = [
            ("Start #",          "start_e",     "110", ""),
            ("Max scan",         "max_e",        "70", "500"),
            ("Stop after misses","miss_e",        "70", "50"),
            ("Min delay (s)",    "delay_min_e",   "65", "3"),
            ("Max delay (s)",    "delay_max_e",   "65", "7"),
        ]
        col = 0
        for label, attr, w, default in num_fields:
            ctk.CTkLabel(lf, text=label + ":", text_color=TEXT_DIM,
                         font=ctk.CTkFont(size=11)).grid(
                row=1, column=col, padx=(10, 2), pady=(4, 10), sticky="w")
            col += 1
            e = ctk.CTkEntry(lf, width=int(w), height=30)
            if default:
                e.insert(0, default)
            e.grid(row=1, column=col, padx=(0, 8), pady=(4, 10), sticky="w")
            setattr(self, attr, e)
            col += 1

        # Right — extension checkboxes
        rf = ctk.CTkFrame(opts, fg_color=BG_MID, corner_radius=8)
        rf.pack(side="right")

        ctk.CTkLabel(rf, text="Extensions", text_color=TEXT_DIM,
                     font=ctk.CTkFont(size=11)).pack(padx=20, pady=(10, 4))
        self.cb_mp4 = ctk.CTkCheckBox(rf, text=".mp4", font=ctk.CTkFont(size=13))
        self.cb_mp4.pack(padx=20, pady=2)
        self.cb_mp4.select()
        self.cb_mov = ctk.CTkCheckBox(rf, text=".mov", font=ctk.CTkFont(size=13))
        self.cb_mov.pack(padx=20, pady=(2, 10))
        self.cb_mov.select()

        # ---- Session Cookie row (paste from browser DevTools) ----
        ck_row = ctk.CTkFrame(body, fg_color=BG_MID, corner_radius=8)
        ck_row.pack(fill="x", padx=18, pady=(0, 6))

        ctk.CTkLabel(ck_row, text="Session Cookie:",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM
                     ).pack(side="left", padx=(12, 6), pady=8)

        self.cookie_var = tk.StringVar()
        self.cookie_entry = ctk.CTkEntry(
            ck_row, textvariable=self.cookie_var, height=32,
            font=ctk.CTkFont(size=11),
            placeholder_text=(
                "Paste browser Cookie header here  "
                "(F12 → Network → request → Headers → Cookie:)"
            )
        )
        self.cookie_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=8)

        # Right-click paste on cookie field too
        self._ck_menu = tk.Menu(self, tearoff=0, bg="#1e293b", fg="white",
                                activebackground=ACCENT, activeforeground="white")
        self._ck_menu.add_command(label="Paste", command=self._paste_cookie)
        self._ck_menu.add_command(label="Clear", command=lambda: self.cookie_var.set(""))
        self.cookie_entry.bind("<Button-3>", lambda e: self._ck_menu.tk_popup(e.x_root, e.y_root))

        ctk.CTkLabel(ck_row, text="Optional — bypasses JS age gates",
                     font=ctk.CTkFont(size=10), text_color="#555"
                     ).pack(side="right", padx=12)

        # ---- Control buttons ----
        ctrl = ctk.CTkFrame(body, fg_color="transparent")
        ctrl.pack(fill="x", padx=18, pady=(0, 6))

        self.btn_scan = ctk.CTkButton(
            ctrl, text="▶  Start Scan", height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=ACCENT, hover_color="#c0304d",
            command=self.toggle_scan, state="disabled"
        )
        self.btn_scan.pack(side="left", padx=(0, 8))

        self.btn_pdf = ctk.CTkButton(
            ctrl, text="💾  Save PDF", height=44,
            font=ctk.CTkFont(size=14),
            fg_color=ACCENT2, hover_color="#1a5276",
            command=self.save_pdf, state="disabled"
        )
        self.btn_pdf.pack(side="left", padx=(0, 8))

        self.btn_html = ctk.CTkButton(
            ctrl, text="🌐  Save HTML", height=44,
            font=ctk.CTkFont(size=14),
            fg_color="#1a472a", hover_color="#27ae60",
            command=self.save_html, state="disabled"
        )
        self.btn_html.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            ctrl, text="Clear", height=44, width=80,
            fg_color="#333", hover_color="#555",
            command=self.clear_results
        ).pack(side="left")

        self.lbl_status = ctk.CTkLabel(
            ctrl, text="Ready", text_color=TEXT_DIM,
            font=ctk.CTkFont(size=12)
        )
        self.lbl_status.pack(side="right")

        # ---- Progress bar ----
        self.progress = ctk.CTkProgressBar(body, height=7, progress_color=ACCENT)
        self.progress.pack(fill="x", padx=18, pady=(0, 6))
        self.progress.set(0)

        # ---- Results area ----
        res_frame = ctk.CTkFrame(body, fg_color=BG_MID, corner_radius=8)
        res_frame.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        res_hdr = ctk.CTkFrame(res_frame, fg_color="transparent")
        res_hdr.pack(fill="x", padx=10, pady=(6, 0))

        self.lbl_count = ctk.CTkLabel(
            res_hdr, text="0 valid URLs found",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT_LNK
        )
        self.lbl_count.pack(side="left")

        ctk.CTkLabel(res_hdr, text="(click any link to open in browser)",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(side="left", padx=10)

        txt_wrap = tk.Frame(res_frame, bg=BG_MID)
        txt_wrap.pack(fill="both", expand=True, padx=6, pady=(4, 6))

        self.res_text = tk.Text(
            txt_wrap, bg=BG_DARK, fg="#cccccc",
            font=("Consolas", 11), wrap="word",
            cursor="arrow", selectbackground="#1a5276",
            relief="flat", borderwidth=0, padx=10, pady=6
        )
        sb = tk.Scrollbar(txt_wrap, command=self.res_text.yview, bg=BG_MID)
        self.res_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.res_text.pack(fill="both", expand=True)

        self.res_text.tag_configure("link",    foreground=TEXT_LNK, underline=True)
        self.res_text.tag_configure("info",    foreground=TEXT_DIM)
        self.res_text.tag_configure("success", foreground="#4caf50")
        self.res_text.tag_configure("error",   foreground="#e57373")

        self.res_text.tag_bind("link", "<Enter>",    lambda e: self.res_text.config(cursor="hand2"))
        self.res_text.tag_bind("link", "<Leave>",    lambda e: self.res_text.config(cursor="arrow"))
        self.res_text.tag_bind("link", "<Button-1>", self._open_link)

        self.res_text.config(state="disabled")
        self._append("TruthSeeker ready. Right-click the URL field to paste, then click Parse.\n", "info")

    # ── Config persistence ────────────────────────────────────────────────────
    def _load_config(self):
        cfg = DEFAULT_CONFIG.copy()
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg.update(json.load(f))
        except Exception:
            pass  # silently fall back to defaults

        if cfg["last_url"]:
            self.url_var.set(cfg["last_url"])
        self._set_entry(self.max_e,       cfg["max_scan"])
        self._set_entry(self.miss_e,      cfg["max_miss"])
        self._set_entry(self.delay_min_e, cfg["delay_min"])
        self._set_entry(self.delay_max_e, cfg["delay_max"])
        if cfg.get("session_cookie"):
            self.cookie_var.set(cfg["session_cookie"])
        if cfg["ext_mp4"]:
            self.cb_mp4.select()
        else:
            self.cb_mp4.deselect()
        if cfg["ext_mov"]:
            self.cb_mov.select()
        else:
            self.cb_mov.deselect()

    def _save_config(self):
        cfg = {
            "last_url":    self.url_var.get().strip(),
            "max_scan":    self.max_e.get(),
            "max_miss":    self.miss_e.get(),
            "delay_min":   self.delay_min_e.get(),
            "delay_max":   self.delay_max_e.get(),
            "ext_mp4":     bool(self.cb_mp4.get()),
            "ext_mov":     bool(self.cb_mov.get()),
            "session_cookie": self.cookie_var.get().strip(),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            self.lbl_config_saved.configure(text="✔ Settings saved")
            self.after(3000, lambda: self.lbl_config_saved.configure(text=""))
        except Exception:
            pass

    @staticmethod
    def _set_entry(widget, value: str):
        widget.delete(0, "end")
        widget.insert(0, value)

    def _paste_cookie(self):
        try:
            self.cookie_var.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    # ── Right-click paste helpers ─────────────────────────────────────────────
    def _show_url_menu(self, event):
        self._url_menu.tk_popup(event.x_root, event.y_root)

    def _paste_url(self):
        try:
            text = self.clipboard_get()
            self.url_var.set(text.strip())
        except tk.TclError:
            pass

    # ── Parse URL ─────────────────────────────────────────────────────────────
    def parse_url(self):
        raw = self.url_var.get().strip()
        if not raw:
            return

        parsed = urlparse(raw)
        path   = parsed.path
        fname  = unquote(path.rstrip("/").split("/")[-1])

        if "." not in fname:
            messagebox.showerror("Parse Error", "Filename has no extension.")
            return

        name_no_ext = fname.rsplit(".", 1)[0]
        m = re.match(r'^(.*?)(\d+)$', name_no_ext)
        if not m:
            messagebox.showerror("Parse Error",
                "Could not detect a numeric suffix in the filename.\n"
                "Expected something like  EFTA01648642.pdf")
            return

        self.prefix    = m.group(1)
        num_str        = m.group(2)
        self.num_width = len(num_str)
        self.base_num  = int(num_str)

        base_path     = "/".join(path.split("/")[:-1]) + "/"
        self.base_url = f"{parsed.scheme}://{parsed.netloc}{base_path}"

        display = f"{self.base_url}{self.prefix}[N]"
        if len(display) > 72:
            display = "…" + display[-69:]
        self.base_lbl.configure(text=display)

        self.start_e.delete(0, "end")
        self.start_e.insert(0, str(self.base_num + 1))
        self.btn_scan.configure(state="normal")

        self._append(
            f"\n✔ Parsed OK\n"
            f"  Base URL : {self.base_url}\n"
            f"  Prefix   : {self.prefix}\n"
            f"  Number   : {self.base_num}  (zero-padded to {self.num_width} digits)\n"
            f"  Will scan from: {self.prefix}{str(self.base_num+1).zfill(self.num_width)}…\n\n",
            "info"
        )

    # ── Scan Control ──────────────────────────────────────────────────────────
    def toggle_scan(self):
        if self.scanning:
            self.scanning = False
            self.btn_scan.configure(text="▶  Start Scan", fg_color=ACCENT)
        else:
            self._start_scan()

    def _start_scan(self):
        exts = []
        if self.cb_mp4.get(): exts.append(".mp4")
        if self.cb_mov.get(): exts.append(".mov")
        if not exts:
            messagebox.showerror("Error", "Select at least one extension.")
            return

        try:
            start     = int(self.start_e.get())
            max_n     = int(self.max_e.get())
            max_mis   = int(self.miss_e.get())
            delay_min = float(self.delay_min_e.get())
            delay_max = float(self.delay_max_e.get())
        except ValueError:
            messagebox.showerror("Error", "All fields must be valid numbers.")
            return

        if delay_min > delay_max:
            delay_min, delay_max = delay_max, delay_min  # swap silently

        self._save_config()   # persist settings before scan starts

        self.scanning = True
        self.btn_scan.configure(text="⏹  Stop", fg_color="#555")
        self.btn_pdf.configure(state="disabled")
        self.btn_html.configure(state="disabled")
        self.progress.set(0)
        self._append(
            f"--- Scan started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            f"  |  random delay {delay_min}–{delay_max}s per request ---\n",
            "info"
        )

        threading.Thread(
            target=self._worker,
            args=(start, max_n, max_mis, delay_min, delay_max, exts),
            daemon=True
        ).start()

    # ── Session / Age-Gate Initialisation ────────────────────────────────────
    def _init_session(self, session: requests.Session, base_url: str):
        """
        Visit the base URL and auto-submit any age / content-verification gate.
        The resulting cookies persist in `session` for all subsequent requests.
        """
        import html as html_mod
        from urllib.parse import urljoin

        # Pre-set common age-gate cookies used by government / archive sites
        p = urlparse(base_url)
        domain = p.netloc
        for name, value in [('age_verified','1'), ('ageGate','true'),
                             ('disclaimer_accepted','true'), ('ack','1'),
                             ('agreed','1'), ('over18','yes')]:
            session.cookies.set(name, value, domain=domain)

        try:
            session.headers.update({'User-Agent': USER_AGENTS[0]})
            r = session.get(base_url, timeout=12, allow_redirects=True)

            gate_kw = ('agree', 'i agree', 'enter', 'verify', 'confirm',
                       'continue', 'accept', 'certify', 'acknowledge', 'proceed')

            form_re  = re.compile(r'<form([^>]*)>(.*?)</form>', re.I | re.S)
            input_re = re.compile(r'<input([^>]*)>', re.I)
            attr_re  = re.compile(r'([\w-]+)=["\']([^"\']*)["\']', re.I)

            for fm in form_re.finditer(r.text):
                body = fm.group(2)
                if not any(kw in body.lower() for kw in gate_kw):
                    continue

                # Collect hidden + submit input values
                post_data = {}
                for im in input_re.finditer(body):
                    attrs = dict(attr_re.findall(im.group(1)))
                    t = attrs.get('type', 'text').lower()
                    n = attrs.get('name', '')
                    v = html_mod.unescape(attrs.get('value', ''))
                    if n and t in ('hidden', 'submit', 'button'):
                        post_data[n] = v

                fa = dict(attr_re.findall(fm.group(1)))
                action = urljoin(r.url, fa.get('action', r.url))
                if fa.get('method', 'post').lower() == 'post':
                    session.post(action, data=post_data, timeout=10)
                else:
                    session.get(action, params=post_data, timeout=10)
                break  # only handle the first gate

        except Exception:
            pass  # silently continue — scan proceeds either way

    # ── Scan Worker ───────────────────────────────────────────────────────────
    def _worker(self, start_num: int, max_n: int, max_mis: int,
                delay_min: float, delay_max: float, exts: list[str]):
        session     = requests.Session()
        agent_cycle = itertools.cycle(USER_AGENTS)
        found       = 0
        consecutive = 0

        # ── Inject browser cookies (bypasses JS age gates) ────────────────────
        raw_cookie = self.cookie_var.get().strip()
        if raw_cookie:
            domain = urlparse(self.base_url).netloc
            for pair in raw_cookie.split(';'):
                pair = pair.strip()
                if '=' in pair:
                    name, _, value = pair.partition('=')
                    session.cookies.set(name.strip(), value.strip(), domain=domain)
            self.after(0, self._append,
                       f"[Session] {len(raw_cookie.split(';'))} browser cookie(s) injected.\n",
                       "info")
        else:
            # No manual cookie — fall back to auto age-gate handling
            seed_url = (f"{self.base_url}{self.prefix}"
                        f"{str(self.base_num).zfill(self.num_width)}{exts[0]}")
            self.after(0, self._append,
                       "[Session] No cookie — attempting auto gate handling…\n", "info")
            self._init_session(session, seed_url)

        # ── Scan range preview (helps catch wrong start numbers early) ─────────
        first_scan_url = (f"{self.base_url}{self.prefix}"
                          f"{str(start_num).zfill(self.num_width)}{exts[0]}")
        last_scan_url  = (f"{self.base_url}{self.prefix}"
                          f"{str(start_num + max_n - 1).zfill(self.num_width)}{exts[-1]}")
        self.after(0, self._append,
                   f"[Range] First : {first_scan_url}\n"
                   f"[Range] Last  : {last_scan_url}\n", "info")

        # ── Age-gate warmup using the ORIGINAL seed URL ────────────────────────
        # We always use the seed URL (self.base_num) because the user confirmed
        # it exists and triggers the gate — not the scan-start URL which may
        # be a different number entirely.
        seed_url = (f"{self.base_url}{self.prefix}"
                    f"{str(self.base_num).zfill(self.num_width)}{exts[0]}")
        self.after(0, self._append,
                   f"[Session] Handling age gate via seed URL…\n", "info")
        self._init_session(session, seed_url)
        self.after(0, self._append, "[Session] Gate handled. Starting scan…\n\n", "info")

        for i in range(max_n):
            if not self.scanning:
                break

            cur_num  = start_num + i
            num_str  = str(cur_num).zfill(self.num_width)
            hit_this = False

            for ext in exts:
                if not self.scanning:
                    break

                url   = f"{self.base_url}{self.prefix}{num_str}{ext}"
                agent = next(agent_cycle)
                session.headers.update({"User-Agent": agent})

                # Random inter-request delay
                wait = round(random.uniform(delay_min, delay_max), 1)
                self.after(0, self.lbl_status.configure, {
                    "text": f"[{wait}s] Checking …{self.prefix}{num_str}{ext}  |  Found: {found}"
                })
                self.after(0, self.progress.set, (i + 1) / max_n)

                # Interruptible sleep in 100ms slices
                for _ in range(int(wait * 10)):
                    if not self.scanning:
                        break
                    time.sleep(0.1)

                if not self.scanning:
                    break

                try:
                    r = session.head(url, timeout=10, allow_redirects=True)
                    if r.status_code in (200, 206):
                        ct = r.headers.get('Content-Type', '').lower().split(';')[0].strip()
                        cl = r.headers.get('Content-Length', None)

                        # ── Soft-404 detection ────────────────────────────────
                        # Many servers return HTTP 200 for missing files but
                        # serve an HTML error page instead of the actual file.
                        # Reject any explicitly HTML/text content-type.
                        html_types = ('text/html', 'text/plain', 'text/xml',
                                      'application/xhtml+xml')
                        if ct in html_types:
                            pass  # soft-404: server returned an error page
                        # If Content-Length is known and tiny, it's an error page
                        elif cl is not None and int(cl) < 5_000:
                            pass  # < 5 KB — can't be a real video file
                        else:
                            # Genuine hit
                            found    += 1
                            hit_this  = True
                            self.valid_urls.append(url)
                            self.after(0, self._append_link, url)
                            count_text = f"{found} valid URL{'s' if found != 1 else ''} found"
                            self.after(0, lambda t=count_text: self.lbl_count.configure(text=t))
                except Exception:
                    pass  # network error → treat as miss

            if hit_this:
                consecutive = 0
            else:
                consecutive += 1
                if consecutive >= max_mis:
                    self.after(0, self._append,
                               f"\n[Auto-stopped: {max_mis} consecutive numbers with no results]\n",
                               "info")
                    break

        self.after(0, self._scan_done, found)

    def _scan_done(self, found: int):
        self.scanning = False
        self.btn_scan.configure(text="▶  Start Scan", fg_color=ACCENT)
        self.progress.set(1.0)
        self.lbl_status.configure(text=f"Done — {found} valid URL{'s' if found != 1 else ''} found")
        self._append(
            f"--- Scan finished {datetime.now().strftime('%H:%M:%S')} — "
            f"{found} URL{'s' if found != 1 else ''} found ---\n",
            "success" if found else "info"
        )
        if found:
            self.btn_pdf.configure(state="normal")
            self.btn_html.configure(state="normal")

    # ── Results Helpers ───────────────────────────────────────────────────────
    def _append(self, text: str, tag: str = ""):
        self.res_text.config(state="normal")
        self.res_text.insert("end", text, tag)
        self.res_text.see("end")
        self.res_text.config(state="disabled")

    def _append_link(self, url: str):
        self.res_text.config(state="normal")
        self.res_text.insert("end", url + "\n", "link")
        self.res_text.see("end")
        self.res_text.config(state="disabled")

    def _open_link(self, event):
        idx   = self.res_text.index(f"@{event.x},{event.y}")
        start = self.res_text.index(f"{idx} linestart")
        end   = self.res_text.index(f"{idx} lineend")
        url   = self.res_text.get(start, end).strip()
        if url.startswith("http"):
            webbrowser.open(url)

    def clear_results(self):
        self.res_text.config(state="normal")
        self.res_text.delete("1.0", "end")
        self.res_text.config(state="disabled")
        self.valid_urls.clear()
        self.lbl_count.configure(text="0 valid URLs found")
        self.btn_pdf.configure(state="disabled")
        self.btn_html.configure(state="disabled")
        self.progress.set(0)
        self.lbl_status.configure(text="Ready")
        self._append("Cleared. Paste a seed URL and click Parse.\n", "info")

    # ── HTML Export ───────────────────────────────────────────────────────────
    def save_html(self):
        if not self.valid_urls:
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("HTML files", "*.html")],
            initialfile=f"TruthSeeker_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        )
        if not filepath:
            return

        ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        base = f"{self.base_url}{self.prefix}[N]"
        rows = "\n".join(
            f'    <tr><td class="num">{i+1}</td>'
            f'<td><a href="{url}" target="_blank">{url}</a></td></tr>'
            for i, url in enumerate(self.valid_urls)
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TruthSeeker Results</title>
  <style>
    *  {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0a1628; color: #ccc; font-family: 'Segoe UI', sans-serif; padding: 32px; }}
    h1   {{ color: #e94560; font-size: 1.8rem; margin-bottom: 6px; }}
    .meta {{ color: #888; font-size: .85rem; margin-bottom: 24px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th    {{ background: #0f3460; color: #7ec8e3; text-align: left;
             padding: 10px 14px; font-size: .85rem; letter-spacing: .05em; }}
    td    {{ padding: 9px 14px; border-bottom: 1px solid #16213e;
             font-size: .88rem; word-break: break-all; }}
    td.num {{ color: #555; width: 48px; text-align: right; }}
    a     {{ color: #7ec8e3; text-decoration: none; }}
    a:hover {{ color: #e94560; text-decoration: underline; }}
    tr:hover {{ background: #0d1b3e; }}
    .footer {{ margin-top: 24px; color: #555; font-size: .75rem; }}
  </style>
</head>
<body>
  <h1>🔍 TruthSeeker — Valid Video URLs</h1>
  <p class="meta">
    Generated: {ts} &nbsp;|&nbsp;
    Base: <code>{base}</code> &nbsp;|&nbsp;
    <strong style="color:#4caf50">{len(self.valid_urls)} active URL(s)  — 404s excluded</strong>
  </p>
  <table>
    <thead>
      <tr><th>#</th><th>URL (click to open)</th></tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
  <p class="footer">Generated by TruthSeeker &mdash; only HTTP 200/206 responses are listed.</p>
</body>
</html>
"""
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
            messagebox.showinfo("Saved", f"HTML saved:\n{filepath}")
            self._append(f"\n[HTML saved → {filepath}]\n", "success")
        except Exception as ex:
            messagebox.showerror("Error", f"Could not save HTML:\n{ex}")

    # ── PDF Export ────────────────────────────────────────────────────────────
    def save_pdf(self):
        if not self.valid_urls:
            return

        try:
            from fpdf import FPDF
        except ImportError:
            messagebox.showerror("Missing library",
                "fpdf2 is not installed.\nRun:  pip install fpdf2")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=f"TruthSeeker_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        )
        if not filepath:
            return

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(233, 69, 96)
        pdf.cell(0, 12, "TruthSeeker — Valid Video URLs", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 7,
                 f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
                 f"Base: {self.base_url}{self.prefix}[N]  |  "
                 f"{len(self.valid_urls)} active URL(s) — 404s excluded",
                 new_x="LMARGIN", new_y="NEXT")

        pdf.ln(2)
        pdf.set_draw_color(233, 69, 96)
        pdf.set_line_width(0.5)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(5)

        pdf.set_font("Courier", "", 9)
        pdf.set_text_color(0, 80, 180)
        for url in self.valid_urls:
            pdf.cell(0, 7, url, new_x="LMARGIN", new_y="NEXT", link=url)

        pdf.output(filepath)
        messagebox.showinfo("Saved", f"PDF saved:\n{filepath}")
        self._append(f"\n[PDF saved → {filepath}]\n", "success")


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = TruthSeekerApp()
    app.mainloop()
