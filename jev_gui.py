"""Jev Router desktop launcher: python3 jev_gui.py (Python 3.10+ with Tk)."""

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
from tempfile import NamedTemporaryFile
from threading import Thread
from time import monotonic
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

import jev_updates
import jev_router
from jev_router import DISABLE_ALIASES, ORDER, TIERS, has_api_key, parse_disabled, router_port
from setup_opencode_jev import configure


WORKER_FLAG = "--router-worker"
TIER_SUMMARIES = {
    "haiku": "Простые вопросы и быстрые действия",
    "mini": "Небольшие правки кода и текста",
    "flash": "Длинный контекст и большие документы",
    "terra": "Повседневная разработка и тесты",
    "sol-61": "Задачи через несколько компонентов",
    "sol": "Сложная отладка и цепочки шагов",
    "sonnet": "Сложная разработка и анализ проектов",
    "sol-pro": "Углублённый разбор кода и математики",
    "opus": "Архитектура и самые трудные задачи",
    "astra": "Исключительно сложные задачи с большим контекстом",
    "astra-pro": "Предельная сложность и длительное исследование",
}


def preferences_path():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jev-router" / "gui.json"


def load_selection(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        enabled = data.get("enabled_tiers") if isinstance(data, dict) else None
        if isinstance(enabled, list) and all(isinstance(name, str) for name in enabled):
            # Unknown removed tiers are harmless; an empty selection stays empty.
            return set(enabled).intersection(ORDER)
    except (OSError, ValueError):
        pass
    return None


def save_selection(path, selected):
    """Persist only non-secret tier names, atomically; never persist environment/key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix="gui.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write((json.dumps({"enabled_tiers": selected}) + "\n").encode("utf-8"))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def router_command(disabled):
    if getattr(sys, "frozen", False):
        command = [sys.executable, WORKER_FLAG]
    else:
        command = [sys.executable, "-B", str(Path(__file__).resolve().with_name("jev_router.py"))]
    if disabled:
        command += ["--disable", ",".join(disabled)]
    return command


def watch_output(stream, ready_line, events):
    # Drain the pipe even after readiness, but do not retain child logs or secrets.
    with stream:
        for line in stream:
            if line.rstrip() == ready_line:
                events.put("ready")
    events.put("eof")


class Launcher:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.closing = False
        self.state = "idle"
        self.deadline = 0
        self.stop_status = "Роутер остановлен."
        self.events = None
        self.status = tk.StringVar(value="Роутер остановлен.")
        self.setup_status = tk.StringVar()
        self.update_status = tk.StringVar(value=f"Версия {jev_updates.APP_VERSION}")
        self.update_events = queue.SimpleQueue()
        self.update_checking = False
        self.key = tk.StringVar()
        self.port = tk.StringVar(value=os.environ.get("JEV_ROUTER_PORT", "8787"))
        self.auto_setup = tk.BooleanVar(value=True)
        self.make_default = tk.BooleanVar(value=False)
        self.preferences = preferences_path()
        selected = load_selection(self.preferences)
        if selected is None:
            disabled = {DISABLE_ALIASES.get(name, name) for name in parse_disabled([], os.environ)}
            selected = set(ORDER) - disabled
        self.levels = {name: tk.BooleanVar(value=name in selected) for name in ORDER}

        root.title("Jev Router")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.configure(background="#f1f0eb")
        root.geometry(f"{min(680, root.winfo_screenwidth())}x{min(820, root.winfo_screenheight())}")
        root.minsize(340, 240)

        bg, ink, muted, mint = "#f1f0eb", "#181818", "#595b56", "#76d8ae"
        style = ttk.Style(root)
        # A portable button style; checkboxes below remain native Tk controls.
        style.theme_use("clam")
        style.configure("Primary.TButton", background=ink, foreground="white",
                        padding=(18, 10), borderwidth=0, font=("TkDefaultFont", 11, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#b8b8b2"), ("active", "#353535")],
                  foreground=[("disabled", "#50504c")])
        style.configure("Secondary.TButton", background=bg, foreground=ink,
                        padding=(12, 9), borderwidth=1, font=("TkDefaultFont", 10))
        style.map("Secondary.TButton", background=[("active", "#e2e2db")],
                  foreground=[("disabled", "#888a84")])

        # The whole page scrolls, including actions and status on short screens.
        canvas = tk.Canvas(root, background=bg, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        panel = tk.Frame(canvas, background=bg, padx=24, pady=22)
        panel_window = canvas.create_window((0, 0), window=panel, anchor="nw")
        panel.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        wrap_widgets = []

        def resize(event):
            canvas.itemconfigure(panel_window, width=event.width)
            for widget, inset in wrap_widgets:
                widget.configure(wraplength=max(100, event.width - 48 - inset))

        canvas.bind("<Configure>", resize)

        def scroll(event):
            if event.widget.winfo_toplevel() != root:
                return
            if event.num in (4, 5):
                steps = -1 if event.num == 4 else 1
            elif sys.platform == "darwin":
                steps = -event.delta
            else:
                steps = -int(event.delta / 120) if abs(event.delta) >= 120 else -event.delta
            canvas.yview_scroll(steps, "units")

        root.bind_all("<MouseWheel>", scroll, add="+")
        root.bind_all("<Button-4>", scroll, add="+")
        root.bind_all("<Button-5>", scroll, add="+")

        def reveal_focus(event):
            widget = event.widget
            if widget == root or widget.winfo_toplevel() != root:
                return
            def reveal():
                if not widget.winfo_exists():
                    return
                top = widget.winfo_rooty() - canvas.winfo_rooty()
                bottom = top + widget.winfo_height()
                shift = top if top < 0 else max(0, bottom - canvas.winfo_height())
                height = panel.winfo_height()
                if shift and height:
                    canvas.yview_moveto((canvas.canvasy(0) + shift) / height)
            root.after_idle(reveal)

        root.bind_all("<FocusIn>", reveal_focus, add="+")

        def label(parent, text=None, *, font=("TkDefaultFont", 10), color=ink, **kwargs):
            return tk.Label(parent, text=text, background=bg, foreground=color, font=font,
                            anchor="w", justify="left", **kwargs)

        def wrapped(parent, text=None, *, inset=0, **kwargs):
            widget = label(parent, text, **kwargs)
            wrap_widgets.append((widget, inset))
            return widget

        def separator():
            tk.Frame(panel, background="#d3d3cb", height=1).pack(fill="x", pady=(18, 16))

        def checkbox(parent, text, variable, command=None):
            widget = tk.Checkbutton(parent, text=text, variable=variable, command=command,
                                    background=bg, foreground=ink, activebackground=bg,
                                    activeforeground=ink, disabledforeground="#858680",
                                    selectcolor="white", anchor="w", justify="left",
                                    font=("TkDefaultFont", 10), highlightthickness=0)
            wrap_widgets.append((widget, 4))
            self.inputs.append(widget)
            return widget

        self.inputs = []
        label(panel, "JEV  /  ROUTER", font=("TkDefaultFont", 10, "bold"), color=muted).pack(anchor="w")
        heading = tk.Frame(panel, background=bg)
        heading.pack(fill="x", pady=(12, 2))
        label(heading, "Ваш", font=("TkDefaultFont", 27, "bold")).pack(anchor="w")
        label(heading, "AI-маршрут", font=("TkTextFont", 27, "italic")).pack(anchor="w")
        wrapped(panel, "Модели, OpenCode и запуск — в одном месте.", color=muted).pack(anchor="w")

        separator()
        label(panel, "01  /  ПОДКЛЮЧЕНИЕ", font=("TkDefaultFont", 10, "bold"), color=muted).pack(anchor="w")
        label(panel, "Ключ Polza", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", pady=(12, 4))
        key_entry = ttk.Entry(panel, textvariable=self.key, show="*")
        key_entry.pack(fill="x", ipady=4)
        self.inputs.append(key_entry)
        wrapped(panel, "Пустое поле → POLZA_API_KEY из окружения. Ключ не сохраняется.",
                color=muted).pack(anchor="w", pady=(5, 10))
        label(panel, "Локальный порт", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", pady=(0, 4))
        port_entry = ttk.Entry(panel, textvariable=self.port, width=12)
        port_entry.pack(anchor="w", ipady=4)
        self.inputs.append(port_entry)

        separator()
        label(panel, "02  /  МОДЕЛИ", font=("TkDefaultFont", 10, "bold"), color=muted).pack(anchor="w")
        label(panel, "Уровни Jev Auto", font=("TkDefaultFont", 17, "bold")).pack(anchor="w", pady=(8, 3))
        wrapped(panel, "Выберите модели для автоматической маршрутизации.", color=muted).pack(anchor="w", pady=(0, 8))
        for name in ORDER:
            row = tk.Frame(panel, background=bg)
            row.pack(fill="x", pady=(4, 4))
            checkbox(row, f"{name} — {TIERS[name]['model']}", self.levels[name],
                     self.selection_changed).pack(anchor="w", fill="x")
            wrapped(row, TIER_SUMMARIES.get(name, TIERS[name]["when"]), color=muted,
                    inset=24).pack(anchor="w", padx=(24, 0))
        wrapped(panel, "Выбор ограничивает только автороутинг и его запасные модели; "
                "явно выбранные модели проходят транзитом. Для маршрутизации используется typesafe/jev, "
                "для изображений — дополнительная vision-модель. Возможны расходы.",
                color=muted).pack(anchor="w", pady=(12, 0))

        separator()
        label(panel, "03  /  OPENCODE", font=("TkDefaultFont", 10, "bold"), color=muted).pack(anchor="w")
        label(panel, "Подключение OpenCode", font=("TkDefaultFont", 17, "bold")).pack(anchor="w", pady=(8, 5))
        for text, var in [("Настраивать OpenCode после запуска", self.auto_setup),
                          ("Сделать Jev Auto моделью по умолчанию", self.make_default)]:
            checkbox(panel, text, var).pack(anchor="w", fill="x", pady=2)
        self.setup_button = ttk.Button(panel, text="Настроить сейчас", command=self.setup_opencode,
                                       style="Secondary.TButton")
        self.setup_button.pack(anchor="w", pady=(8, 0))
        wrapped(panel, textvariable=self.setup_status, color=muted).pack(anchor="w", pady=(4, 0))

        separator()
        label(panel, "04  /  УПРАВЛЕНИЕ", font=("TkDefaultFont", 10, "bold"), color=muted).pack(anchor="w")
        status_row = tk.Frame(panel, background=bg)
        status_row.pack(fill="x", pady=(10, 12))
        tk.Frame(status_row, background=mint, width=10, height=10).pack(side="left", anchor="n", pady=4, padx=(0, 10))
        wrapped(status_row, textvariable=self.status, inset=20, font=("TkDefaultFont", 11, "bold")).pack(side="left", fill="x", expand=True)
        actions = tk.Frame(panel, background=bg)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Запустить", command=self.start, style="Primary.TButton")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Остановить", command=self.stop, style="Secondary.TButton")
        self.stop_button.pack(side="left", padx=8)
        self.update_button = ttk.Button(panel, text="Проверить обновления", command=self.check_updates,
                                        style="Secondary.TButton")
        self.update_button.pack(anchor="w", pady=(16, 0))
        wrapped(panel, textvariable=self.update_status, color=muted).pack(anchor="w", pady=(5, 0))
        self.update_controls()
        root.after(100, self.tick)

    def check_updates(self):
        if self.update_checking:
            return
        self.update_checking = True
        self.update_button.configure(state="disabled")
        self.update_status.set("Проверяем GitHub Releases…")

        def check():
            try:
                result = jev_updates.check_for_updates()
            except Exception:
                result = jev_updates.UpdateResult("error", "Не удалось проверить обновления")
            self.update_events.put(result)

        Thread(target=check, daemon=True).start()

    def show_update_result(self, result):
        self.update_checking = False
        self.update_button.configure(state="normal")
        details = result.message
        if result.latest_version:
            details += f" · последняя версия {result.latest_version}"
        self.update_status.set(details)
        if result.status == "update_available" and result.release_url:
            if messagebox.askyesno("Обновление Jev Router",
                                   f"Доступна версия {result.latest_version}. Открыть страницу релиза?\n\n"
                                   "Скачивание и установка выполняются вручную.", parent=self.root):
                if not webbrowser.open(result.release_url):
                    self.update_status.set("Не удалось открыть браузер. " + result.release_url)

    def selection_changed(self):
        selected = [name for name in ORDER if self.levels[name].get()]
        try:
            save_selection(self.preferences, selected)
        except OSError:
            self.status.set("Не удалось сохранить выбор уровней.")
        self.update_controls()

    def update_controls(self):
        idle = self.state == "idle"
        for widget in self.inputs:
            widget.configure(state="normal" if idle else "disabled")
        enabled = any(var.get() for var in self.levels.values())
        self.start_button.configure(state="normal" if idle and enabled else "disabled")
        self.stop_button.configure(state="normal" if self.state in {"starting", "running"} else "disabled")
        self.setup_button.configure(state="normal" if self.state in {"idle", "running"} else "disabled")
        if idle and not enabled:
            self.status.set("Выберите хотя бы один уровень.")
        elif idle and self.status.get() == "Выберите хотя бы один уровень.":
            self.status.set("Роутер остановлен.")

    def selected_port(self):
        return router_port({"JEV_ROUTER_PORT": self.port.get()})

    def setup_opencode(self):
        self.setup_status.set("")
        try:
            port = self.selected_port()
            root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"
            path, backup = configure(root, port, make_default=self.make_default.get())
        except (OSError, ValueError) as error:
            self.setup_status.set(f"Настройка не выполнена: {error}")
            return
        self.setup_status.set(
            f"Готово: {path}" + (f"\nРезервная копия: {backup}" if backup else "")
            + ("" if self.make_default.get() else
               "\nЕсли другая модель уже выбрана по умолчанию, выберите jev/auto вручную."))

    def start(self):
        if self.process is not None:
            return
        disabled = [name for name in ORDER if not self.levels[name].get()]
        if len(disabled) == len(ORDER):
            return
        try:
            port = self.selected_port()
        except ValueError as error:
            self.status.set(str(error))
            return
        key = self.key.get().strip() or os.environ.get("POLZA_API_KEY", "").strip()
        if not has_api_key(key) or not key.isascii() or any(not 33 <= ord(c) <= 126 for c in key):
            self.status.set("Введите действительный ключ Polza или задайте POLZA_API_KEY.")
            return
        env = dict(os.environ, POLZA_API_KEY=key, JEV_ROUTER_PORT=str(port), PYTHONIOENCODING="utf-8")
        # The checkboxes are authoritative; the CLI normally merges both sources.
        env.pop("JEV_DISABLE_TIERS", None)
        if getattr(sys, "frozen", False):
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        try:
            self.process = subprocess.Popen(
                router_command(disabled), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, ValueError):
            self.status.set("Не удалось создать процесс Jev Router.")
            return
        self.address = f"http://127.0.0.1:{port}"
        self.events = queue.SimpleQueue()
        Thread(target=watch_output, args=(self.process.stdout, f"Jev Router listening on {self.address}",
                                         self.events), daemon=True).start()
        self.deadline = monotonic() + 15
        self.state = "starting"
        self.setup_status.set("")
        self.status.set("Роутер запускается…")
        self.update_controls()

    def tick(self):
        if hasattr(self, "update_events"):
            try:
                result = self.update_events.get_nowait()
            except queue.Empty:
                pass
            else:
                if not self.closing:
                    self.show_update_result(result)
        if self.process is not None:
            code = self.process.poll()
            if code is not None:
                if self.state == "starting":
                    status = f"Не удалось запустить роутер (код {code}). Проверьте, свободен ли порт."
                elif self.state == "stopping":
                    status = self.stop_status
                else:
                    status = f"Роутер завершился (код {code})."
                self.process = None
                self.events = None
                self.state = "idle"
                self.status.set(status)
                self.update_controls()
            elif self.state == "starting":
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    event = None
                if event == "ready":
                    self.state = "running"
                    self.status.set(f"Роутер работает: {self.address}/v1")
                    self.update_controls()
                    if self.auto_setup.get():
                        self.setup_opencode()
                elif event == "eof" or monotonic() >= self.deadline:
                    self.stop("Роутер не подтвердил запуск.")
            elif self.state == "stopping" and monotonic() >= self.deadline:
                self.signal_process(force=True)
                self.deadline = monotonic() + 2
        if self.closing and self.process is None:
            self.root.destroy()
        else:
            self.root.after(100, self.tick)

    def signal_process(self, *, force=False):
        try:
            if force:
                self.process.kill()
            else:
                self.process.terminate()
        except OSError:
            if self.process.poll() is None:
                self.status.set("Не удалось остановить процесс; повторяем попытку…")

    def stop(self, status="Роутер остановлен."):
        if self.process is None or self.state == "stopping":
            return
        self.stop_status = status
        self.state = "stopping"
        self.status.set("Роутер останавливается…")
        self.deadline = monotonic() + 2
        self.signal_process()
        self.update_controls()

    def close(self):
        self.closing = True
        self.stop()


def main():
    if getattr(sys, "frozen", False) and sys.argv[1:2] == [WORKER_FLAG]:
        # A windowed frozen executable may initialize sys.stdout/stderr to None.
        if sys.stdout is None:
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes
                get_handle = ctypes.windll.kernel32.GetStdHandle
                get_handle.argtypes = [wintypes.DWORD]
                get_handle.restype = wintypes.HANDLE
                handle = get_handle(-11 & 0xffffffff)  # STD_OUTPUT_HANDLE from parent
                if not handle or handle == ctypes.c_void_p(-1).value:
                    raise SystemExit("Worker has no output pipe.")
                fd = msvcrt.open_osfhandle(handle, os.O_TEXT)
                sys.stdout = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
            else:
                sys.stdout = open(1, "w", encoding="utf-8", closefd=False)
        if sys.stderr is None:
            sys.stderr = sys.stdout
        jev_router.main(sys.argv[2:])
        return
    if sys.version_info < (3, 10):
        raise SystemExit("Требуется Python 3.10 или новее.")
    root = tk.Tk()
    Launcher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
