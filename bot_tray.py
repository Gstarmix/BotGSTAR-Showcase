"""
BotGSTAR — Tray watchdog
========================

Remplace start_bot.bat en mode "icône system tray" :
- Spawne `python -u bot.py` en subprocess (sans console visible)
- Tee de stdout/stderr vers %TEMP%\\BotGSTAR_startup.log + buffer mémoire
- Auto-restart 10 s après crash (même comportement que le .bat)
- Icône tray colorée (vert=running, orange=pause, rouge=crash/wait)
- Menu clic droit : voir logs / pause / redémarrer / autostart / quitter
- Toast Windows à chaque crash + redémarrage

Lancement silencieux : start_tray.vbs (pythonw.exe sans console).
Le start_bot.bat historique reste utilisable pour debug en console.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from enum import Enum
from pathlib import Path
from tkinter import scrolledtext

from PIL import Image, ImageDraw, ImageFont
import pystray

# ============================================================
# CONFIG
# ============================================================

WORKSPACE = Path(__file__).resolve().parent
BOT_SCRIPT = WORKSPACE / "bot.py"
LOG_FILE = Path(os.environ.get("TEMP", str(Path.home()))) / "BotGSTAR_startup.log"
DATAS_DIR = WORKSPACE / "datas"

RESTART_DELAY_SECONDS = 10
TAIL_BUFFER_LINES = 4000
TOOLTIP_REFRESH_SECONDS = 30

STARTUP_DIR = (
    Path(os.environ.get("APPDATA", str(Path.home())))
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "Startup"
)
STARTUP_VBS = STARTUP_DIR / "BotGSTAR_Tray.vbs"
LOCAL_VBS = WORKSPACE / "start_tray.vbs"

PYTHON_EXE = sys.executable  # pythonw.exe quand lancé via start_tray.vbs
# Pour le subprocess bot, on force python.exe (pour que `-u` fonctionne
# bien et qu'on capture stdout proprement). Si on est en pythonw, on
# dérive le chemin de python.exe.
PYTHON_FOR_BOT = PYTHON_EXE.replace("pythonw.exe", "python.exe")

# CREATE_NO_WINDOW pour subprocess.Popen sur Windows
CREATE_NO_WINDOW = 0x08000000

# ============================================================
# STATE
# ============================================================


class BotState(Enum):
    RUNNING = "running"
    PAUSED = "paused"
    CRASHED_WAITING = "crashed_waiting"
    RESTARTING = "restarting"


COLORS = {
    BotState.RUNNING: (39, 174, 96),          # vert
    BotState.PAUSED: (230, 126, 34),          # orange
    BotState.CRASHED_WAITING: (192, 57, 43),  # rouge
    BotState.RESTARTING: (52, 152, 219),      # bleu
}


class TrayApp:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: BotState = BotState.RUNNING
        self._proc: subprocess.Popen | None = None
        self._started_at: datetime | None = None
        self._crash_count = 0
        self._restart_at: datetime | None = None  # when CRASHED_WAITING
        self._tail = deque(maxlen=TAIL_BUFFER_LINES)
        self._log_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._app_started_at = datetime.now()

        # Tk log window (créé à la demande, sur son propre thread)
        self._log_window_thread: threading.Thread | None = None

        # pystray icon (créé dans main)
        self.icon: pystray.Icon | None = None

    # --------------------------------------------------------
    # SUBPROCESS / WATCHDOG
    # --------------------------------------------------------

    def _spawn_bot(self) -> None:
        """Lance le subprocess bot et le thread qui pompe son stdout."""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return  # déjà actif
            cmd = [PYTHON_FOR_BOT, "-u", str(BOT_SCRIPT)]
            self._append_log_line(
                f"[{_ts()}] Demarrage du bot (pid pending)..."
            )
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(WORKSPACE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._started_at = datetime.now()
            self._state = BotState.RUNNING
            self._restart_at = None
            self._append_log_line(
                f"[{_ts()}] Bot demarre (pid {self._proc.pid})"
            )
            t = threading.Thread(
                target=self._pump_stdout, args=(self._proc,), daemon=True
            )
            t.start()
        self._refresh_icon()

    def _pump_stdout(self, proc: subprocess.Popen) -> None:
        """Thread : lit stdout du subprocess → log file + tail buffer."""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                self._append_log_line(line)
        except Exception as e:
            self._append_log_line(f"[{_ts()}] [pump-error] {e!r}")

    def _append_log_line(self, line: str) -> None:
        """Append une ligne au log (fichier + buffer mémoire)."""
        with self._log_lock:
            self._tail.append(line)
            try:
                with LOG_FILE.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def _kill_bot(self) -> None:
        """Tue le subprocess proprement (taskkill /F /T)."""
        with self._lock:
            proc = self._proc
        if not proc:
            return
        if proc.poll() is not None:
            return  # déjà mort
        try:
            # /T = arbre de processus (au cas où le bot spawn des enfants)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    def _watchdog_loop(self) -> None:
        """
        Thread principal de surveillance.
        - Si RUNNING et le subprocess meurt → CRASHED_WAITING (10s) → respawn.
        - Si PAUSED → ne respawn pas.
        - Réagit au _stop_event pour quitter proprement.
        """
        while not self._stop_event.is_set():
            with self._lock:
                proc = self._proc
                state = self._state

            if state == BotState.PAUSED:
                time.sleep(0.5)
                continue

            if proc is None:
                time.sleep(0.5)
                continue

            rc = proc.poll()
            if rc is None:
                # Toujours vivant
                time.sleep(0.5)
                continue

            # Le subprocess est mort
            if state == BotState.RESTARTING:
                # Tué exprès, on respawn immédiatement
                self._append_log_line(
                    f"[{_ts()}] Bot tue (rc={rc}), redemarrage immediat..."
                )
                self._spawn_bot()
                self._toast(
                    "BotGSTAR redémarré",
                    "Le bot a été redémarré manuellement.",
                )
                continue

            if state == BotState.PAUSED:
                continue  # pas de respawn

            # Crash inattendu → countdown
            self._crash_count += 1
            with self._lock:
                self._state = BotState.CRASHED_WAITING
                self._restart_at = datetime.now()
            self._append_log_line(
                f"[{_ts()}] Bot arrete (rc={rc}). Restart dans {RESTART_DELAY_SECONDS}s "
                f"(crash #{self._crash_count})..."
            )
            self._refresh_icon()
            self._toast(
                "BotGSTAR a crashé",
                f"Code {rc}. Redémarrage automatique dans {RESTART_DELAY_SECONDS}s "
                f"(crash #{self._crash_count}).",
            )
            # Attente interruptible
            for _ in range(RESTART_DELAY_SECONDS * 10):
                if self._stop_event.is_set():
                    return
                with self._lock:
                    if self._state == BotState.PAUSED:
                        break
                time.sleep(0.1)
            with self._lock:
                if self._state != BotState.PAUSED and not self._stop_event.is_set():
                    pass  # respawn ci-dessous
                else:
                    continue
            self._spawn_bot()

    # --------------------------------------------------------
    # ACTIONS MENU
    # --------------------------------------------------------

    def action_pause_or_resume(self, icon: pystray.Icon, item) -> None:
        with self._lock:
            current = self._state
        if current == BotState.PAUSED:
            self._spawn_bot()
            self._toast("BotGSTAR repris", "Le bot a redémarré.")
        else:
            with self._lock:
                self._state = BotState.PAUSED
            self._kill_bot()
            self._refresh_icon()
            self._toast("BotGSTAR en pause", "Le bot est arrêté (pas d'auto-restart).")

    def action_restart(self, icon: pystray.Icon, item) -> None:
        with self._lock:
            self._state = BotState.RESTARTING
        self._kill_bot()
        # Le watchdog va voir RESTARTING et respawn

    def action_show_logs(self, icon: pystray.Icon, item) -> None:
        if self._log_window_thread and self._log_window_thread.is_alive():
            return  # déjà ouverte
        self._log_window_thread = threading.Thread(
            target=self._run_log_window, daemon=True
        )
        self._log_window_thread.start()

    def action_open_log_folder(self, icon: pystray.Icon, item) -> None:
        if LOG_FILE.exists():
            subprocess.Popen(
                ["explorer", "/select,", str(LOG_FILE)],
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                ["explorer", str(LOG_FILE.parent)],
                creationflags=CREATE_NO_WINDOW,
            )

    def action_open_data_folder(self, icon: pystray.Icon, item) -> None:
        subprocess.Popen(
            ["explorer", str(DATAS_DIR)],
            creationflags=CREATE_NO_WINDOW,
        )

    def action_toggle_startup(self, icon: pystray.Icon, item) -> None:
        if STARTUP_VBS.exists():
            try:
                STARTUP_VBS.unlink()
                self._toast(
                    "Démarrage auto désactivé",
                    "BotGSTAR ne se lancera plus avec Windows (mode tray).",
                )
            except OSError as e:
                self._toast("Erreur", f"Impossible de retirer : {e}")
        else:
            try:
                _write_startup_vbs()
                self._toast(
                    "Démarrage auto activé",
                    "BotGSTAR se lancera avec Windows (mode tray).",
                )
            except OSError as e:
                self._toast("Erreur", f"Impossible d'installer : {e}")
        if self.icon:
            self.icon.update_menu()

    def action_quit(self, icon: pystray.Icon, item) -> None:
        self._stop_event.set()
        with self._lock:
            self._state = BotState.PAUSED  # bloque tout respawn
        self._kill_bot()
        if self.icon:
            self.icon.stop()

    # --------------------------------------------------------
    # ÉTAT VISUEL
    # --------------------------------------------------------

    def _refresh_icon(self) -> None:
        if not self.icon:
            return
        with self._lock:
            state = self._state
        self.icon.icon = make_icon_image(COLORS[state])
        self.icon.title = self._build_tooltip()
        self.icon.update_menu()

    def _build_tooltip(self) -> str:
        with self._lock:
            state = self._state
            started = self._started_at
            restart_at = self._restart_at
            crashes = self._crash_count

        if state == BotState.RUNNING and started:
            uptime = _format_duration(datetime.now() - started)
            return f"BotGSTAR · en cours · uptime {uptime} · {crashes} crash"
        if state == BotState.PAUSED:
            return f"BotGSTAR · EN PAUSE · {crashes} crash depuis le boot"
        if state == BotState.CRASHED_WAITING and restart_at:
            elapsed = (datetime.now() - restart_at).total_seconds()
            remaining = max(0, RESTART_DELAY_SECONDS - int(elapsed))
            return f"BotGSTAR · CRASH · restart dans {remaining}s · {crashes} crash"
        if state == BotState.RESTARTING:
            return f"BotGSTAR · redémarrage..."
        return "BotGSTAR"

    def _state_label(self, item=None) -> str:
        with self._lock:
            state = self._state
            started = self._started_at
            restart_at = self._restart_at
        if state == BotState.RUNNING and started:
            return f"🟢 En cours · uptime {_format_duration(datetime.now() - started)}"
        if state == BotState.PAUSED:
            return "⏸️ En pause"
        if state == BotState.CRASHED_WAITING and restart_at:
            elapsed = (datetime.now() - restart_at).total_seconds()
            remaining = max(0, RESTART_DELAY_SECONDS - int(elapsed))
            return f"🔴 Crashé · restart dans {remaining}s"
        if state == BotState.RESTARTING:
            return "🔄 Redémarrage..."
        return "❓ Inconnu"

    def _pause_label(self, item=None) -> str:
        with self._lock:
            return "▶️ Reprendre" if self._state == BotState.PAUSED else "⏸️ Mettre en pause"

    def _startup_label(self, item=None) -> str:
        check = "✅" if STARTUP_VBS.exists() else "⬜"
        return f"{check} Démarrer avec Windows"

    def _tooltip_refresher(self) -> None:
        """Thread : rafraîchit l'icône/tooltip périodiquement."""
        while not self._stop_event.is_set():
            self._refresh_icon()
            for _ in range(TOOLTIP_REFRESH_SECONDS * 10):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    # --------------------------------------------------------
    # FENÊTRE LOGS (Tkinter sur son propre thread)
    # --------------------------------------------------------

    def _run_log_window(self) -> None:
        root = tk.Tk()
        root.title("BotGSTAR · Logs en direct")
        root.geometry("1100x600")
        root.minsize(600, 300)

        # Toolbar
        bar = tk.Frame(root)
        bar.pack(fill=tk.X, padx=6, pady=4)

        autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="Auto-scroll", variable=autoscroll_var).pack(side=tk.LEFT)

        def open_external():
            if LOG_FILE.exists():
                os.startfile(str(LOG_FILE))

        tk.Button(bar, text="Ouvrir dans Notepad", command=open_external).pack(side=tk.LEFT, padx=4)

        def clear_view():
            text.config(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            text.config(state=tk.DISABLED)

        tk.Button(bar, text="Vider la vue", command=clear_view).pack(side=tk.LEFT, padx=4)

        status_lbl = tk.Label(bar, text="", anchor="e")
        status_lbl.pack(side=tk.RIGHT, padx=4)

        # Zone de texte
        text = scrolledtext.ScrolledText(
            root, wrap=tk.NONE, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        text.config(state=tk.DISABLED)

        last_seen = 0  # index dans le tail buffer

        def poll():
            nonlocal last_seen
            with self._log_lock:
                snapshot = list(self._tail)
            new_lines = snapshot[last_seen:]
            last_seen = len(snapshot)
            if new_lines:
                text.config(state=tk.NORMAL)
                for line in new_lines:
                    text.insert(tk.END, line + "\n")
                text.config(state=tk.DISABLED)
                if autoscroll_var.get():
                    text.see(tk.END)
            status_lbl.config(text=f"{len(snapshot)} lignes · {self._state_label()}")
            root.after(500, poll)

        poll()
        root.mainloop()

    # --------------------------------------------------------
    # NOTIFICATIONS
    # --------------------------------------------------------

    def _toast(self, title: str, message: str) -> None:
        if not self.icon:
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    # --------------------------------------------------------
    # MENU
    # --------------------------------------------------------

    def build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(self._state_label, lambda *_: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "📋 Voir les logs en direct",
                self.action_show_logs,
                default=True,  # double-clic icône
            ),
            pystray.MenuItem("📁 Ouvrir dossier logs", self.action_open_log_folder),
            pystray.MenuItem("📁 Ouvrir dossier datas", self.action_open_data_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._pause_label, self.action_pause_or_resume),
            pystray.MenuItem("🔄 Redémarrer", self.action_restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._startup_label, self.action_toggle_startup),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Quitter", self.action_quit),
        )

    # --------------------------------------------------------
    # MAIN
    # --------------------------------------------------------

    def run(self) -> None:
        # Spawn initial du bot
        self._spawn_bot()

        # Threads watchdog + tooltip
        threading.Thread(target=self._watchdog_loop, daemon=True).start()
        threading.Thread(target=self._tooltip_refresher, daemon=True).start()

        # Icône tray
        self.icon = pystray.Icon(
            "BotGSTAR",
            icon=make_icon_image(COLORS[BotState.RUNNING]),
            title="BotGSTAR · démarrage...",
            menu=self.build_menu(),
        )
        # icon.run() bloque le thread principal jusqu'à icon.stop()
        self.icon.run()


# ============================================================
# HELPERS
# ============================================================


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _format_duration(td) -> str:
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def make_icon_image(rgb: tuple[int, int, int]) -> Image.Image:
    """Crée une icône 64x64 : disque coloré avec un B blanc au centre."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([3, 3, size - 3, size - 3], fill=rgb + (255,), outline=(255, 255, 255, 255), width=2)
    try:
        font = ImageFont.truetype("arialbd.ttf", 38)
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2 - 2), "B", fill=(255, 255, 255, 255), font=font, anchor="mm")
    return img


def _write_startup_vbs() -> None:
    """Écrit le .vbs dans le dossier Startup pour autostart au login."""
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    pythonw = PYTHON_EXE.replace("python.exe", "pythonw.exe")
    tray_script = WORKSPACE / "bot_tray.py"
    content = (
        'Set WshShell = CreateObject("WScript.Shell")\r\n'
        f'WshShell.CurrentDirectory = "{WORKSPACE}"\r\n'
        f'WshShell.Run """{pythonw}"" ""{tray_script}""", 0, False\r\n'
    )
    STARTUP_VBS.write_text(content, encoding="utf-8")


# ============================================================
# ENTRY POINT
# ============================================================


if __name__ == "__main__":
    if not BOT_SCRIPT.exists():
        # Fallback : afficher une erreur en console (utile si lancé via python.exe)
        print(f"ERREUR : {BOT_SCRIPT} introuvable.", file=sys.stderr)
        sys.exit(1)
    TrayApp().run()
