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
    ("Выдать скины по командам", "Alt+Ctrl+2", "skins"),
    ("Выдать оружие", "Alt+Ctrl+3", "weapons"),
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
    VK_LCONTROL = 0xA2
    VK_RCONTROL = 0xA3
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
        self._physical_ctrl_down = False

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
                is_down = message in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN)
                is_up = message in (self.WM_KEYUP, self.WM_SYSKEYUP)
                injected = event.flags & (self.LLKHF_INJECTED | self.LLKHF_LOWER_IL_INJECTED)
                if event.vkCode in (self.VK_CONTROL, self.VK_LCONTROL, self.VK_RCONTROL):
                    if is_down and not injected:
                        self._physical_ctrl_down = True
                    elif is_up:
                        self._physical_ctrl_down = False

                is_v_key = event.vkCode == self.VK_V or event.scanCode == self.SCANCODE_V
                if is_v_key:
                    if is_up:
                        self._paste_v_down = False
                    elif is_down and not self._paste_v_down:
                        self._paste_v_down = True
                        target_active = self.foreground_hwnd() == self.paste_target_hwnd
                        if not injected and self._physical_ctrl_down and target_active:
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
                    shortcuts = [
                        (index + 1, self.MOD_ALT | self.MOD_CONTROL, ord(key.split("+")[-1]))
                        for index, (_label, key, _action) in enumerate(HOTKEYS)
                    ]
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
                    self._physical_ctrl_down = False
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
                    self._physical_ctrl_down = False
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
        style.configure("TNotebook.Tab", padding=(9, 4), font=("Arial", 9))
        style.configure("TButton", font=("Arial", 10), padding=5)
        style.configure("Treeview", font=("Arial", 10), rowheight=25)
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))

        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=2, pady=(2, 0))

        info_tab = tk.Frame(notebook, bg=self.BG)
        gmp_tab = tk.Frame(notebook, bg=self.BG)
        commands_tab = tk.Frame(notebook, bg=self.BG)
        test_tab = tk.Frame(notebook, bg=self.BG)
        notebook.add(info_tab, text="ИНФО")
        notebook.add(gmp_tab, text="ГМП")
        notebook.add(commands_tab, text="Команды")
        notebook.add(test_tab, text="Тест")
        self._build_info(info_tab)
        self._build_gmp(gmp_tab)
        self._build_commands(commands_tab)
        self._build_test(test_tab)

        footer = tk.Frame(self.root, bg=self.BG, height=58, highlightbackground=self.BORDER,
                          highlightthickness=1)
        footer.grid(row=1, column=0, sticky="ew")
        footer.grid_propagate(False)
        tk.Label(footer, text="by Пепси Кольный", bg=self.BG, fg=self.RED,
                 font=("Arial", 29, "bold")).pack(expand=True)

        bottom = tk.Frame(self.root, bg=self.BG, height=23)
        bottom.grid(row=2, column=0, sticky="ew", padx=3, pady=(1, 2))
        bottom.grid_propagate(False)
        tk.Label(bottom, text="ДС: okyrihuw..", bg=self.BG, fg="black",
                 font=("Arial", 9, "bold")).pack(side="left")
        tk.Label(bottom, textvariable=self.status, bg=self.BG, fg="#333333",
                 font=("Arial", 9)).pack(side="right", padx=8)

    def _build_info(self, parent: tk.Frame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        card = tk.Frame(parent, bg=self.BG)
        card.grid(row=0, column=0, sticky="", padx=30, pady=30)
        tk.Label(card, text="Выдача", bg=self.BG, fg=self.TEXT,
                 font=("Arial", 26, "bold")).pack(pady=(8, 14))
        tk.Label(card, text="Помощник для подготовки и последовательной отправки игровых команд.",
                 bg=self.BG, font=("Arial", 13), wraplength=700, justify="center").pack(pady=6)
        tk.Label(card, text="Автор: Пепси Кольный", bg=self.BG,
                 font=("Arial", 12, "bold")).pack(pady=(20, 4))
        tk.Label(card, text="Discord: okyrihuw..", bg=self.BG,
                 font=("Arial", 11)).pack(pady=4)
        tk.Label(card, text="Горячие клавиши и очередь команд настраиваются во вкладке «ГМП».",
                 bg=self.BG, font=("Arial", 10), wraplength=700, justify="center").pack(pady=(18, 8))

    def _build_commands(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        tk.Label(parent, text="Список доступных команд", bg=self.BG,
                 font=("Arial", 15, "bold")).grid(row=0, column=0, pady=(15, 10))
        table_frame = tk.Frame(parent, bg=self.BG)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(table_frame, columns=("command", "description"), show="headings")
        tree.heading("command", text="Команда")
        tree.heading("description", text="Описание")
        tree.column("command", width=470, minwidth=300, stretch=True)
        tree.column("description", width=650, minwidth=300, stretch=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        for command, description in COMMANDS:
            tree.insert("", "end", values=(command, description))

    def _build_gmp(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        content = tk.Frame(parent, bg=self.BG)
        content.grid(row=0, column=0, sticky="nsew")
        for col in range(4):
            content.grid_columnconfigure(col, weight=1, uniform="groups")
        content.grid_columnconfigure(4, weight=0)
        content.grid_columnconfigure(5, weight=0)

        for group in range(4):
            frame = tk.Frame(content, bg=self.BG, highlightbackground="#c5c5c5",
                             highlightthickness=1, padx=5, pady=4)
            frame.grid(row=0, column=group, sticky="nw", padx=(3, 2), pady=5)
            frame.grid_columnconfigure(1, weight=1)
            tk.Label(frame, text=f"Группа {group + 1}", bg="#e5e5e5", fg=self.TEXT,
                     font=("Arial", 10, "bold"), anchor="center").grid(
                         row=0, column=0, columnspan=2, sticky="ew", pady=(0, 3), ipady=3)
            for i in range(15):
                self._field(frame, f"id_{group}_{i}", f"ID-{i+1}:", i + 1,
                            width=9, label_width=6, compact=True)
            tk.Label(frame, text="Скины: первая / вторая половина", bg=self.BG,
                     font=("Arial", 8, "bold"), anchor="w").grid(
                         row=16, column=0, columnspan=2, sticky="ew", pady=(4, 1))
            self._field(frame, f"skin_{group}_0", "Скин 1:", 17,
                        width=9, label_width=6, compact=True)
            self._field(frame, f"skin_{group}_1", "Скин 2:", 18,
                        width=9, label_width=6, compact=True)
            tk.Label(frame, text="Оружие: порядок выдачи каждому ID", bg=self.BG,
                     font=("Arial", 8, "bold"), anchor="w").grid(
                         row=19, column=0, columnspan=2, sticky="ew", pady=(4, 1))
            for slot in range(3):
                self._field(frame, f"weapon_{group}_{slot}", f"Оружие {slot+1}:", 20 + slot,
                            width=9, label_width=9, compact=True)

        separator = tk.Frame(content, bg="#153c86", width=2)
        separator.grid(row=0, column=4, sticky="ns", padx=(4, 3), pady=5)
        controls = tk.Frame(content, bg=self.BG, width=270)
        controls.grid(row=0, column=5, sticky="nsew", padx=(8, 8), pady=5)
        controls.grid_propagate(False)
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=0)
        tk.Label(controls, text="Параметры и бинды", bg=self.BG,
                 font=("Arial", 12, "bold")).grid(row=0, column=0, columnspan=2,
                                                   sticky="w", pady=(0, 5))
        stats = tk.Frame(controls, bg=self.BG)
        stats.grid(row=1, column=0, columnspan=2, sticky="ew")
        stats.grid_columnconfigure(1, weight=1)
        self._field(stats, "armor", "Броня:", 0, width=10, label_width=7, compact=True)
        self._field(stats, "health", "ХР:", 1, width=10, label_width=7, compact=True)

        if WINDOWS:
            tk.Checkbutton(controls, text="Включить горячие клавиши",
                           variable=self.hotkeys_enabled, command=self._toggle_hotkeys,
                           bg=self.BG, activebackground=self.BG,
                           font=("Arial", 9, "bold")).grid(row=2, column=0, columnspan=2,
                                                            sticky="w", pady=(5, 4))
        else:
            tk.Label(controls, text="Горячие клавиши доступны в Windows-сборке.",
                     bg=self.BG, font=("Arial", 9)).grid(row=2, column=0, columnspan=2,
                                                          sticky="w", pady=(5, 4))
        tk.Label(controls, text="Действие / активация", bg=self.BG,
                 font=("Arial", 9, "bold")).grid(row=3, column=0, columnspan=2,
                                                  sticky="w", pady=(3, 1))
        for row, (label, key, _action) in enumerate(HOTKEYS, start=4):
            tk.Label(controls, text=label, bg=self.BG, font=("Arial", 8),
                     anchor="w").grid(row=row, column=0, sticky="w", pady=1)
            tk.Label(controls, text=key, bg=self.BG, font=("Arial", 8, "bold"),
                     anchor="e").grid(row=row, column=1, sticky="e", padx=(4, 0), pady=1)

        button_row = 4 + len(HOTKEYS)
        buttons = [
            ("Сохранить", self.save_preset),
            ("Очистить поля", self.clear_fields),
            ("Обновить из пресета", self.load_preset),
        ]
        for index, (text, callback) in enumerate(buttons):
            tk.Button(controls, text=text, command=callback, height=1,
                      font=("Arial", 9)).grid(row=button_row + index, column=0, columnspan=2,
                                              sticky="ew", pady=2)
        tk.Label(controls,
                 text="В очереди: физический Ctrl+V → Enter. Следующая команда готовится сама.",
                 bg=self.BG, font=("Arial", 8), wraplength=255, justify="left").grid(
                     row=button_row + len(buttons), column=0, columnspan=2,
                     sticky="ew", pady=(5, 0))

    def _build_test(self, parent: tk.Frame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        tk.Label(parent, text="Тест буфера обмена", bg=self.BG,
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
                 text="Включите тестовые клавиши на этой вкладке. Ctrl+1/2/3 копируют соответствующий текст; вставьте его вручную.",
                 bg=self.BG, font=("Arial", 10), wraplength=900, justify="center").grid(
                     row=2, column=0, padx=20, pady=18, sticky="ew")
        tk.Checkbutton(parent, text="Включить только тестовые клавиши Ctrl+1…3",
                       variable=self.test_hotkeys_enabled, command=self._toggle_test_hotkeys,
                       bg=self.BG, activebackground=self.BG, font=("Arial", 11)).grid(
                           row=3, column=0, pady=(4, 16))

    def _field(self, parent: tk.Widget, key: str, label: str, row: int, width: int = 10,
               label_width: int = 8, compact: bool = False) -> None:
        font_size = 9 if compact else 10
        padding_y = 0 if compact else 1
        inner_padding = 1 if compact else 2
        tk.Label(parent, text=label, bg=self.BG, fg=self.TEXT,
                 font=("Arial", font_size), width=label_width, anchor="w").grid(
                     row=row, column=0, sticky="w", pady=padding_y)
        entry = tk.Entry(parent, width=width, font=("Arial", font_size), relief="sunken", bd=1)
        entry.grid(row=row, column=1, sticky="ew", pady=padding_y, ipady=inner_padding)
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
                group_ids = [pid for pid in group_ids if self._valid_token(pid)]
                skins = [self.fields[f"skin_{group}_{i}"].get().strip() for i in range(2)]
                skins = [skin for skin in skins if self._valid_token(skin)]
                if not group_ids or not skins:
                    continue
                if len(skins) == 1:
                    assignments = [skins[0]] * len(group_ids)
                else:
                    first_team_size = (len(group_ids) + 1) // 2
                    assignments = ([skins[0]] * first_team_size
                                   + [skins[1]] * (len(group_ids) - first_team_size))
                result.extend(f"/tempskin {pid} {skin}"
                              for pid, skin in zip(group_ids, assignments))
            return result
        if action == "weapons":
            result = []
            for group in range(4):
                group_ids = [self.fields[f"id_{group}_{i}"].get().strip() for i in range(15)]
                group_ids = [pid for pid in group_ids if self._valid_token(pid)]
                weapons = [self.fields[f"weapon_{group}_{slot}"].get().strip() for slot in range(3)]
                weapons = [weapon for weapon in weapons if self._valid_token(weapon)]
                for pid in group_ids:
                    for weapon in weapons:
                        # The server command requires an ammunition count; use 1000 per weapon.
                        result.append(f"/weapongive {pid} {weapon} 1000")
            return result
        if action in ("health", "armor"):
            value = self.fields[action].get().strip()
            if not self._valid_token(value):
                return []
            verb = "/sethp" if action == "health" else "/setarmor"
            return [f"{verb} {pid} {value}" for pid in ids if self._valid_token(pid)]
        return []

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
        # Let the game consume the current clipboard before preparing the next line.
        self.pending_delay = 0.05
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

    def close(self) -> None:
        self.hotkeys.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
