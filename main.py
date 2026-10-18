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
TEST_HOTKEYS = [("Ctrl+1", 0x31), ("Ctrl+2", 0x32), ("Ctrl+3", 0x33)]

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
    KEYEVENTF_SCANCODE = 0x0008
    INPUT_KEYBOARD = 1
    VK_RETURN = 0x0D
    VK_CONTROL = 0x11
    VK_V = 0x56
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    def __init__(self, notify: Callable[[str], None], run_action: Callable[[int], None],
                 run_test: Callable[[int], None]):
        self.notify = notify
        self.run_action = run_action
        self.run_test = run_test
        self.thread: threading.Thread | None = None
        self.thread_id = None
        self.thread_ready = threading.Event()
        self.toggle_results: queue.Queue[tuple[bool, list[int]]] = queue.Queue()
        self.main_enabled = False
        self.test_enabled = False
        self.registered_ids: set[int] = set()
        self._user32 = None

    def start(self, owner_hwnd: int | None = None) -> None:
        if not WINDOWS:
            return
        self.owner_hwnd = owner_hwnd
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._user32.OpenClipboard.argtypes = [wintypes.HWND]
        self._user32.OpenClipboard.restype = ctypes.c_int
        self._user32.EmptyClipboard.argtypes = []
        self._user32.EmptyClipboard.restype = ctypes.c_int
        self._user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        self._user32.SetClipboardData.restype = ctypes.c_void_p
        self._user32.CloseClipboard.argtypes = []
        self._user32.CloseClipboard.restype = ctypes.c_int
        self._kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self._kernel32.GlobalAlloc.restype = ctypes.c_void_p
        self._kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalLock.restype = ctypes.c_void_p
        self._kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalUnlock.restype = ctypes.c_int
        self._kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.GlobalFree.restype = ctypes.c_void_p
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._user32.RegisterHotKey.restype = ctypes.c_int
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = ctypes.c_int
        self._user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.PostThreadMessageW.restype = ctypes.c_int
        self._user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self._user32.GetMessageW.restype = ctypes.c_int
        self._user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self._user32.PeekMessageW.restype = ctypes.c_int
        self._user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self._user32.MapVirtualKeyW.restype = wintypes.UINT
        self._init_input_structs()
        self.thread = threading.Thread(target=self._message_loop, daemon=True)
        self.thread.start()

    def _init_input_structs(self) -> None:
        ctypes = self._ctypes
        wintypes = self._wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                        ("wParamH", wintypes.WORD)]

        # INPUT's union must be as large as MOUSEINPUT even when we only send keys;
        # otherwise cbSize is wrong and Windows rejects every SendInput call.
        class INPUT_UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]

        expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        if ctypes.sizeof(INPUT) != expected_size:
            raise RuntimeError(f"Неверный размер Windows INPUT: {ctypes.sizeof(INPUT)} вместо {expected_size}")
        self._KEYBDINPUT = KEYBDINPUT
        self._INPUT = INPUT
        self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT

    def _send_inputs(self, events: list[tuple[int, int, int]]) -> None:
        inputs = (self._INPUT * len(events))(*[
            self._INPUT(type=self.INPUT_KEYBOARD,
                        ki=self._KEYBDINPUT(virtual_key, scan_code, flags, 0, 0))
            for virtual_key, scan_code, flags in events
        ])
        sent = self._user32.SendInput(len(events), inputs, self._ctypes.sizeof(self._INPUT))
        if sent != len(events):
            raise RuntimeError(f"Windows приняла только {sent} из {len(events)} событий клавиатуры")

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
                test_only = bool(msg.lParam)
                if test_only:
                    shortcuts = [(10 + index, self.MOD_CONTROL, key)
                                 for index, (_label, key) in enumerate(TEST_HOTKEYS)]
                    ids = range(10, 10 + len(TEST_HOTKEYS))
                else:
                    keys = [ord("1"), ord("2"), ord("3"), ord("4"), ord("5"), ord("6"), ord("8"), ord("9"), ord("0")]
                    shortcuts = [(index + 1, self.MOD_ALT | self.MOD_CONTROL, key)
                                 for index, key in enumerate(keys)]
                    ids = range(1, len(HOTKEYS) + 1)

                failed_ids: list[int] = []
                if requested:
                    scope_registered = set()
                    for hotkey_id, modifiers, key in shortcuts:
                        if self._user32.RegisterHotKey(None, hotkey_id, modifiers, key):
                            scope_registered.add(hotkey_id)
                        else:
                            failed_ids.append(hotkey_id)
                    self.registered_ids.update(scope_registered)
                    scope_enabled = bool(scope_registered)
                else:
                    for hotkey_id in ids:
                        self._user32.UnregisterHotKey(None, hotkey_id)
                        self.registered_ids.discard(hotkey_id)
                    scope_enabled = True

                if test_only:
                    self.test_enabled = scope_enabled if requested else False
                else:
                    self.main_enabled = scope_enabled if requested else False
                self.toggle_results.put((scope_enabled, failed_ids))
            elif msg.message == self.WM_HOTKEY:
                hotkey_id = int(msg.wParam)
                if (1 <= hotkey_id <= len(HOTKEYS) and self.main_enabled
                        and hotkey_id in self.registered_ids):
                    self.run_action(hotkey_id - 1)
                elif (10 <= hotkey_id < 10 + len(TEST_HOTKEYS) and self.test_enabled
                      and hotkey_id in self.registered_ids):
                    self.run_test(hotkey_id - 10)

    def set_enabled(self, enabled: bool, test_only: bool = False) -> tuple[bool, list[int]]:
        if not WINDOWS or self.thread is None or not self.thread_ready.wait(2):
            return False, []
        self._user32.PostThreadMessageW(self.thread_id, self.WM_APP_TOGGLE,
                                        int(enabled), int(test_only))
        try:
            return self.toggle_results.get(timeout=2)
        except queue.Empty:
            return False, []

    def stop(self) -> None:
        if self.main_enabled:
            self.set_enabled(False, test_only=False)
        if self.test_enabled:
            self.set_enabled(False, test_only=True)
        if self.thread_id:
            self._user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)

    def _scan_key(self, virtual_key: int, up: bool = False) -> None:
        # Games may read scan codes rather than WM_CHAR / virtual-key messages.
        scan_code = self._user32.MapVirtualKeyW(virtual_key, 0)
        if not scan_code:
            raise RuntimeError(f"Не удалось получить scan code для клавиши {virtual_key}")
        flags = self.KEYEVENTF_SCANCODE | (self.KEYEVENTF_KEYUP if up else 0)
        self._send_inputs([(0, scan_code, flags)])

    def _set_clipboard_text(self, text: str) -> None:
        payload = text.encode("utf-16-le") + b"\x00\x00"
        opened = False
        for _ in range(30):
            if self._user32.OpenClipboard(self.owner_hwnd):
                opened = True
                break
            time.sleep(0.01)
        if not opened:
            raise RuntimeError("Не удалось открыть буфер обмена Windows")

        handle = None
        try:
            if not self._user32.EmptyClipboard():
                raise RuntimeError("Не удалось очистить буфер обмена")
            handle = self._kernel32.GlobalAlloc(self.GMEM_MOVEABLE, len(payload))
            if not handle:
                raise RuntimeError("Не удалось выделить память для буфера обмена")
            pointer = self._kernel32.GlobalLock(handle)
            if not pointer:
                raise RuntimeError("Не удалось заблокировать память буфера обмена")
            self._ctypes.memmove(pointer, payload, len(payload))
            self._kernel32.GlobalUnlock(handle)
            if not self._user32.SetClipboardData(self.CF_UNICODETEXT, handle):
                raise RuntimeError("Windows не приняла текст в буфер обмена")
            handle = None  # Ownership passes to the Windows clipboard.
        finally:
            if handle:
                self._kernel32.GlobalFree(handle)
            self._user32.CloseClipboard()

    def paste_text(self, text: str, press_enter: bool = False) -> None:
        if not text:
            return
        self._set_clipboard_text(text)
        # Use the same Ctrl+V path that the user confirmed works in the MTA console.
        self._scan_key(self.VK_CONTROL)
        try:
            time.sleep(0.04)
            self._scan_key(self.VK_V)
            time.sleep(0.04)
            self._scan_key(self.VK_V, up=True)
            time.sleep(0.04)
        finally:
            self._scan_key(self.VK_CONTROL, up=True)
        time.sleep(0.12)
        if press_enter:
            self._scan_key(self.VK_RETURN)
            self._scan_key(self.VK_RETURN, up=True)

    def type_command(self, command: str, delay: float) -> None:
        self.paste_text(command, press_enter=True)
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
        self.test_entries: list[tk.Entry] = []
        self.status = tk.StringVar(value="Горячие клавиши выключены")
        self.hotkeys_enabled = tk.BooleanVar(value=False)
        self.test_hotkeys_enabled = tk.BooleanVar(value=False)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.send_lock = threading.Lock()
        self.hotkeys = WindowsHotkeys(self._queue_status, self._queue_hotkey, self._queue_test_hotkey)
        self._build_ui()
        self.hotkeys.start(root.winfo_id())
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
        test_tab = tk.Frame(notebook, bg=self.BG)
        notebook.add(commands_tab, text="Команды")
        notebook.add(gmp_tab, text="ГМП")
        notebook.add(test_tab, text="Тест")
        self._build_commands(commands_tab)
        self._build_gmp(gmp_tab)
        self._build_test(test_tab)

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
            tk.Checkbutton(bottom, text="Включить горячие клавиши действий (Alt+Ctrl)",
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
        self._field(stats, "sleep", "sleep (мс):", 2, width=12, label_width=11)

        buttons = [
            ("[ГМП]Сохранить", self.save_preset),
            ("[ГМП]Очистить", self.clear_fields),
            ("[ГМП]Обновить", self.load_preset),
        ]
        for index, (text, callback) in enumerate(buttons):
            tk.Button(controls, text=text, command=callback, height=2, width=18,
                      font=("Arial", 12)).grid(row=index + 2, column=0, columnspan=2,
                                                sticky="ew", pady=(7, 4))

    def _build_test(self, parent: tk.Frame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        tk.Label(parent, text="Тест ввода горячих клавиш", bg=self.BG,
                 font=("Arial", 16, "bold")).grid(row=0, column=0, pady=(42, 12))
        panel = tk.Frame(parent, bg=self.BG)
        panel.grid(row=1, column=0, sticky="n", padx=40, pady=12)
        panel.grid_columnconfigure(1, weight=1)
        for index, (hotkey, _vk) in enumerate(TEST_HOTKEYS):
            tk.Label(panel, text=f"Текст {index + 1}:", bg=self.BG,
                     font=("Arial", 12), width=12, anchor="w").grid(
                         row=index, column=0, sticky="w", padx=(0, 10), pady=8)
            entry = tk.Entry(panel, width=58, font=("Arial", 12), relief="sunken", bd=1)
            entry.grid(row=index, column=1, sticky="ew", ipady=4, pady=8)
            tk.Label(panel, text=hotkey, bg=self.BG, font=("Arial", 12, "bold"),
                     width=9).grid(row=index, column=2, padx=(15, 0), pady=8)
            self.test_entries.append(entry)
        tk.Label(parent,
                 text="Включите тестовые клавиши ниже. Откройте консоль игры клавишей F8 вручную. Ctrl+1, Ctrl+2 или Ctrl+3 введёт соответствующий текст и нажмёт Enter.",
                 bg=self.BG, font=("Arial", 10), wraplength=900, justify="center").grid(
                     row=2, column=0, padx=20, pady=18, sticky="ew")
        tk.Checkbutton(parent, text="Включить только тестовые клавиши Ctrl+1…3",
                       variable=self.test_hotkeys_enabled, command=self._toggle_test_hotkeys,
                       bg=self.BG, activebackground=self.BG, font=("Arial", 11)).grid(
                           row=3, column=0, pady=(4, 16))

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
        ok, failed_ids = self.hotkeys.set_enabled(enabled, test_only=False)
        self._report_hotkey_result(ok, failed_ids, enabled, self.hotkeys_enabled,
                                   "основные клавиши", test_only=False)

    def _toggle_test_hotkeys(self) -> None:
        enabled = self.test_hotkeys_enabled.get()
        ok, failed_ids = self.hotkeys.set_enabled(enabled, test_only=True)
        self._report_hotkey_result(ok, failed_ids, enabled, self.test_hotkeys_enabled,
                                   "тестовые клавиши", test_only=True)

    def _report_hotkey_result(self, ok: bool, failed_ids: list[int], enabled: bool,
                              variable: tk.BooleanVar, label: str, test_only: bool) -> None:
        if ok:
            if enabled and failed_ids:
                if test_only:
                    failed_keys = [f"Ctrl+{i - 9}" for i in failed_ids]
                else:
                    failed_keys = [HOTKEYS[i - 1][1] for i in failed_ids]
                self.status.set(f"{label} включены частично")
                messagebox.showwarning("Горячие клавиши", "Не зарегистрировались: " + ", ".join(failed_keys))
            else:
                self.status.set(f"{label.capitalize()} включены" if enabled else f"{label.capitalize()} выключены")
        else:
            variable.set(False)
            self.status.set(f"Не удалось включить {label}")
            messagebox.showwarning("Горячие клавиши", "Не удалось зарегистрировать сочетания. Возможно, их заняла другая программа.")

    def _queue_status(self, text: str) -> None:
        self.events.put(("status", text))

    def _queue_hotkey(self, index: int) -> None:
        # Read Tk widgets only on the UI thread; the Win32 message loop stays separate.
        self.events.put(("hotkey", index))

    def _queue_test_hotkey(self, index: int) -> None:
        self.events.put(("test", index))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(str(value))
                elif kind == "hotkey":
                    self._hotkey_action(int(value))
                elif kind == "test":
                    self._test_hotkey_action(int(value))
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

    def _test_hotkey_action(self, index: int) -> None:
        if not 0 <= index < len(self.test_entries):
            return
        text = self.test_entries[index].get()
        if not text:
            self.status.set("Тестовое поле пустое — ничего не вставлено")
            return
        if not self.send_lock.acquire(blocking=False):
            self.status.set("Предыдущая отправка ещё выполняется")
            return
        self.status.set(f"Тест: ввожу текст {index + 1} и отправляю Enter")
        threading.Thread(target=self._send_test_text, args=(text,), daemon=True).start()

    def _send_test_text(self, text: str) -> None:
        try:
            self.hotkeys.paste_text(text, press_enter=True)
            self._queue_status("Тест: текст введён, Enter отправлен")
        except Exception as exc:
            self._queue_status(f"Ошибка ввода: {exc}. Проверьте запуск приложения и игры с одинаковыми правами.")
        finally:
            self.send_lock.release()

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
            for command in commands:
                self.hotkeys.type_command(command, delay=delay)
            self._queue_status(f"Готово: отправлено {len(commands)} команд(ы)")
        except Exception as exc:
            self._queue_status(f"Ошибка ввода: {exc}. Проверьте запуск приложения и игры с одинаковыми правами.")
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
