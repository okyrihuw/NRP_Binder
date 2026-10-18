from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

APP_NAME = "Выдача"
WINDOWS = sys.platform == "win32"

# The settings live outside the executable, so updates/rebuilds do not erase presets.
if os.name == "nt":
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BindiVidacha"
else:
    CONFIG_DIR = Path.home() / ".config" / "bindi_vidacha"
CONFIG_FILE = CONFIG_DIR / "preset.json"

HOTKEYS = [
    ("ТП игроков к себе", "Alt+Ctrl+1", "teleport"),
    ("Выдать скины", "Alt+Ctrl+2", "skins"),
    ("Выдать оружие - 1", "Alt+Ctrl+3", "weapon_1"),
    ("Выдать оружие - 2", "Alt+Ctrl+4", "weapon_2"),
    ("Выдать оружие - 3", "Alt+Ctrl+5", "weapon_3"),
    ("Выдать все оружия", "Alt+Ctrl+6", "weapons"),
    ("Выдать здоровье", "Alt+Ctrl+8", "health"),
    ("Выдать броню", "Alt+Ctrl+9", "armor"),
    ("Возрождения", "Alt+Ctrl+0", "revive"),
]

COMMANDS = [
    ("/respawn", "Возродиться самому"),
    ("/suicide", "Самоубийство"),
    ("/admins", "Список администраторов онлайн"),
    ("/inv", "Стать невидимым"),
    ("/commands", "Список доступных команд"),
    ("/reviving <id>", "Возродить игрока"),
    ("/chatlist", "Список чатов"),
    ("/deleteallmessagefrom <channel_id> <reason>", "Удалить сообщения из чата"),
    ("/deleteplayermessage <player_id> <reason>", "Удалить сообщения игрока"),
    ("/punmute <id>", "Снять мут"),
    ("/fixveh <vid>", "Починить машину"),
    ("/flip <vid>", "Поставить машину на колёса"),
    ("/get <id>", "Телепортировать игрока к себе"),
    ("/pwarp <id>", "Телепортироваться к игроку"),
    ("/vget <vid>", "Телепортировать машину к себе"),
    ("/setfuel <vid> <топливо>", "Установить топливо машине"),
    ("/pfreeze <id>", "Заморозить игрока"),
    ("/punfreeze <id>", "Разморозить игрока"),
    ("/unjail <id>", "Вытащить игрока из тюрьмы"),
    ("/tempnickname <id> <имя> <фамилия>", "Временное имя игрока"),
    ("/tempskin <id> <скин>", "Временный скин игрока"),
    ("/sethp <id> <число>", "Установить здоровье"),
    ("/setarmor <id> <число>", "Установить броню"),
    ("/setnickname <id> <имя> <фамилия>", "Сменить ник игрока"),
    ("/p <id> <пункт правила> <причина>", "Наказать за нарушение правила"),
    ("/weapongive <id> <ID оружия> <патроны>", "Выдать оружие"),
    ("/weapontake <id> <ID оружия> <патроны>", "Отобрать оружие"),
    ("/takeallweapons <id>", "Отобрать всё оружие"),
    ("/toprison <id>", "Перевести в колонию"),
    ("/freeprison <id>", "Освободить из колонии"),
    ("/adminmode", "Включить/выключить режим администратора"),
]


class WindowsHotkeys:
    """Register visible global hotkeys and type commands into the foreground game."""

    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    WM_APP_TOGGLE = 0x8001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    INPUT_KEYBOARD = 1
    VK_F8 = 0x77
    VK_RETURN = 0x0D

    def __init__(self, notify: Callable[[str], None], run_action: Callable[[int], None]):
        self.notify = notify
        self.run_action = run_action
        self.thread: threading.Thread | None = None
        self.thread_id = None
        self.thread_ready = threading.Event()
        self.toggle_results: queue.Queue[bool] = queue.Queue()
        self.enabled = False
        self._user32 = None

    def start(self) -> None:
        if not WINDOWS:
            return
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self.thread = threading.Thread(target=self._message_loop, daemon=True)
        self.thread.start()

    def _message_loop(self) -> None:
        ctypes = self._ctypes
        wintypes = self._wintypes
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        # Force creation of this thread's message queue before the UI posts toggle requests.
        self._user32.PeekMessageW(ctypes.byref(wintypes.MSG()), None, 0, 0, 0)
        self.thread_ready.set()
        msg = wintypes.MSG()
        while True:
            result = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == self.WM_APP_TOGGLE:
                requested = bool(msg.wParam)
                success = True
                if requested:
                    keys = [ord("1"), ord("2"), ord("3"), ord("4"), ord("5"), ord("6"), ord("8"), ord("9"), ord("0")]
                    for hotkey_id, key in enumerate(keys, start=1):
                        if not self._user32.RegisterHotKey(None, hotkey_id, self.MOD_ALT | self.MOD_CONTROL, key):
                            success = False
                    if not success:
                        for hotkey_id in range(1, len(HOTKEYS) + 1):
                            self._user32.UnregisterHotKey(None, hotkey_id)
                    self.enabled = success
                else:
                    self.enabled = False
                    for hotkey_id in range(1, len(HOTKEYS) + 1):
                        self._user32.UnregisterHotKey(None, hotkey_id)
                self.toggle_results.put(success)
            elif msg.message == self.WM_HOTKEY:
                index = int(msg.wParam) - 1
                if self.enabled and 0 <= index < len(HOTKEYS):
                    self.run_action(index)

    def set_enabled(self, enabled: bool) -> bool:
        if not WINDOWS or self.thread is None or not self.thread_ready.wait(2):
            return False
        self._user32.PostThreadMessageW(self.thread_id, self.WM_APP_TOGGLE, int(enabled), 0)
        try:
            result = self.toggle_results.get(timeout=2)
        except queue.Empty:
            return False
        return result

    def stop(self) -> None:
        if self.enabled:
            self.set_enabled(False)
        if self.thread_id:
            self._user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)

    def _key(self, virtual_key: int, up: bool = False) -> None:
        class KEYBDINPUT(self._ctypes.Structure):
            _fields_ = [("wVk", self._wintypes.WORD), ("wScan", self._wintypes.WORD),
                        ("dwFlags", self._wintypes.DWORD), ("time", self._wintypes.DWORD),
                        ("dwExtraInfo", self._ctypes.c_size_t)]

        class INPUT_UNION(self._ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(self._ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", self._wintypes.DWORD), ("u", INPUT_UNION)]

        flags = self.KEYEVENTF_KEYUP if up else 0
        item = INPUT(type=self.INPUT_KEYBOARD, ki=KEYBDINPUT(virtual_key, 0, flags, 0, 0))
        self._user32.SendInput(1, self._ctypes.byref(item), self._ctypes.sizeof(INPUT))

    def _unicode_char(self, char: str) -> None:
        class KEYBDINPUT(self._ctypes.Structure):
            _fields_ = [("wVk", self._wintypes.WORD), ("wScan", self._wintypes.WORD),
                        ("dwFlags", self._wintypes.DWORD), ("time", self._wintypes.DWORD),
                        ("dwExtraInfo", self._ctypes.c_size_t)]

        class INPUT_UNION(self._ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(self._ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", self._wintypes.DWORD), ("u", INPUT_UNION)]

        code = ord(char)
        inputs = (INPUT * 2)(
            INPUT(type=self.INPUT_KEYBOARD, ki=KEYBDINPUT(0, code, self.KEYEVENTF_UNICODE, 0, 0)),
            INPUT(type=self.INPUT_KEYBOARD, ki=KEYBDINPUT(0, code, self.KEYEVENTF_UNICODE | self.KEYEVENTF_KEYUP, 0, 0)),
        )
        self._user32.SendInput(2, inputs, self._ctypes.sizeof(INPUT))

    def type_command(self, command: str, first: bool, delay: float) -> None:
        if first:
            self._key(self.VK_F8)
            self._key(self.VK_F8, True)
            time.sleep(0.18)
        for char in command:
            self._unicode_char(char)
        time.sleep(0.04)
        self._key(self.VK_RETURN)
        self._key(self.VK_RETURN, True)
        # Let the game process each line before sending the next one.
        time.sleep(delay)


class App:
    BG = "#f0f0f0"
    BORDER = "#777777"
    TEXT = "#111111"
    RED = "#ff0000"

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_NAME)
        root.geometry("1200x700")
        root.minsize(980, 620)
        root.configure(bg=self.BG)
        self.fields: dict[str, tk.Entry] = {}
        self.status = tk.StringVar(value="Горячие клавиши выключены")
        self.hotkeys_enabled = tk.BooleanVar(value=False)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.send_lock = threading.Lock()
        self.hotkeys = WindowsHotkeys(self._queue_status, self._queue_hotkey)
        self._build_ui()
        self.hotkeys.start()
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista" if WINDOWS else "clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(8, 3), font=("Arial", 9))
        style.configure("TButton", font=("Arial", 12), padding=10)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=2, pady=(2, 0))
        commands_tab = tk.Frame(notebook, bg=self.BG)
        gmp_tab = tk.Frame(notebook, bg=self.BG)
        notebook.add(commands_tab, text="Команды")
        notebook.add(gmp_tab, text="ГМП")
        self._build_commands(commands_tab)
        self._build_gmp(gmp_tab)

        footer = tk.Frame(self.root, bg=self.BG, height=126, highlightbackground=self.BORDER,
                          highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Label(footer, text="by Пепси Кольный", bg=self.BG, fg=self.RED,
                 font=("Arial", 42, "bold")).pack(expand=True)
        bottom = tk.Frame(self.root, bg=self.BG)
        bottom.pack(fill="x", side="bottom", padx=3, pady=(2, 3))
        tk.Label(bottom, text="ДС: okyrihuw..", bg=self.BG, fg="black",
                 font=("Arial", 10, "bold")).pack(side="left")
        tk.Label(bottom, textvariable=self.status, bg=self.BG, fg="#333333",
                 font=("Arial", 9)).pack(side="right", padx=8)

    def _build_commands(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        header = tk.Frame(parent, bg=self.BG)
        header.grid(row=0, column=0, sticky="ew", pady=(62, 8))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=1)
        tk.Label(header, text="Действие:", bg=self.BG, font=("Arial", 14)).grid(row=0, column=0)
        tk.Label(header, text="Клавиша активации:", bg=self.BG, font=("Arial", 14)).grid(row=0, column=1)
        tk.Frame(parent, bg="#333333", height=1).grid(row=1, column=0, sticky="ew")
        table = tk.Frame(parent, bg=self.BG)
        table.grid(row=2, column=0, sticky="nsew", pady=(4, 3))
        table.grid_columnconfigure(0, weight=1)
        table.grid_columnconfigure(1, weight=1)
        for row, (label, key, _action) in enumerate(HOTKEYS):
            tk.Label(table, text=label, bg=self.BG, font=("Arial", 13, "bold"),
                     anchor="w").grid(row=row, column=0, padx=(25, 5), pady=7, sticky="w")
            tk.Label(table, text=key, bg=self.BG, font=("Arial", 13, "bold"),
                     anchor="w").grid(row=row, column=1, padx=(5, 0), pady=7, sticky="w")
        bottom = tk.Frame(parent, bg=self.BG)
        bottom.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 12))
        if WINDOWS:
            tk.Checkbutton(bottom, text="Включить горячие клавиши (отправка в активное окно)",
                           variable=self.hotkeys_enabled, command=self._toggle_hotkeys,
                           bg=self.BG, activebackground=self.BG, font=("Arial", 10)).pack(side="left")
        else:
            tk.Label(bottom, text="Глобальные клавиши доступны в Windows-сборке.", bg=self.BG,
                     font=("Arial", 10)).pack(side="left")
        tk.Button(bottom, text="Список команд", command=self._show_command_catalog,
                  font=("Arial", 10), padx=8, pady=3).pack(side="right")

    def _build_gmp(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        content = tk.Frame(parent, bg=self.BG)
        content.grid(row=0, column=0, sticky="nsew")
        for col in range(4):
            content.grid_columnconfigure(col, weight=1, uniform="groups")
        content.grid_columnconfigure(4, weight=0)
        for group in range(4):
            frame = tk.Frame(content, bg=self.BG)
            frame.grid(row=0, column=group, sticky="nw", padx=(4, 3), pady=(8, 4))
            for i in range(15):
                self._field(frame, f"id_{group}_{i}", f"ID-{i+1}:", i, width=12)
            line = 15
            for n in range(2):
                self._field(frame, f"skin_{group}_{n}", f"Скин:", line+n, width=12)
            line += 2
            for n in range(3):
                self._field(frame, f"weapon_{group}_{n}", f"Оружие:", line+n, width=12)

        separator = tk.Frame(content, bg="#153c86", width=2)
        separator.grid(row=0, column=4, sticky="ns", padx=(5, 2))
        controls = tk.Frame(content, bg=self.BG, width=255)
        controls.grid(row=0, column=5, sticky="nsew", padx=(20, 16))
        controls.grid_propagate(False)
        controls.grid_columnconfigure(1, weight=1)
        controls.grid_rowconfigure(0, weight=1)
        stats = tk.Frame(controls, bg=self.BG)
        stats.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        stats.grid_columnconfigure(1, weight=1)
        self._field(stats, "armor", "Броня:", 0, width=12, label_width=8)
        self._field(stats, "health", "ХР:", 1, width=12, label_width=8)
        self._field(stats, "sleep", "sleep:", 2, width=12, label_width=8)

        buttons = [
            ("[ГМП]Сохранить", self.save_preset),
            ("[ГМП]Очистить", self.clear_fields),
            ("[ГМП]Обновить", self.load_preset),
        ]
        for index, (text, callback) in enumerate(buttons):
            tk.Button(controls, text=text, command=callback, height=2, width=18,
                      font=("Arial", 12)).grid(row=index + 2, column=0, columnspan=2,
                                                sticky="ew", pady=(7, 4))

    def _field(self, parent: tk.Widget, key: str, label: str, row: int, width: int = 12,
               label_width: int = 8) -> None:
        tk.Label(parent, text=label, bg=self.BG, fg=self.TEXT,
                 font=("Arial", 12), width=label_width, anchor="w").grid(
                     row=row, column=0, sticky="w", pady=2)
        entry = tk.Entry(parent, width=width, font=("Arial", 11), relief="sunken", bd=1)
        entry.grid(row=row, column=1, sticky="w", pady=2, ipady=3)
        self.fields[key] = entry

    def _toggle_hotkeys(self) -> None:
        enabled = self.hotkeys_enabled.get()
        if self.hotkeys.set_enabled(enabled):
            self.status.set("Горячие клавиши включены" if enabled else "Горячие клавиши выключены")
        else:
            self.hotkeys_enabled.set(False)
            self.status.set("Не удалось зарегистрировать клавиши — проверьте конфликты")
            messagebox.showwarning("Горячие клавиши", "Не удалось включить сочетания. Возможно, они уже заняты другой программой.")

    def _queue_status(self, text: str) -> None:
        self.events.put(("status", text))

    def _queue_hotkey(self, index: int) -> None:
        # Read Tk widgets only on the UI thread; the Win32 message loop stays separate.
        self.events.put(("hotkey", index))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(str(value))
                elif kind == "hotkey":
                    self._hotkey_action(int(value))
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _all_ids(self) -> list[str]:
        return [entry.get().strip() for group in range(4) for i in range(15)
                if (entry := self.fields[f"id_{group}_{i}"]).get().strip()]

    @staticmethod
    def _valid_token(value: str) -> bool:
        return bool(value) and value.isascii() and value.isdigit()

    def _commands_for(self, action_index: int) -> list[str]:
        action = HOTKEYS[action_index][2]
        ids = self._all_ids()
        if action in ("teleport", "revive"):
            verb = "/get" if action == "teleport" else "/reviving"
            return [f"{verb} {pid}" for pid in ids if self._valid_token(pid)]
        if action == "skins":
            result = []
            for group in range(4):
                group_ids = [self.fields[f"id_{group}_{i}"].get().strip() for i in range(15)]
                skins = [self.fields[f"skin_{group}_{i}"].get().strip() for i in range(2)]
                for pid in group_ids:
                    if not self._valid_token(pid):
                        continue
                    for skin in skins:
                        if self._valid_token(skin):
                            result.append(f"/tempskin {pid} {skin}")
            return result
        if action.startswith("weapon") or action == "weapons":
            slots = range(3) if action == "weapons" else [int(action[-1]) - 1]
            result = []
            for group in range(4):
                group_ids = [self.fields[f"id_{group}_{i}"].get().strip() for i in range(15)]
                for slot in slots:
                    weapon = self.fields[f"weapon_{group}_{slot}"].get().strip()
                    if not self._valid_token(weapon):
                        continue
                    for pid in group_ids:
                        if self._valid_token(pid):
                            # The game command requires an ammunition count; use 1000 per weapon.
                            result.append(f"/weapongive {pid} {weapon} 1000")
            return result
        if action in ("health", "armor"):
            value = self.fields[action].get().strip()
            if not self._valid_token(value):
                return []
            verb = "/sethp" if action == "health" else "/setarmor"
            return [f"{verb} {pid} {value}" for pid in ids if self._valid_token(pid)]
        return []

    def _delay_seconds(self) -> float:
        text = self.fields["sleep"].get().strip()
        try:
            return min(max(float(text), 0.0), 60000.0) / 1000.0 if text else 0.0
        except ValueError:
            return 0.0

    def _hotkey_action(self, index: int) -> None:
        if not self.send_lock.acquire(blocking=False):
            self.status.set("Предыдущая отправка ещё выполняется")
            return
        commands = self._commands_for(index)
        if not commands:
            self.send_lock.release()
            self.status.set("Нет заполненных данных — команды не отправлены")
            return
        self.status.set(f"Отправка: {len(commands)} команд(ы). Консоль F8 останется открытой")
        delay = self._delay_seconds()
        threading.Thread(target=self._send_commands, args=(commands, delay), daemon=True).start()

    def _send_commands(self, commands: list[str], delay: float) -> None:
        try:
            if not WINDOWS:
                return
            for index, command in enumerate(commands):
                self.hotkeys.type_command(command, first=index == 0, delay=delay)
            self._queue_status(f"Готово: отправлено {len(commands)} команд(ы)")
        finally:
            self.send_lock.release()

    def save_preset(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {key: entry.get() for key, entry in self.fields.items()}
            CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.set("Пресет сохранён")
            messagebox.showinfo("Пресет", "Настройки сохранены.")
        except OSError as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))

    def load_preset(self) -> None:
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.status.set("Сохранённый пресет не найден")
            messagebox.showinfo("Пресет", "Пока нет сохранённого пресета.")
            return
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("Ошибка загрузки", str(exc))
            return
        for key, entry in self.fields.items():
            entry.delete(0, tk.END)
            entry.insert(0, str(data.get(key, "")))
        self.status.set("Пресет загружен")

    def clear_fields(self) -> None:
        for entry in self.fields.values():
            entry.delete(0, tk.END)
        self.status.set("Поля очищены; сохранённый пресет не изменён")

    def _show_command_catalog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Список команд")
        win.geometry("750x600")
        win.configure(bg=self.BG)
        tk.Label(win, text="Доступные команды", bg=self.BG, font=("Arial", 16, "bold")).pack(pady=10)
        container = tk.Frame(win, bg=self.BG)
        container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        canvas = tk.Canvas(container, bg=self.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=self.BG)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for row, (command, description) in enumerate(COMMANDS):
            tk.Label(inner, text=command, bg=self.BG, font=("Consolas", 10, "bold"),
                     anchor="w", width=49).grid(row=row, column=0, sticky="w", padx=8, pady=3)
            tk.Label(inner, text=description, bg=self.BG, font=("Arial", 10),
                     anchor="w").grid(row=row, column=1, sticky="w", padx=8, pady=3)

    def close(self) -> None:
        self.hotkeys.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
