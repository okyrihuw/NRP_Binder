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
    """Global shortcuts plus passive detection of the user's physical Ctrl+V."""

    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    WM_APP_TOGGLE = 0x8001
    WM_APP_PASTE_MONITOR = 0x8002
    WH_KEYBOARD_LL = 13
    HC_ACTION = 0
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    LLKHF_LOWER_IL_INJECTED = 0x02
    LLKHF_INJECTED = 0x10
    VK_CONTROL = 0x11
    VK_V = 0x56
    SCANCODE_V = 0x2F
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    def __init__(self, notify: Callable[[str], None], run_action: Callable[[int], None],
                 run_test: Callable[[int], None], run_paste: Callable[[], None]):
        self.notify = notify
        self.run_action = run_action
        self.run_test = run_test
        self.run_paste = run_paste
        self.thread: threading.Thread | None = None
        self.thread_id = None
        self.thread_ready = threading.Event()
        self.toggle_results: queue.Queue[tuple[bool, list[int]]] = queue.Queue()
        self.monitor_results: queue.Queue[bool] = queue.Queue()
        self.main_enabled = False
        self.test_enabled = False
        self.registered_ids: set[int] = set()
        self._user32 = None
        self.paste_hook = None
        self.paste_target_hwnd = 0
        self._paste_v_down = False

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
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short
        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        self._KBDLLHOOKSTRUCT = KBDLLHOOKSTRUCT
        self._HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                            wintypes.WPARAM, wintypes.LPARAM)
        self._user32.SetWindowsHookExW.argtypes = [ctypes.c_int, self._HOOKPROC,
                                                   wintypes.HINSTANCE, wintypes.DWORD]
        self._user32.SetWindowsHookExW.restype = ctypes.c_void_p
        self._user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        self._user32.UnhookWindowsHookEx.restype = ctypes.c_int
        self._user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                wintypes.WPARAM, wintypes.LPARAM]
        self._user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self._module_handle = self._kernel32.GetModuleHandleW(None)
        self._hook_callback = self._HOOKPROC(self._low_level_keyboard_hook)
        self.thread = threading.Thread(target=self._message_loop, daemon=True)
        self.thread.start()

    @staticmethod
    def _handle_value(handle) -> int:
        return int(handle or 0)

    def foreground_hwnd(self) -> int:
        if not WINDOWS or self._user32 is None:
            return 0
        return self._handle_value(self._user32.GetForegroundWindow())

    def _low_level_keyboard_hook(self, code, wparam, lparam):
        if code == self.HC_ACTION and self.paste_hook:
            try:
                event = self._ctypes.cast(
                    lparam, self._ctypes.POINTER(self._KBDLLHOOKSTRUCT)
                ).contents
                message = int(wparam)
                is_v_key = event.vkCode == self.VK_V or event.scanCode == self.SCANCODE_V
                if is_v_key:
                    if message in (self.WM_KEYUP, self.WM_SYSKEYUP):
                        self._paste_v_down = False
                    elif message in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN) and not self._paste_v_down:
                        self._paste_v_down = True
                        injected = event.flags & (self.LLKHF_INJECTED | self.LLKHF_LOWER_IL_INJECTED)
                        ctrl_down = self._user32.GetAsyncKeyState(self.VK_CONTROL) & 0x8000
                        target_active = self.foreground_hwnd() == self.paste_target_hwnd
                        if not injected and ctrl_down and target_active:
                            # Observe the physical paste only; never swallow the key event.
                            self.run_paste()
            except Exception:
                pass
        return self._user32.CallNextHookEx(self.paste_hook, code, wparam, lparam)

    def _message_loop(self) -> None:
        ctypes = self._ctypes
        wintypes = self._wintypes
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
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
            elif msg.message == self.WM_APP_PASTE_MONITOR:
                enabled = bool(msg.wParam)
                success = True
                if enabled:
                    self.paste_target_hwnd = self._handle_value(msg.lParam)
                    self._paste_v_down = False
                    if not self.paste_hook:
                        self.paste_hook = self._user32.SetWindowsHookExW(
                            self.WH_KEYBOARD_LL, self._hook_callback, self._module_handle, 0
                        )
                    success = bool(self.paste_hook)
                else:
                    if self.paste_hook:
                        self._user32.UnhookWindowsHookEx(self.paste_hook)
                        self.paste_hook = None
                    self.paste_target_hwnd = 0
                    self._paste_v_down = False
                self.monitor_results.put(success)
            elif msg.message == self.WM_HOTKEY:
                hotkey_id = int(msg.wParam)
                if (1 <= hotkey_id <= len(HOTKEYS) and self.main_enabled
                        and hotkey_id in self.registered_ids):
                    self.run_action(hotkey_id - 1)
                elif (10 <= hotkey_id < 10 + len(TEST_HOTKEYS) and self.test_enabled
                      and hotkey_id in self.registered_ids):
                    self.run_test(hotkey_id - 10)
        if self.paste_hook:
            self._user32.UnhookWindowsHookEx(self.paste_hook)
            self.paste_hook = None

    def set_enabled(self, enabled: bool, test_only: bool = False) -> tuple[bool, list[int]]:
        if not WINDOWS or self.thread is None or not self.thread_ready.wait(2):
            return False, []
        self._user32.PostThreadMessageW(self.thread_id, self.WM_APP_TOGGLE,
                                        int(enabled), int(test_only))
        try:
            return self.toggle_results.get(timeout=2)
        except queue.Empty:
            return False, []

    def monitor_paste(self, enabled: bool, target_hwnd: int = 0) -> bool:
        if not WINDOWS or self.thread is None or not self.thread_ready.wait(2):
            return False
        self._user32.PostThreadMessageW(self.thread_id, self.WM_APP_PASTE_MONITOR,
                                        int(enabled), target_hwnd)
        try:
            return self.monitor_results.get(timeout=2)
        except queue.Empty:
            return False

    def stop(self) -> None:
        if self.main_enabled:
            self.set_enabled(False, test_only=False)
        if self.test_enabled:
            self.set_enabled(False, test_only=True)
        if self.paste_hook:
            self.monitor_paste(False)
        if self.thread_id:
            self._user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)

    def copy_to_clipboard(self, text: str) -> None:
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
            handle = None
        finally:
            if handle:
                self._kernel32.GlobalFree(handle)
            self._user32.CloseClipboard()

    def clear_clipboard(self) -> None:
        for _ in range(30):
            if self._user32.OpenClipboard(self.owner_hwnd):
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("Не удалось открыть буфер обмена")
        try:
            if not self._user32.EmptyClipboard():
                raise RuntimeError("Не удалось очистить буфер обмена")
        finally:
            self._user32.CloseClipboard()


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
        self.pending_commands: list[str] = []
        self.pending_index = 0
        self.pending_generation = 0
        self.pending_advance_scheduled = False
        self.pending_delay = 0.05
        self.pending_target_hwnd = 0
        self.hotkeys = WindowsHotkeys(self._queue_status, self._queue_hotkey,
                                      self._queue_test_hotkey, self._queue_paste_event)
        self._build_ui()
        self.hotkeys.start(root.winfo_id())
        self.root.after(25, self._drain_events)
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
                 text="Включите тестовые клавиши ниже. Ctrl+1, Ctrl+2 или Ctrl+3 скопирует соответствующий текст. В игре вручную нажмите Ctrl+V и Enter.",
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

    def _queue_paste_event(self) -> None:
        self.events.put(("physical_paste", None))

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
                elif kind == "physical_paste":
                    self._on_physical_paste()
        except queue.Empty:
            pass
        self.root.after(25, self._drain_events)

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
        if self.pending_commands:
            self.status.set("Сначала завершите текущую очередь команд")
            return
        text = self.test_entries[index].get()
        if not text:
            self.status.set("Тестовое поле пустое — ничего не скопировано")
            return
        try:
            self.hotkeys.copy_to_clipboard(text)
            self.status.set("Тестовый текст скопирован. В игре нажмите Ctrl+V и Enter.")
        except Exception as exc:
            self.status.set(f"Ошибка буфера обмена: {exc}")

    def _hotkey_action(self, index: int) -> None:
        commands = self._commands_for(index)
        if not commands:
            self.status.set("Нет заполненных данных — очередь не создана")
            return

        target_hwnd = self.hotkeys.foreground_hwnd()
        if not target_hwnd or target_hwnd == self.root.winfo_id():
            self.status.set("Сначала активируйте окно игры и откройте F8")
            return

        self.pending_generation += 1
        generation = self.pending_generation
        self.pending_commands = commands
        self.pending_index = 0
        self.pending_advance_scheduled = False
        # After Ctrl+V, wait at least 50 ms so the target has consumed the current clipboard.
        self.pending_delay = max(0.05, self._delay_seconds())
        self.pending_target_hwnd = target_hwnd

        if not self.hotkeys.monitor_paste(True, target_hwnd):
            self._reset_pending_queue()
            self.status.set("Не удалось отслеживать физический Ctrl+V")
            return
        try:
            self.hotkeys.copy_to_clipboard(commands[0])
        except Exception as exc:
            self._reset_pending_queue()
            self.status.set(f"Не удалось скопировать команду: {exc}")
            return

        self.status.set(
            f"Команда 1/{len(commands)} в буфере: Ctrl+V, затем Enter. "
            "После Ctrl+V следующая команда подготовится автоматически."
        )

    def _on_physical_paste(self) -> None:
        if not self.pending_commands or self.pending_advance_scheduled:
            return
        self.pending_advance_scheduled = True
        generation = self.pending_generation
        delay_ms = max(50, int(self.pending_delay * 1000))
        self.status.set("Ctrl+V принят; подготавливаю следующую команду…")
        self.root.after(delay_ms, lambda: self._advance_command_queue(generation))

    def _advance_command_queue(self, generation: int) -> None:
        if generation != self.pending_generation or not self.pending_commands:
            return
        next_index = self.pending_index + 1
        if next_index >= len(self.pending_commands):
            total = len(self.pending_commands)
            try:
                self.hotkeys.clear_clipboard()
            except Exception as exc:
                self.status.set(f"Очередь закончилась, но буфер очистить не удалось: {exc}")
            self._reset_pending_queue()
            self.status.set(f"Готово: последняя команда вставлена ({total}/{total}). Нажмите Enter.")
            return

        command = self.pending_commands[next_index]
        try:
            self.hotkeys.copy_to_clipboard(command)
        except Exception as exc:
            self._reset_pending_queue()
            self.status.set(f"Не удалось подготовить следующую команду: {exc}")
            return

        self.pending_index = next_index
        self.pending_advance_scheduled = False
        self.status.set(
            f"Команда {next_index + 1}/{len(self.pending_commands)} в буфере. "
            "Нажмите Enter для предыдущей, затем Ctrl+V."
        )

    def _reset_pending_queue(self) -> None:
        self.pending_generation += 1
        self.pending_commands = []
        self.pending_index = 0
        self.pending_advance_scheduled = False
        self.pending_target_hwnd = 0
        self.hotkeys.monitor_paste(False)

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
