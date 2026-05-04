"""
summarize_gui.py — Launcher Tkinter pour `summarize.py` (Arsenal Intelligence Unit).

Pilote le pipeline de résumés Claude Sonnet via subprocess. Affiche progression
live, console intégrée, raccourcis vers Discord/dossiers/audit. Auto-restart
optionnel si crash.

Lancement : double-clic sur `start_summarize_gui.vbs` (sans console) ou
            `python summarize_gui.py` depuis un terminal.
"""

from __future__ import annotations

import os
import re
import sys
import json
import signal
import queue
import threading
import subprocess
import urllib.error
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime
from pathlib import Path

import psutil

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import claude_usage
from claude_usage import (
    Quota, QuotaState, fetch_usage,
    load_state, save_state, has_cookie, save_cookie, delete_cookie,
    UsageError, CookieMissingError, CookieExpiredError, EndpointChangedError,
    NetworkError, fmt_duration,
)
from arsenal_config import load_engine_pref, save_engine_pref

HERE = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable
SUMMARIZE_PY = HERE / "summarize.py"
AUDIT_PY = HERE / "arsenal_audit.py"
SUMMARY_DIR = HERE / "03_ai_summaries"
ARSENAL_GUILD_ID = "1466806132998672466"  # Migration 2026-04-29 — était guild Veille 1475846763909873727
LOGS_CHANNEL_ID = "1493760267300110466"   # Migration 2026-04-29 — était #logs Veille 1475955504332411187
DISCORD_LOGS_URL = f"https://discord.com/channels/{ARSENAL_GUILD_ID}/{LOGS_CHANNEL_ID}"
DISCORD_API_URL = f"https://discord.com/api/v10/channels/{LOGS_CHANNEL_ID}/messages"
ANTHROPIC_STATUS_URL = "https://status.claude.com/"
ANTHROPIC_BILLING_URL = "https://console.anthropic.com/settings/billing"
CLAUDE_AI_USAGE_URL = "https://claude.ai/settings/usage"
USD_TO_EUR = 0.92

CONSOLE_MAX_LINES = 400
RESTART_DELAY_SECONDS = 10
RESTART_MAX_DEFAULT = 10
DRAIN_INTERVAL_MS = 100
QUOTA_REFRESH_MS = 60_000

DISCORD_COLOR_GREEN = 0x2ECC71
DISCORD_COLOR_ORANGE = 0xE67E22
DISCORD_COLOR_RED = 0xE74C3C
DISCORD_COLOR_BLUE = 0x3498DB

PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\] (TEXTE|IMAGE) (\S+) (\S+)")
TOTAL_RE = re.compile(r"À traiter : \d+ lignes CSV → (\d+) tâches prêtes")
OK_RE = re.compile(r"  OK — ")
FAIL_RE = re.compile(r"  ERREUR ")
COST_RE = re.compile(r"total: \$([\d.]+)")


class State:
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    CRASHED_WAITING = "crashed_waiting"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Arsenal — Summarize Launcher")
        self.root.geometry("780x720")
        self.root.minsize(640, 600)

        self.state = State.IDLE
        self.proc: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None
        self.line_queue: queue.Queue[str] = queue.Queue()
        self.start_time: float | None = None
        self.restart_count = 0
        self.restart_after_id: str | None = None
        self.user_requested_stop = False

        self.done = 0
        self.total = 0
        self.success_count = 0
        self.fail_count = 0
        self.total_cost_usd = 0.0

        self.var_engine = tk.StringVar(value=load_engine_pref())
        self.var_text_only = tk.BooleanVar(value=False)
        self.var_re_summarize = tk.BooleanVar(value=False)
        self.var_target_id = tk.StringVar(value="")
        self.var_no_wait = tk.BooleanVar(value=False)
        self.var_auto_restart = tk.BooleanVar(value=True)
        self.var_max_restart = tk.IntVar(value=RESTART_MAX_DEFAULT)

        # ---- Quota Pro Max (TODO 3bis) ----
        self.quota_state: QuotaState = load_state()
        self.last_quota: Quota | None = None
        self.quota_error_msg: str = ""
        self._session_throttle_active = False  # volatile, re-évalué à chaque refresh
        self.var_session_threshold = tk.IntVar(value=self.quota_state.session_threshold_pct)
        self.var_weekly_threshold = tk.IntVar(value=self.quota_state.weekly_threshold_pct)
        self.var_force_session = tk.BooleanVar(value=False)  # bypass 1-shot du throttle session

        self._build_ui()
        # Persiste la préférence moteur (partagée avec arsenal_pipeline).
        # Trace ajoutée APRÈS _build_ui pour ne pas firer pendant l'init de la
        # StringVar (le set() interne du constructor sauverait inutilement la
        # valeur déjà lue depuis le disque).
        self.var_engine.trace_add("write", self._on_engine_changed)
        self._set_state(State.IDLE)
        self._refresh_quota_ui()  # initial render avec ce qu'on a en cache (rien)
        self.root.after(DRAIN_INTERVAL_MS, self._drain_queue)
        self.root.after(500, self._refresh_quota_and_check)  # 1er fetch ~immédiat
        self.root.after(1000, self._refresh_drops)  # 1er rendu drops
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_engine_changed(self, *_args):
        try:
            engine = self.var_engine.get()
            save_engine_pref(engine)
            label = "Claude Code CLI (subscription)" if engine == "claude_code" else "API Anthropic (payant)"
            self._console_append(f"══ Moteur enregistré : {label}\n", tag="meta")
        except Exception as e:
            self._console_append(f"══ Erreur enregistrement moteur : {e}\n", tag="meta")

    # ------------------------------------------------------------ UI build

    def _build_scrollable_root(self) -> ttk.Frame:
        """Wrap toute la UI dans un Canvas vertical scrollable. Retourne le
        Frame interne où packer le contenu. Le Canvas étire le contenu à sa
        largeur (pas de scroll horizontal) et à sa hauteur quand la fenêtre
        est plus grande que le contenu naturel — ça préserve l'`expand=True`
        de la console quand l'utilisateur agrandit la fenêtre. Quand la
        fenêtre est plus petite que le contenu, la scrollbar verticale prend
        le relais."""
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        vbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        content = ttk.Frame(canvas)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _on_inner_resize(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_resize(e):
            # Largeur : toujours synchronisée (pas de scroll horizontal).
            canvas.itemconfigure(content_id, width=e.width)
            # Hauteur : le frame interne s'étire jusqu'à la hauteur du canvas
            # quand celui-ci est plus grand que le contenu naturel, sinon il
            # garde sa hauteur naturelle (et la scrollbar fait son boulot).
            natural_h = content.winfo_reqheight()
            canvas.itemconfigure(content_id, height=max(e.height, natural_h))

        content.bind("<Configure>", _on_inner_resize)
        canvas.bind("<Configure>", _on_canvas_resize)

        def _on_mousewheel(e):
            # Laisser les widgets scrollables gérer leur propre roulette.
            try:
                if e.widget.winfo_class() in ("Text", "Treeview", "Listbox", "TCombobox"):
                    return
            except Exception:
                pass
            bbox = canvas.bbox("all")
            if bbox and (bbox[3] - bbox[1]) > canvas.winfo_height():
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        return content

    def _build_ui(self):
        self._content = self._build_scrollable_root()
        opts = ttk.LabelFrame(self._content, text="Options", padding=10)
        opts.pack(fill="x", padx=10, pady=(10, 4))

        # --- Moteur ---
        engine = ttk.LabelFrame(opts, text="Moteur", padding=6)
        engine.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
        ttk.Radiobutton(engine, text="Claude Code CLI (gratuit, subscription)  [défaut]",
                        variable=self.var_engine, value="claude_code").pack(anchor="w")
        ttk.Radiobutton(engine, text="API Anthropic (payant, nécessite ANTHROPIC_API_KEY)",
                        variable=self.var_engine, value="api").pack(anchor="w")

        # --- Filtres ---
        filt = ttk.LabelFrame(opts, text="Filtres", padding=6)
        filt.grid(row=0, column=1, sticky="nsew", pady=4)
        ttk.Checkbutton(filt, text="--text-only (skip carrousels image)",
                        variable=self.var_text_only).pack(anchor="w")
        ttk.Checkbutton(filt, text="--re-summarize (re-traite les SUCCESS — ⚠ coûteux)",
                        variable=self.var_re_summarize).pack(anchor="w")
        ttk.Checkbutton(filt, text="--no-wait-carousel-transcripts",
                        variable=self.var_no_wait).pack(anchor="w")
        row_id = ttk.Frame(filt)
        row_id.pack(fill="x", anchor="w", pady=(4, 0))
        ttk.Label(row_id, text="--id (1 contenu):").pack(side="left")
        ttk.Entry(row_id, textvariable=self.var_target_id, width=22).pack(side="left", padx=(6, 0))

        # --- Robustesse ---
        robust = ttk.LabelFrame(opts, text="Robustesse", padding=6)
        robust.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Checkbutton(robust, text="Auto-restart (crash + reprise auto au reset session)",
                        variable=self.var_auto_restart).pack(side="left")
        ttk.Label(robust, text="    Max restarts:").pack(side="left")
        ttk.Spinbox(robust, from_=0, to=100, width=4,
                    textvariable=self.var_max_restart).pack(side="left", padx=(4, 0))

        opts.columnconfigure(0, weight=1)
        opts.columnconfigure(1, weight=1)

        # --- Quota Pro Max (TODO 3bis) ---
        quota = ttk.LabelFrame(self._content, text="📊 Quota Pro Max", padding=(8, 4))
        quota.pack(fill="x", padx=10, pady=(4, 4))

        # Cookie row
        cookie_row = ttk.Frame(quota)
        cookie_row.pack(fill="x", pady=(0, 4))
        ttk.Label(cookie_row, text="Cookie :").pack(side="left")
        self.lbl_cookie = ttk.Label(cookie_row, text="—", font=("Segoe UI", 9, "bold"))
        self.lbl_cookie.pack(side="left", padx=(4, 8))
        ttk.Button(cookie_row, text="🔐 Configurer", width=14,
                   command=self._open_cookie_dialog).pack(side="left", padx=2)
        ttk.Button(cookie_row, text="🗑 Effacer", width=10,
                   command=self._delete_cookie).pack(side="left", padx=2)
        ttk.Button(cookie_row, text="🔄 Refresh", width=10,
                   command=lambda: self._fetch_quota_async()).pack(side="left", padx=2)

        ttk.Separator(quota, orient="horizontal").pack(fill="x", pady=2)

        # Quota bars
        bars = ttk.Frame(quota)
        bars.pack(fill="x", pady=2)
        bars.columnconfigure(1, weight=1)

        def _make_bar(row, label):
            ttk.Label(bars, text=label, width=14).grid(row=row, column=0, sticky="w")
            pb = ttk.Progressbar(bars, mode="determinate", maximum=100)
            pb.grid(row=row, column=1, sticky="ew", padx=4)
            lbl = ttk.Label(bars, text="—", width=24, font=("Consolas", 9))
            lbl.grid(row=row, column=2, sticky="w")
            return pb, lbl

        self.pb_session, self.lbl_session = _make_bar(0, "Session 5h")
        self.pb_weekly, self.lbl_weekly = _make_bar(1, "Hebdo 7j")
        self.pb_sonnet, self.lbl_sonnet = _make_bar(2, "Hebdo Sonnet")
        self.pb_overage, self.lbl_overage = _make_bar(3, "Overage")

        ttk.Separator(quota, orient="horizontal").pack(fill="x", pady=2)

        # Thresholds + state
        thr = ttk.Frame(quota)
        thr.pack(fill="x", pady=(2, 0))
        ttk.Label(thr, text="Seuil session :").pack(side="left")
        ttk.Spinbox(thr, from_=10, to=100, width=4,
                    textvariable=self.var_session_threshold,
                    command=self._save_thresholds).pack(side="left", padx=(2, 8))
        ttk.Label(thr, text="%   Seuil hebdo :").pack(side="left")
        ttk.Spinbox(thr, from_=10, to=100, width=4,
                    textvariable=self.var_weekly_threshold,
                    command=self._save_thresholds).pack(side="left", padx=(2, 8))
        ttk.Label(thr, text="%").pack(side="left")
        self.lbl_quota_state = ttk.Label(thr, text="État : —",
                                          font=("Segoe UI", 9, "bold"))
        self.lbl_quota_state.pack(side="right")

        # Throttle action buttons (visibility toggled in _refresh_quota_ui)
        self.frm_throttle_actions = ttk.Frame(quota)
        self.frm_throttle_actions.pack(fill="x", pady=(2, 0))
        self.btn_force_session = ttk.Button(self.frm_throttle_actions,
            text="⏯ Forcer continue session",
            command=self._force_continue_session)
        self.btn_reset_weekly = ttk.Button(self.frm_throttle_actions,
            text="🔄 Reset throttle hebdo",
            command=self._reset_weekly_throttle)

        # --- Drops récents (#liens) ---
        drops = ttk.LabelFrame(self._content, text="📥 Drops récents (#liens)", padding=(8, 4))
        drops.pack(fill="x", padx=10, pady=(4, 4))
        cols = ("plat", "dl", "sum", "sync", "ts")
        self.drops_tree = ttk.Treeview(drops, columns=cols,
                                        show="tree headings", height=6)
        self.drops_tree.heading("#0", text="ID source")
        self.drops_tree.heading("plat", text="Plate")
        self.drops_tree.heading("dl", text="DL")
        self.drops_tree.heading("sum", text="Sum")
        self.drops_tree.heading("sync", text="Sync")
        self.drops_tree.heading("ts", text="Quand")
        self.drops_tree.column("#0", width=170, anchor="w", stretch=True)
        self.drops_tree.column("plat", width=70, anchor="center")
        self.drops_tree.column("dl", width=40, anchor="center")
        self.drops_tree.column("sum", width=40, anchor="center")
        self.drops_tree.column("sync", width=40, anchor="center")
        self.drops_tree.column("ts", width=120, anchor="center")
        self.drops_tree.pack(fill="x")

        # --- Contrôles ---
        ctrl = ttk.Frame(self._content, padding=(10, 4))
        ctrl.pack(fill="x")
        self.btn_start = ttk.Button(ctrl, text="▶ Lancer", command=self.start)
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_pause = ttk.Button(ctrl, text="⏸ Pause", command=self.toggle_pause)
        self.btn_pause.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(ctrl, text="⏹ Stop", command=self.stop)
        self.btn_stop.pack(side="left", padx=6)

        self.lbl_state = ttk.Label(ctrl, text="État : IDLE", font=("Segoe UI", 9, "bold"))
        self.lbl_state.pack(side="right")

        # --- Progression ---
        prog = ttk.Frame(self._content, padding=(10, 0))
        prog.pack(fill="x", pady=(4, 4))
        self.progress = ttk.Progressbar(prog, mode="determinate")
        self.progress.pack(fill="x")
        self.lbl_progress = ttk.Label(prog, text="0 / 0  (—)   ETA: —   OK: 0   KO: 0",
                                       font=("Consolas", 9))
        self.lbl_progress.pack(anchor="w", pady=(2, 0))

        # --- Console live ---
        cons = ttk.LabelFrame(self._content, text="Console (live)", padding=4)
        cons.pack(fill="both", expand=True, padx=10, pady=4)
        self.console = scrolledtext.ScrolledText(cons, wrap="none", font=("Consolas", 9),
                                                  height=14, bg="#1e1e1e", fg="#dcdcdc",
                                                  insertbackground="#dcdcdc")
        self.console.pack(fill="both", expand=True)
        self.console.configure(state="disabled")

        # --- Raccourcis ---
        bottom = ttk.Frame(self._content, padding=(10, 4))
        bottom.pack(fill="x", pady=(0, 8))
        ttk.Button(bottom, text="📂 Ouvrir 03_ai_summaries",
                   command=lambda: os.startfile(str(SUMMARY_DIR))).pack(side="left", padx=(0, 4))
        ttk.Button(bottom, text="📊 Lancer audit",
                   command=self._run_audit).pack(side="left", padx=4)
        ttk.Button(bottom, text="🌐 #logs Discord",
                   command=lambda: webbrowser.open(DISCORD_LOGS_URL)).pack(side="left", padx=4)
        ttk.Button(bottom, text="🩺 Status Anthropic",
                   command=lambda: webbrowser.open(ANTHROPIC_STATUS_URL)).pack(side="left", padx=4)
        ttk.Button(bottom, text="💳 Console billing",
                   command=lambda: webbrowser.open(ANTHROPIC_BILLING_URL)).pack(side="left", padx=4)
        ttk.Button(bottom, text="📈 Claude.ai usage",
                   command=lambda: webbrowser.open(CLAUDE_AI_USAGE_URL)).pack(side="left", padx=4)

    # ---------------------------------------------------------- State control

    def _set_state(self, st: str):
        self.state = st
        labels = {
            State.IDLE: ("État : IDLE", "#444"),
            State.RUNNING: ("État : RUNNING", "#1a8b1a"),
            State.PAUSED: ("État : PAUSED", "#b58900"),
            State.STOPPING: ("État : STOPPING…", "#cc3333"),
            State.CRASHED_WAITING: ("État : CRASHED — auto-restart…", "#cc3333"),
        }
        text, color = labels.get(st, (f"État : {st}", "#444"))
        self.lbl_state.configure(text=text, foreground=color)

        running = st in (State.RUNNING, State.PAUSED)
        # Phase Y.16 : le throttle hebdo ne s'applique qu'au CLI subscription
        # (claude_code), pas à l'API Anthropic facturée séparément.
        engine_is_api = self.var_engine.get() == "api"
        weekly_throttled = (getattr(self, "quota_state", None)
                            and self.quota_state.weekly_throttled
                            and not engine_is_api)
        self.btn_start.configure(state="disabled" if (running or weekly_throttled) else "normal")
        self.btn_pause.configure(state="normal" if running else "disabled",
                                  text="▶ Reprendre" if st == State.PAUSED else "⏸ Pause")
        self.btn_stop.configure(state="normal" if running or st == State.CRASHED_WAITING else "disabled")

    # ---------------------------------------------------------- Args build

    def _build_args(self) -> list[str]:
        args = [PYTHON_EXE, "-u", str(SUMMARIZE_PY)]
        if self.var_engine.get() == "claude_code":
            args.append("--use-claude-code")
        if self.var_text_only.get():
            args.append("--text-only")
        if self.var_re_summarize.get():
            args.append("--re-summarize")
        if self.var_no_wait.get():
            args.append("--no-wait-carousel-transcripts")
        target_id = self.var_target_id.get().strip()
        if target_id:
            args.extend(["--id", target_id])
        return args

    # ---------------------------------------------------------- Start / stop

    def start(self):
        if self.state in (State.RUNNING, State.PAUSED):
            return
        if self.var_re_summarize.get():
            ok = messagebox.askyesno(
                "Confirmer --re-summarize",
                "Le mode --re-summarize re-traite TOUS les SUCCESS existants.\n"
                "Tu vas re-payer (en quota subscription ou tokens API) les 19+ "
                "résumés Sonnet déjà faits.\n\nContinuer ?")
            if not ok:
                return
        self.user_requested_stop = False
        self.restart_count = 0
        self._spawn()

    def _can_spawn(self) -> tuple[bool, str]:
        """Pre-check : retourne (False, raison) si throttle quota actif. Pose
        aussi les flags throttle si on a un quota qui dépasse mais pas encore
        flaggué — ça évite la fenêtre de timing entre un Lancer manuel et le
        prochain refresh quota.

        Phase Y.16 : si engine = api, bypass tous les checks quota Pro Max
        (l'API est facturée séparément, pas concernée par le hebdo claude.ai).
        """
        if self.var_engine.get() == "api":
            return True, ""
        if self.quota_state.weekly_throttled:
            return False, f"throttle hebdo actif depuis {self.quota_state.weekly_throttled_at or '?'}"
        if self.last_quota is not None:
            q = self.last_quota
            if q.weekly_pct >= self.quota_state.weekly_threshold_pct:
                self.quota_state.weekly_throttled = True
                self.quota_state.weekly_throttled_at = q.fetched_at.isoformat()
                save_state(self.quota_state)
                self._refresh_quota_ui()
                return False, (f"hebdo {q.weekly_pct:.1f}% ≥ seuil "
                               f"{self.quota_state.weekly_threshold_pct}%")
            if (q.session_pct >= self.quota_state.session_threshold_pct
                    and not self.var_force_session.get()):
                self._session_throttle_active = True
                self._refresh_quota_ui()
                return False, (f"session {q.session_pct:.1f}% ≥ seuil "
                               f"{self.quota_state.session_threshold_pct}% "
                               f"(« Forcer continue session » pour bypass)")
        if self._session_throttle_active and not self.var_force_session.get():
            return False, "throttle session actif (en attente reset ou baisse quota)"
        return True, ""

    def _spawn(self):
        can, reason = self._can_spawn()
        if not can:
            self._console_append(f"══ Spawn refusé : {reason}\n", tag="meta")
            self._set_state(State.IDLE)
            return
        args = self._build_args()
        self._console_append(f"\n══ Lancement : {' '.join(args)}\n", tag="meta")
        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            self.proc = subprocess.Popen(
                args,
                cwd=str(HERE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except Exception as e:
            messagebox.showerror("Erreur de lancement", f"Impossible de lancer summarize.py :\n{e}")
            return

        self.start_time = datetime.now().timestamp()
        self.done = self.success_count = self.fail_count = 0
        self.total = 0
        self.total_cost_usd = 0.0
        self._update_progress()
        self._set_state(State.RUNNING)

        self.reader_thread = threading.Thread(target=self._reader_loop,
                                              args=(self.proc,), daemon=True)
        self.reader_thread.start()

    def _reader_loop(self, proc: subprocess.Popen):
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                self.line_queue.put(line.rstrip("\n"))
        except Exception:
            pass
        finally:
            rc = proc.wait()
            self.line_queue.put(f"__EXIT__:{rc}")

    def toggle_pause(self):
        if self.proc is None or self.state not in (State.RUNNING, State.PAUSED):
            return
        try:
            p = psutil.Process(self.proc.pid)
            if self.state == State.RUNNING:
                for child in p.children(recursive=True):
                    try: child.suspend()
                    except psutil.NoSuchProcess: pass
                p.suspend()
                self._set_state(State.PAUSED)
                self._console_append("══ Pause demandée (process suspendu)\n", tag="meta")
            else:
                p.resume()
                for child in p.children(recursive=True):
                    try: child.resume()
                    except psutil.NoSuchProcess: pass
                self._set_state(State.RUNNING)
                self._console_append("══ Reprise\n", tag="meta")
        except psutil.NoSuchProcess:
            self._console_append("══ Process introuvable (déjà mort ?)\n", tag="meta")

    def stop(self):
        self.user_requested_stop = True
        if self.restart_after_id:
            self.root.after_cancel(self.restart_after_id)
            self.restart_after_id = None
        if self.state == State.CRASHED_WAITING:
            self._set_state(State.IDLE)
            self._console_append("══ Auto-restart annulé\n", tag="meta")
            return
        if self.proc is None:
            self._set_state(State.IDLE)
            return
        if self.state == State.PAUSED:
            try:
                psutil.Process(self.proc.pid).resume()
            except psutil.NoSuchProcess:
                pass
        self._set_state(State.STOPPING)
        self._console_append("══ Stop demandé (Ctrl-C envoyé, embed Discord 'Interrompu' attendu)\n", tag="meta")
        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        self.root.after(10000, self._force_kill_if_alive)

    def _force_kill_if_alive(self):
        if self.proc is not None and self.proc.poll() is None:
            self._console_append("══ Stop soft ignoré, hard kill\n", tag="meta")
            try:
                psutil.Process(self.proc.pid).kill()
            except psutil.NoSuchProcess:
                pass

    # ---------------------------------------------------------- Drain & parse

    def _drain_queue(self):
        try:
            while True:
                line = self.line_queue.get_nowait()
                if line.startswith("__EXIT__:"):
                    rc = int(line.split(":", 1)[1])
                    self._on_proc_exit(rc)
                    continue
                self._console_append(line + "\n")
                self._parse_line(line)
        except queue.Empty:
            pass
        self.root.after(DRAIN_INTERVAL_MS, self._drain_queue)

    def _parse_line(self, line: str):
        m = TOTAL_RE.search(line)
        if m:
            self.total = int(m.group(1))
            self.progress.configure(maximum=max(self.total, 1))
            self._update_progress()
            return
        m = PROGRESS_RE.search(line)
        if m:
            self.done = int(m.group(1))
            if self.total == 0:
                self.total = int(m.group(2))
                self.progress.configure(maximum=max(self.total, 1))
            self.progress.configure(value=self.done)
            self._update_progress()
            return
        if OK_RE.search(line):
            self.success_count += 1
            self._update_progress()
        elif FAIL_RE.search(line):
            self.fail_count += 1
            self._update_progress()
        m = COST_RE.search(line)
        if m:
            try:
                self.total_cost_usd = float(m.group(1))
                self._update_progress()
            except ValueError:
                pass

    def _update_progress(self):
        pct = int(round(100 * self.done / max(self.total, 1))) if self.total else 0
        eta = "—"
        if self.start_time and self.done > 0 and self.total > 0:
            elapsed = datetime.now().timestamp() - self.start_time
            avg = elapsed / self.done
            remaining_sec = avg * max(self.total - self.done, 0)
            eta = self._fmt_eta(remaining_sec)
        if self.var_engine.get() == "claude_code":
            cost_label = "0 € (subscription)"
        else:
            cost_label = f"${self.total_cost_usd:.4f} (~{self.total_cost_usd * USD_TO_EUR:.4f}€)"
        self.lbl_progress.configure(
            text=f"{self.done} / {self.total}  ({pct}%)   ETA: {eta}   "
                 f"OK: {self.success_count}   KO: {self.fail_count}   Coût: {cost_label}"
        )

    @staticmethod
    def _fmt_eta(sec: float) -> str:
        if sec <= 0:
            return "—"
        sec = int(sec)
        if sec < 60: return f"{sec}s"
        m, s = divmod(sec, 60)
        if m < 60: return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"

    # ---------------------------------------------------------- Process exit

    def _on_proc_exit(self, rc: int):
        self.proc = None
        self._console_append(f"══ Process terminé (exit code {rc})\n", tag="meta")

        # Throttle quota actif : on ne relance pas, _check_thresholds le fera au reset.
        if self.quota_state.weekly_throttled or self._session_throttle_active:
            self._set_state(State.IDLE)
            return

        if self.user_requested_stop or rc == 0:
            self._set_state(State.IDLE)
            return

        # Lock actif (autre instance summarize.py vivante) : pas d'auto-restart
        # car ça boucle sur la même erreur jusqu'à manual kill du PID propriétaire.
        try:
            console_tail = self.console.get("end-300c", "end")
            if "Lock actif" in console_tail and "encore vivant" in console_tail:
                self._console_append(
                    "══ Auto-restart annulé (lock détenu par une autre instance) — "
                    "tuer le PID propriétaire ou cliquer ▶ Lancer manuellement\n",
                    tag="meta")
                self._set_state(State.IDLE)
                return
        except Exception:
            pass

        if not self.var_auto_restart.get():
            self._set_state(State.IDLE)
            return

        if self.restart_count >= self.var_max_restart.get():
            self._console_append(f"══ Plafond {self.var_max_restart.get()} restarts atteint, abandon\n",
                                  tag="meta")
            self._set_state(State.IDLE)
            return

        self.restart_count += 1
        self._console_append(f"══ Auto-restart {self.restart_count}/{self.var_max_restart.get()} "
                              f"dans {RESTART_DELAY_SECONDS}s…\n", tag="meta")
        self._set_state(State.CRASHED_WAITING)
        self.restart_after_id = self.root.after(RESTART_DELAY_SECONDS * 1000, self._auto_restart_fire)

    def _auto_restart_fire(self):
        self.restart_after_id = None
        if self.user_requested_stop:
            self._set_state(State.IDLE)
            return
        if self.quota_state.weekly_throttled or self._session_throttle_active:
            self._console_append("══ Auto-restart annulé (throttle quota actif)\n", tag="meta")
            self._set_state(State.IDLE)
            return
        self._spawn()

    # ---------------------------------------------------------- Console

    def _console_append(self, text: str, tag: str | None = None):
        self.console.configure(state="normal")
        self.console.insert("end", text)
        # Trim si trop long
        line_count = int(self.console.index("end-1c").split(".")[0])
        if line_count > CONSOLE_MAX_LINES:
            self.console.delete("1.0", f"{line_count - CONSOLE_MAX_LINES}.0")
        self.console.see("end")
        self.console.configure(state="disabled")

    # ---------------------------------------------------------- Audit

    def _run_audit(self):
        win = tk.Toplevel(self.root)
        win.title("Audit Arsenal")
        win.geometry("760x520")
        txt = scrolledtext.ScrolledText(win, font=("Consolas", 9),
                                         bg="#1e1e1e", fg="#dcdcdc")
        txt.pack(fill="both", expand=True)
        txt.insert("end", "Lancement de arsenal_audit.py…\n\n")
        txt.configure(state="disabled")

        def worker():
            try:
                proc = subprocess.run(
                    [PYTHON_EXE, "-u", str(AUDIT_PY)],
                    cwd=str(HERE), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=300,
                )
                output = proc.stdout + ("\n--- STDERR ---\n" + proc.stderr if proc.stderr else "")
            except Exception as e:
                output = f"ERREUR : {e}"
            self.root.after(0, lambda: self._fill_audit(txt, output))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _fill_audit(txt_widget, output: str):
        txt_widget.configure(state="normal")
        txt_widget.insert("end", output)
        txt_widget.see("end")
        txt_widget.configure(state="disabled")

    # ---------------------------------------------------------- Quota Pro Max

    def _refresh_quota_and_check(self):
        """Boucle 60s — fetch quota async puis re-schedule."""
        self._fetch_quota_async()
        self.root.after(QUOTA_REFRESH_MS, self._refresh_quota_and_check)

    def _fetch_quota_async(self):
        """Fetch dans un thread daemon pour ne pas freeze la UI."""
        if not has_cookie():
            self.last_quota = None
            self.quota_error_msg = "Cookie non configuré"
            self._refresh_quota_ui()
            return

        def worker():
            try:
                q = fetch_usage()
                self.root.after(0, lambda: self._on_quota_received(q))
            except UsageError as e:
                err_msg = str(e)
                err_type = type(e).__name__
                self.root.after(0, lambda: self._on_quota_error(err_type, err_msg))
        threading.Thread(target=worker, daemon=True).start()

    def _on_quota_received(self, q: Quota):
        self.last_quota = q
        self.quota_error_msg = ""
        self.quota_state.last_check_at = q.fetched_at.isoformat()
        save_state(self.quota_state)
        self._check_thresholds(q)
        self._refresh_quota_ui()

    def _on_quota_error(self, err_type: str, err_msg: str):
        self.quota_error_msg = f"{err_type}: {err_msg[:120]}"
        self._refresh_quota_ui()
        if err_type == "EndpointChangedError":
            self._post_discord_log(
                "⚠ Quota Watcher — endpoint claude.ai changé",
                f"`{err_msg[:300]}`\n\nVérifier `claude_usage.py` et adapter le parser.",
                DISCORD_COLOR_ORANGE,
            )

    def _refresh_quota_ui(self):
        # Cookie label — indicateur visuel cookie expiré (orange/rouge selon le type d'erreur)
        if has_cookie():
            err = (self.quota_error_msg or "").lower()
            if any(x in err for x in ("cookieexpired", "401", "403", "expir", "rejet")):
                self.lbl_cookie.configure(text="⚠ Cookie expiré (re-configurer)",
                                          foreground="#cc3333")
            elif any(x in err for x in ("network", "timeout", "connection")):
                self.lbl_cookie.configure(text="🌐 Cookie OK, réseau down", foreground="#b58900")
            else:
                self.lbl_cookie.configure(text="✅ Configuré", foreground="#1a8b1a")
        else:
            self.lbl_cookie.configure(text="❌ Non configuré", foreground="#cc3333")

        q = self.last_quota
        if q is None:
            for pb, lbl in [(self.pb_session, self.lbl_session),
                             (self.pb_weekly, self.lbl_weekly),
                             (self.pb_sonnet, self.lbl_sonnet),
                             (self.pb_overage, self.lbl_overage)]:
                pb.configure(value=0)
                lbl.configure(text="—")
            txt = self.quota_error_msg or "En attente du 1er fetch…"
            self.lbl_quota_state.configure(text=f"État : {txt}", foreground="#888")
        else:
            self._set_bar(self.pb_session, self.lbl_session, q.session_pct,
                          q.session_seconds_until_reset(),
                          self.quota_state.session_threshold_pct)
            self._set_bar(self.pb_weekly, self.lbl_weekly, q.weekly_pct,
                          q.weekly_seconds_until_reset(),
                          self.quota_state.weekly_threshold_pct)
            if q.weekly_sonnet_pct is not None:
                from claude_usage import _seconds_until
                sonnet_sec = _seconds_until(q.weekly_sonnet_resets_at)
                self._set_bar(self.pb_sonnet, self.lbl_sonnet, q.weekly_sonnet_pct,
                              sonnet_sec, threshold_pct=None)
            else:
                self.pb_sonnet.configure(value=0)
                self.lbl_sonnet.configure(text="—")
            if q.extra_pct is not None:
                self.pb_overage.configure(value=min(q.extra_pct, 100))
                self.lbl_overage.configure(
                    text=f"{q.extra_pct:5.1f}%  ({q.extra_used_credits}/{q.extra_limit_credits} €)")
            else:
                self.pb_overage.configure(value=0)
                self.lbl_overage.configure(text="désactivé")

            # État global
            if self.quota_state.weekly_throttled:
                self.lbl_quota_state.configure(text="État : 🚨 Hebdo bloqué", foreground="#cc3333")
            elif self._session_throttle_active:
                self.lbl_quota_state.configure(text="État : ⚠ Session pause", foreground="#b58900")
            else:
                self.lbl_quota_state.configure(text="État : ✅ OK", foreground="#1a8b1a")

        # Boutons throttle
        for w in self.frm_throttle_actions.winfo_children():
            w.pack_forget()
        if self._session_throttle_active:
            self.btn_force_session.pack(side="left", padx=2)
        if self.quota_state.weekly_throttled:
            self.btn_reset_weekly.pack(side="left", padx=2)

        # ▶ Lancer désactivé si throttle hebdo
        if self.quota_state.weekly_throttled and self.state == State.IDLE:
            self.btn_start.configure(state="disabled")
        elif self.state == State.IDLE:
            self.btn_start.configure(state="normal")

    @staticmethod
    def _set_bar(pb: ttk.Progressbar, lbl: ttk.Label, pct: float,
                 seconds_until_reset, threshold_pct):
        pb.configure(value=min(pct, 100))
        reset_str = fmt_duration(seconds_until_reset) if seconds_until_reset is not None else "—"
        lbl.configure(text=f"{pct:5.1f}%  reset {reset_str}")

    def _check_thresholds(self, q: Quota):
        state = self.quota_state

        # ---- Hebdo (persistant) ----
        if q.weekly_pct >= state.weekly_threshold_pct:
            if not state.weekly_throttled:
                state.weekly_throttled = True
                state.weekly_throttled_at = q.fetched_at.isoformat()
                save_state(state)
                self._auto_stop_quota("hebdo")
                self._post_discord_log(
                    "🚨 Limite hebdo Arsenal atteinte",
                    (f"Quota hebdo : **{q.weekly_pct:.1f} %** (seuil : "
                     f"{state.weekly_threshold_pct} %).\n"
                     f"Reset auto dans {fmt_duration(q.weekly_seconds_until_reset())}.\n"
                     f"Pour reprendre maintenant : augmenter le seuil ou cliquer "
                     f"« Reset throttle hebdo » dans la GUI."),
                    DISCORD_COLOR_RED,
                )
        else:
            if state.weekly_throttled:
                state.weekly_throttled = False
                state.weekly_throttled_at = None
                save_state(state)
                self._post_discord_log(
                    "✅ Throttle hebdo levé automatiquement",
                    f"Quota hebdo redescendu à **{q.weekly_pct:.1f} %** "
                    f"(sous seuil {state.weekly_threshold_pct} %).",
                    DISCORD_COLOR_GREEN,
                )

        # ---- Session (volatile) ----
        if q.session_pct >= state.session_threshold_pct:
            if not self._session_throttle_active and not self.var_force_session.get():
                self._session_throttle_active = True
                # Annule un auto-restart programmé (pas la peine d'attendre 10s)
                if self.restart_after_id:
                    self.root.after_cancel(self.restart_after_id)
                    self.restart_after_id = None
                # Stop le batch s'il tourne ; sinon juste flag
                if self.state == State.RUNNING:
                    self._auto_stop_quota("session")
                elif self.state == State.CRASHED_WAITING:
                    self._set_state(State.IDLE)
                self._post_discord_log(
                    "⚠ Pause session — quota 5h atteint",
                    (f"Quota session : **{q.session_pct:.1f} %** (seuil : "
                     f"{state.session_threshold_pct} %).\n"
                     f"Reprise auto au reset (dans "
                     f"{fmt_duration(q.session_seconds_until_reset())}) "
                     f"ou dès que le quota redescend."),
                    DISCORD_COLOR_ORANGE,
                )
        else:
            if self._session_throttle_active:
                self._session_throttle_active = False
                self.var_force_session.set(False)
                if (self.var_auto_restart.get() and self.state == State.IDLE
                        and not self.user_requested_stop and self.proc is None):
                    self._post_discord_log(
                        "✅ Reprise session auto",
                        f"Quota session redescendu à **{q.session_pct:.1f} %**. "
                        f"Relance du batch summarize.",
                        DISCORD_COLOR_GREEN,
                    )
                    self._spawn()

    def _auto_stop_quota(self, reason: str):
        """Stop le batch sans set user_requested_stop, pour autoriser l'auto-resume."""
        if self.proc is None or self.state not in (State.RUNNING, State.PAUSED):
            return
        if self.state == State.PAUSED:
            try:
                psutil.Process(self.proc.pid).resume()
            except psutil.NoSuchProcess:
                pass
        self._set_state(State.STOPPING)
        self._console_append(f"══ Auto-stop : quota {reason} dépassé\n", tag="meta")
        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        self.root.after(10000, self._force_kill_if_alive)

    def _save_thresholds(self):
        try:
            self.quota_state.session_threshold_pct = int(self.var_session_threshold.get())
            self.quota_state.weekly_threshold_pct = int(self.var_weekly_threshold.get())
            save_state(self.quota_state)
            if self.last_quota is not None:
                self._check_thresholds(self.last_quota)
                self._refresh_quota_ui()
        except (ValueError, TypeError):
            pass

    # ---------- Cookie management ----------

    def _open_cookie_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Configurer cookie claude.ai")
        win.geometry("560x220")
        win.transient(self.root)
        win.grab_set()
        ttk.Label(
            win,
            text=("Coller le header `cookie:` complet récupéré via DevTools :\n"
                  "F12 → Network → Fetch/XHR → recharger → clic sur `usage` →\n"
                  "Headers → Request Headers → copier la valeur de `cookie:`.\n\n"
                  "Le cookie est chiffré localement via Windows DPAPI."),
            wraplength=520, justify="left",
        ).pack(padx=10, pady=8)
        var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=var, show="*", width=60)
        entry.pack(padx=10, pady=4, fill="x")
        entry.focus_set()
        btns = ttk.Frame(win)
        btns.pack(pady=8)
        ttk.Button(btns, text="Sauvegarder",
                   command=lambda: self._save_cookie_from_dialog(var.get(), win)
                   ).pack(side="left", padx=5)
        ttk.Button(btns, text="Annuler",
                   command=win.destroy).pack(side="left", padx=5)
        win.bind("<Return>",
                 lambda _e: self._save_cookie_from_dialog(var.get(), win))

    def _save_cookie_from_dialog(self, value: str, win: tk.Toplevel):
        if not value or not value.strip():
            messagebox.showwarning("Cookie vide",
                                    "Coller le cookie avant de sauvegarder.",
                                    parent=win)
            return
        try:
            save_cookie(value.strip())
        except Exception as e:
            messagebox.showerror("Erreur DPAPI", f"Impossible de sauvegarder :\n{e}",
                                  parent=win)
            return
        win.destroy()
        self._refresh_quota_ui()
        self._fetch_quota_async()

    def _delete_cookie(self):
        if not has_cookie():
            messagebox.showinfo("Cookie", "Aucun cookie à effacer.")
            return
        if not messagebox.askyesno(
                "Effacer cookie",
                "Effacer le cookie claude.ai chiffré ?\n"
                "Le quota ne sera plus rafraîchi tant qu'un cookie n'est pas reconfiguré."):
            return
        delete_cookie()
        self.last_quota = None
        self.quota_error_msg = "Cookie effacé"
        self._refresh_quota_ui()

    # ---------- Throttle override ----------

    def _force_continue_session(self):
        if not messagebox.askyesno(
                "Forcer continue (session)",
                "Bypass du throttle session ?\n"
                "Le batch va relancer même si le quota 5h dépasse le seuil.\n"
                "Le bypass dure jusqu'au prochain Stop manuel ou reset session."):
            return
        self.var_force_session.set(True)
        self._session_throttle_active = False
        self._refresh_quota_ui()
        if (self.var_auto_restart.get() and self.state == State.IDLE
                and not self.user_requested_stop and self.proc is None):
            self._spawn()

    def _reset_weekly_throttle(self):
        if not messagebox.askyesno(
                "Reset throttle hebdo",
                "Lever manuellement le throttle hebdo ?\n"
                "Le batch pourra relancer même si le quota dépasse le seuil hebdo.\n"
                "À utiliser si tu veux dépasser ton propre garde-fou."):
            return
        self.quota_state.weekly_throttled = False
        self.quota_state.weekly_throttled_at = None
        save_state(self.quota_state)
        self._refresh_quota_ui()
        self._post_discord_log(
            "🔄 Throttle hebdo levé manuellement",
            "L'utilisateur a forcé le reset du throttle hebdo. "
            "Le batch peut être relancé.",
            DISCORD_COLOR_BLUE,
        )

    # ---------- Drops monitoring (lecture CSV) ----------

    def _refresh_drops(self):
        """Rafraîchit la liste des 10 derniers drops depuis le CSV. Re-schedule
        toutes les 30s. Lecture CSV directe (pas d'appel réseau, négligeable
        en CPU même avec 1.4 MB)."""
        self.root.after(30_000, self._refresh_drops)
        try:
            import csv
            csv_path = HERE / "suivi_global.csv"
            if not csv_path.is_file():
                return
            rows = []
            with open(csv_path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r.get("download_timestamp", "").strip():
                        rows.append(r)
            rows.sort(key=lambda r: r.get("download_timestamp", ""), reverse=True)

            # Reset + repopule
            for item in self.drops_tree.get_children():
                self.drops_tree.delete(item)

            def emoji(value: str) -> str:
                v = (value or "").upper().strip()
                return {"SUCCESS": "✅", "FAILED": "❌", "PENDING": "⏳"}.get(v, "·")

            for r in rows[:10]:
                rid = (r.get("id") or "?")[:30]
                plat = (r.get("plateforme") or "")[:9]
                dl = emoji(r.get("download_status"))
                sm = emoji(r.get("summary_status"))
                sy = emoji(r.get("sync_status"))
                ts = r.get("download_timestamp", "")
                # Format MM-DD HH:MM (lisible et compact)
                ts_short = ts[5:16] if len(ts) >= 16 else ts
                self.drops_tree.insert("", "end", text=rid,
                                       values=(plat, dl, sm, sy, ts_short))
        except Exception:
            pass  # silent — la GUI ne doit pas crasher si CSV temporairement illisible

    # ---------- Discord post ----------

    def _post_discord_log(self, title: str, description: str, color: int):
        token = os.environ.get("DISCORD_BOT_TOKEN")
        if not token:
            return
        embed = {
            "title": title[:256],
            "description": description[:4000],
            "color": color,
            "timestamp": datetime.now().astimezone().isoformat(),
            "footer": {"text": "Arsenal Quota Watcher"},
        }
        payload = json.dumps({"embeds": [embed]}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_API_URL,
            data=payload,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "BotGSTAR-Arsenal-QuotaWatcher/1.0",
            },
            method="POST",
        )
        def worker():
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    r.read()
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    # ---------------------------------------------------------- Close

    def _on_close(self):
        if self.state in (State.RUNNING, State.PAUSED, State.CRASHED_WAITING):
            ok = messagebox.askyesno(
                "Quitter",
                "Un process summarize est encore actif.\n"
                "Quitter va envoyer Stop et fermer la fenêtre. OK ?")
            if not ok:
                return
            self.user_requested_stop = True
            self.stop()
        self.root.after(500, self.root.destroy)


def main():
    root = tk.Tk()
    try:
        from tkinter import font
        font.nametofont("TkDefaultFont").configure(family="Segoe UI", size=9)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
