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

APP_NAME = "NRP Binder - by okyrihuw"
WINDOWS = sys.platform == "win32"

# The settings live outside the executable, so updates/rebuilds do not erase presets.
if os.name == "nt":
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BindiVidacha"
else:
    CONFIG_DIR = Path.home() / ".config" / "bindi_vidacha"
CONFIG_FILE = CONFIG_DIR / "preset.json"
THEME_FILE = CONFIG_DIR / "theme.json"

HOTKEYS = [
    ("ТП игроков к себе", "Ctrl+1", "teleport"),
    ("Выдать скины по командам", "Ctrl+2", "skins"),
    ("Выдать оружие", "Ctrl+3", "weapons"),
    ("Выдать здоровье", "Ctrl+4", "health"),
    ("Выдать броню", "Ctrl+5", "armor"),
    ("Возрождения", "Ctrl+6", "revive"),
]

FORUM_HOTKEYS = [
    ("Выдача команд Forum", "Alt+Ctrl+1", "forum"),
]
ALL_HOTKEYS = HOTKEYS + FORUM_HOTKEYS

# Shared registry is used to prevent collisions between enabled tabs.
HOTKEY_CATEGORY_BINDINGS = {
    "МП": tuple(item[1] for item in HOTKEYS),
    "Forum": tuple(item[1] for item in FORUM_HOTKEYS),
}
FORUM_COLUMNS = (("Мут", "pmute"), ("КПЗ", "jail"), ("Бан", "pban"))
FORUM_ROWS = 15


def find_hotkey_conflicts(active_categories: dict[str, bool]) -> dict[str, list[str]]:
    """Return shortcuts claimed by more than one currently enabled category."""
    owners: dict[str, list[str]] = {}
    for category, active in active_categories.items():
        if not active:
            continue
        for shortcut in HOTKEY_CATEGORY_BINDINGS.get(category, ()):
            parts = shortcut.split("+")
            modifiers = set(parts[:-1])
            ordered_modifiers = [name for name in ("Ctrl", "Alt", "Shift", "Win")
                                 if name in modifiers]
            normalized = "+".join([*ordered_modifiers, parts[-1].upper()])
            owners.setdefault(normalized, []).append(category)
    return {shortcut: categories for shortcut, categories in owners.items()
            if len(set(categories)) > 1}

COMMANDS = [
    ("respawn", "Возродиться самому"),
    ("suicide", "Самоубийство"),
    ("admins", "Список администраторов онлайн"),
    ("inv", "Стать невидимым"),
    ("commands", "Список доступных команд"),
    ("reviving <id>", "Возродить игрока"),
    ("chatlist", "Список чатов"),
    ("deleteallmessagefrom <channel_id> <reason>", "Удалить сообщения из чата"),
    ("deleteplayermessage <player_id> <reason>", "Удалить сообщения игрока"),
    ("punmute <id>", "Снять мут"),
    ("fixveh <vid>", "Починить машину"),
    ("flip <vid>", "Поставить машину на колёса"),
    ("get <id>", "Телепортировать игрока к себе"),
    ("pwarp <id>", "Телепортироваться к игроку"),
    ("vget <vid>", "Телепортировать машину к себе"),
    ("setfuel <vid> <топливо>", "Установить топливо машине"),
    ("pfreeze <id>", "Заморозить игрока"),
    ("punfreeze <id>", "Разморозить игрока"),
    ("unjail <id>", "Вытащить игрока из тюрьмы"),
    ("tempnickname <id> <имя> <фамилия>", "Временное имя игрока"),
    ("tempskin <id> <скин>", "Временный скин игрока"),
    ("sethp <id> <число>", "Установить здоровье"),
    ("setarmor <id> <число>", "Установить броню"),
    ("setnickname <id> <имя> <фамилия>", "Сменить ник игрока"),
    ("p <id> <пункт правила> <причина>", "Наказать за нарушение правила"),
    ("weapongive <id> <ID оружия> <патроны>", "Выдать оружие"),
    ("weapontake <id> <ID оружия> <патроны>", "Отобрать оружие"),
    ("takeallweapons <id>", "Отобрать всё оружие"),
    ("toprison <id>", "Перевести в колонию"),
    ("freeprison <id>", "Освободить из колонии"),
    ("adminmode", "Включить/выключить режим администратора"),
]


class WindowsHotkeys:
    """Register action binds, copy clipboard text, and observe physical Ctrl+V."""

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
    CAN_INCLUDE_IN_CLIPBOARD_HISTORY = "CanIncludeInClipboardHistory"
    CAN_UPLOAD_TO_CLOUD_CLIPBOARD = "CanUploadToCloudClipboard"
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040

    def __init__(self, run_action: Callable[[int], None], run_paste: Callable[[], None]):
        self.run_action = run_action
        self.run_paste = run_paste
        self.thread: threading.Thread | None = None
        self.thread_id = None
        self.thread_ready = threading.Event()
        self.toggle_results: queue.Queue[tuple[bool, list[int]]] = queue.Queue()
        self.monitor_results: queue.Queue[bool] = queue.Queue()
        self.enabled = False
        self.registered_ids: set[int] = set()
        self._user32 = None
        self.paste_hook = None
        self.paste_target_hwnd = 0
        self.paste_target_pid = 0
        self._paste_v_down = False
        self._physical_ctrl_down = False
        self.last_history_exclusion_ok = False

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
        self._user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        self._user32.RegisterClipboardFormatW.restype = wintypes.UINT
        self._user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._user32.GetWindowRect.restype = wintypes.BOOL
        self._user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.GetWindowLongW.restype = ctypes.c_long
        self._user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        self._user32.SetWindowLongW.restype = ctypes.c_long
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT
        ]
        self._user32.SetWindowPos.restype = wintypes.BOOL
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
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
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

    def _window_process_id(self, hwnd: int) -> int:
        if not hwnd or not WINDOWS or self._user32 is None:
            return 0
        process_id = self._wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, self._ctypes.byref(process_id))
        return int(process_id.value)

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        if not hwnd or not WINDOWS or self._user32 is None:
            return None
        rect = self._wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, self._ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def configure_nonactivating_overlay(self, hwnd: int) -> bool:
        if not WINDOWS or self._user32 is None or not hwnd:
            return False
        try:
            style = self._user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
            self._user32.SetWindowLongW(
                hwnd, self.GWL_EXSTYLE, style | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE
            )
            return True
        except Exception:
            return False

    def show_topmost_overlay(self, hwnd: int, x: int, y: int, width: int, height: int) -> bool:
        if not WINDOWS or self._user32 is None or not hwnd:
            return False
        try:
            return bool(self._user32.SetWindowPos(
                hwnd, self._ctypes.c_void_p(-1), x, y, width, height,
                self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW
            ))
        except Exception:
            return False

    def _set_clipboard_dword_format(self, name: str, value: int = 0) -> bool:
        fmt = self._user32.RegisterClipboardFormatW(name)
        if not fmt:
            return False
        handle = self._kernel32.GlobalAlloc(self.GMEM_MOVEABLE, self._ctypes.sizeof(self._ctypes.c_uint32))
        if not handle:
            return False
        try:
            pointer = self._kernel32.GlobalLock(handle)
            if not pointer:
                return False
            self._ctypes.cast(pointer, self._ctypes.POINTER(self._ctypes.c_uint32))[0] = value
            self._kernel32.GlobalUnlock(handle)
            if not self._user32.SetClipboardData(fmt, handle):
                return False
            handle = None  # Windows owns the HGLOBAL after SetClipboardData succeeds.
            return True
        finally:
            if handle:
                self._kernel32.GlobalFree(handle)

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
                        # The V event can arrive before our tracked Ctrl state updates on
                        # some keyboard layouts, so also ask Windows for the live state.
                        ctrl_async_down = bool(
                            self._user32.GetAsyncKeyState(self.VK_CONTROL) & 0x8000
                        )
                        control_down = self._physical_ctrl_down or ctrl_async_down
                        foreground = self.foreground_hwnd()
                        target_active = foreground == self.paste_target_hwnd
                        if not target_active and self.paste_target_pid:
                            # F8 may be hosted by another top-level window of the game.
                            target_active = (
                                self._window_process_id(foreground) == self.paste_target_pid
                            )
                        if not injected and control_down and target_active:
                            # Observe physical Ctrl+V without consuming the user's keypress.
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
                requested_mask = int(msg.wParam)
                shortcuts = []
                for index, (_label, shortcut, _action) in enumerate(ALL_HOTKEYS):
                    hotkey_id = index + 1
                    if requested_mask & (1 << (hotkey_id - 1)):
                        modifiers = 0
                        parts = shortcut.split("+")
                        if "Ctrl" in parts:
                            modifiers |= self.MOD_CONTROL
                        if "Alt" in parts:
                            modifiers |= self.MOD_ALT
                        shortcuts.append((hotkey_id, modifiers, ord(parts[-1])))
                for hotkey_id in tuple(self.registered_ids):
                    self._user32.UnregisterHotKey(None, hotkey_id)
                    self.registered_ids.discard(hotkey_id)
                failed_ids: list[int] = []
                scope_registered = set()
                for hotkey_id, modifiers, key in shortcuts:
                    if self._user32.RegisterHotKey(None, hotkey_id, modifiers, key):
                        scope_registered.add(hotkey_id)
                    else:
                        failed_ids.append(hotkey_id)
                self.registered_ids.update(scope_registered)
                self.enabled = bool(scope_registered)
                self.toggle_results.put((self.enabled if shortcuts else True, failed_ids))
            elif msg.message == self.WM_APP_PASTE_MONITOR:
                enabled = bool(msg.wParam)
                success = True
                if enabled:
                    self.paste_target_hwnd = self._handle_value(msg.lParam)
                    self.paste_target_pid = self._window_process_id(self.paste_target_hwnd)
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
                    self.paste_target_pid = 0
                    self._paste_v_down = False
                    self._physical_ctrl_down = False
                self.monitor_results.put(success)
            elif msg.message == self.WM_HOTKEY:
                hotkey_id = int(msg.wParam)
                if self.enabled and 1 <= hotkey_id <= len(ALL_HOTKEYS) and hotkey_id in self.registered_ids:
                    self.run_action(hotkey_id - 1)
        if self.paste_hook:
            self._user32.UnhookWindowsHookEx(self.paste_hook)
            self.paste_hook = None

    def set_active_categories(self, categories: set[str]) -> tuple[bool, list[int]]:
        if not WINDOWS or self.thread is None or not self.thread_ready.wait(2):
            return False, []
        requested_mask = 0
        for index, _binding in enumerate(ALL_HOTKEYS):
            category = "МП" if index < len(HOTKEYS) else "Forum"
            if category in categories:
                requested_mask |= 1 << index
        self._user32.PostThreadMessageW(self.thread_id, self.WM_APP_TOGGLE, requested_mask, 0)
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
        if self.registered_ids:
            self.set_active_categories(set())
        if self.paste_hook:
            self.monitor_paste(False)
        if self.thread_id:
            self._user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)

    def copy_to_clipboard(self, text: str) -> None:
        self.last_history_exclusion_ok = False
        payload = text.encode("utf-16-le") + b"\x00\x00"
        for _ in range(30):
            if self._user32.OpenClipboard(self.owner_hwnd):
                break
            time.sleep(0.01)
        else:
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
            # These registered DWORD formats tell Windows not to retain or sync
            # the command. They must be written before CloseClipboard().
            history_flags_ok = True
            for format_name in (
                self.CAN_INCLUDE_IN_CLIPBOARD_HISTORY,
                self.CAN_UPLOAD_TO_CLOUD_CLIPBOARD,
            ):
                try:
                    if not self._set_clipboard_dword_format(format_name, 0):
                        history_flags_ok = False
                except Exception:
                    history_flags_ok = False
            self.last_history_exclusion_ok = history_flags_ok
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
    BLUE = "#2F6FB3"
    THEMES = {
        "light": {
            "BG": "#f0f0f0", "SURFACE": "#ffffff", "PANEL": "#e5e5e5",
            "PANEL_ALT": "#f4f4f4", "TEXT": "#111111", "SUBTEXT": "#333333",
            "MUTED": "#555555", "BORDER": "#777777", "SOFT_BORDER": "#c5c5c5",
            "BRAND": "#2F6FB3", "INPUT_BG": "#ffffff", "INPUT_FG": "#111111",
            "BUTTON_BG": "#f0f0f0", "BUTTON_ACTIVE": "#e5e5e5", "SELECT_BG": "#b9d3f0",
        },
        "dark": {
            "BG": "#1d2024", "SURFACE": "#292d32", "PANEL": "#353a40",
            "PANEL_ALT": "#262a2e", "TEXT": "#eef1f4", "SUBTEXT": "#c5ccd3",
            "MUTED": "#9ba4ad", "BORDER": "#727b84", "SOFT_BORDER": "#454c53",
            "BRAND": "#65a4e8", "INPUT_BG": "#14171a", "INPUT_FG": "#f1f3f4",
            "BUTTON_BG": "#32373d", "BUTTON_ACTIVE": "#444b52", "SELECT_BG": "#3e5c78",
        },
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.dark_theme = tk.BooleanVar(value=self._load_theme_preference())
        self._set_theme_palette(self.dark_theme.get())
        root.title(APP_NAME)
        root.geometry("1200x700")
        root.minsize(980, 620)
        root.configure(bg=self.BG)
        self.fields: dict[str, tk.Entry] = {}
        self.status = tk.StringVar(value="Горячие клавиши выключены")
        self.mp_hotkeys_enabled = tk.BooleanVar(value=False)
        self.forum_hotkeys_enabled = tk.BooleanVar(value=False)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.pending_commands: list[str] = []
        self.pending_index = 0
        self.pending_generation = 0
        self.pending_advance_scheduled = False
        self.pending_delay = 0.05
        self.pending_target_hwnd = 0
        self.toast_window: tk.Toplevel | None = None
        self.toast_after_id = None
        self.hotkeys = WindowsHotkeys(self._queue_hotkey, self._queue_paste_event)
        self._build_ui()
        self.hotkeys.start(root.winfo_id())
        self.root.after(25, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    @staticmethod
    def _load_theme_preference() -> bool:
        try:
            data = json.loads(THEME_FILE.read_text(encoding="utf-8"))
            return data.get("dark") is True if isinstance(data, dict) else False
        except (OSError, json.JSONDecodeError):
            return False

    def _save_theme_preference(self) -> bool:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            THEME_FILE.write_text(
                json.dumps({"dark": self.dark_theme.get()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except OSError as exc:
            self.status.set(f"Тема изменена, но не сохранена: {exc}")
            return False

    def _set_theme_palette(self, dark: bool) -> None:
        self.palette = self.THEMES["dark" if dark else "light"]
        self.BG = self.palette["BG"]
        self.BORDER = self.palette["BORDER"]
        self.TEXT = self.palette["TEXT"]
        self.BLUE = self.palette["BRAND"]

    def _color_role(self, color: str) -> str | None:
        normalized = str(color).lower()
        legacy = {
            "#f0f0f0": "BG", "#ffffff": "SURFACE", "white": "SURFACE",
            "#e5e5e5": "PANEL", "#f4f4f4": "PANEL_ALT",
            "#111111": "TEXT", "#000000": "TEXT", "black": "TEXT",
            "#333333": "SUBTEXT", "#777777": "BORDER", "#c5c5c5": "SOFT_BORDER",
            "#2f6fb3": "BRAND",
        }
        role = legacy.get(normalized)
        if role:
            return role
        for role, value in self.THEMES["dark"].items():
            if normalized == value.lower():
                return role
        for role, value in self.THEMES["light"].items():
            if normalized == value.lower():
                return role
        return None

    def _configure_ttk_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(9, 4), font=("Arial", 9),
                        background=self.palette["PANEL"], foreground=self.TEXT,
                        bordercolor=self.BORDER)
        style.map("TNotebook.Tab",
                  background=[("selected", self.palette["SURFACE"]),
                              ("active", self.palette["PANEL_ALT"])],
                  foreground=[("selected", self.TEXT), ("!selected", self.palette["SUBTEXT"])])
        style.configure("TScrollbar", background=self.palette["PANEL"],
                        troughcolor=self.BG, bordercolor=self.BORDER,
                        arrowcolor=self.TEXT)

    def _apply_widget_theme(self, parent: tk.Misc) -> None:
        if parent is self.toast_window:
            return
        palette = self.palette
        try:
            if isinstance(parent, (tk.Tk, tk.Toplevel)):
                parent.configure(bg=self.BG)
            elif isinstance(parent, tk.Frame):
                role = self._color_role(parent.cget("bg"))
                if role:
                    parent.configure(bg=palette.get(role, self.BG))
                for option in ("highlightbackground", "highlightcolor"):
                    role = self._color_role(parent.cget(option))
                    if role:
                        parent.configure(**{option: palette[role]})
            elif isinstance(parent, tk.Label):
                bg_role = self._color_role(parent.cget("bg"))
                fg_role = self._color_role(parent.cget("fg"))
                parent.configure(bg=palette.get(bg_role, self.BG),
                                 fg=palette.get(fg_role, self.TEXT))
            elif isinstance(parent, tk.Entry):
                parent.configure(bg=palette["INPUT_BG"], fg=palette["INPUT_FG"],
                                 insertbackground=palette["INPUT_FG"],
                                 selectbackground=palette["SELECT_BG"],
                                 selectforeground=palette["TEXT"])
            elif isinstance(parent, tk.Text):
                parent.configure(bg=palette["INPUT_BG"], fg=palette["INPUT_FG"],
                                 insertbackground=palette["INPUT_FG"],
                                 selectbackground=palette["SELECT_BG"],
                                 selectforeground=palette["TEXT"])
            elif isinstance(parent, tk.Checkbutton):
                parent.configure(bg=self.BG, fg=self.TEXT, activebackground=self.BG,
                                 activeforeground=self.TEXT, selectcolor=palette["PANEL"],
                                 disabledforeground=palette["MUTED"])
            elif isinstance(parent, tk.Button):
                parent.configure(bg=palette["BUTTON_BG"], fg=self.TEXT,
                                 activebackground=palette["BUTTON_ACTIVE"],
                                 activeforeground=self.TEXT)
            for child in parent.winfo_children():
                self._apply_widget_theme(child)
        except tk.TclError:
            return

    def _toggle_theme(self) -> None:
        self._set_theme_palette(self.dark_theme.get())
        self.root.configure(bg=self.BG)
        self._configure_ttk_styles()
        self._apply_widget_theme(self.root)
        if self._save_theme_preference():
            self.status.set("Включена тёмная тема" if self.dark_theme.get()
                            else "Включена светлая тема")

    def _build_ui(self) -> None:
        self._configure_ttk_styles()

        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=2, pady=(2, 0))

        info_tab = tk.Frame(notebook, bg=self.BG)
        mp_tab = tk.Frame(notebook, bg=self.BG)
        forum_tab = tk.Frame(notebook, bg=self.BG)
        notebook.add(info_tab, text="ИНФО")
        notebook.add(mp_tab, text="МП")
        notebook.add(forum_tab, text="Forum")
        self._build_info(info_tab)
        self._build_gmp(mp_tab)
        self._build_forum(forum_tab)

        footer = tk.Frame(self.root, bg=self.BG, height=58, highlightbackground=self.BORDER,
                          highlightthickness=1)
        footer.grid(row=1, column=0, sticky="ew")
        footer.grid_propagate(False)
        tk.Label(footer, text="by Пепси Кольный", bg=self.BG, fg=self.BLUE,
                 font=("Arial", 29, "bold")).pack(expand=True)

        bottom = tk.Frame(self.root, bg=self.BG, height=23)
        bottom.grid(row=2, column=0, sticky="ew", padx=3, pady=(1, 2))
        bottom.grid_propagate(False)
        tk.Label(bottom, text="ДС: okyrihuw", bg=self.BG, fg="black",
                 font=("Arial", 9, "bold")).pack(side="left")
        tk.Label(bottom, textvariable=self.status, bg=self.BG, fg="#333333",
                 font=("Arial", 9)).pack(side="right", padx=8)
        self._apply_widget_theme(self.root)

    def _build_info(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        card = tk.Frame(parent, bg=self.BG)
        card.grid(row=0, column=0, sticky="", padx=30, pady=24)
        tk.Label(card, text="NRP Binder", bg=self.BG, fg=self.TEXT,
                 font=("Arial", 28, "bold")).pack(pady=(8, 6))
        tk.Label(card,
                 text="Помощник администрации для подготовки игровых команд и последовательной выдачи наказаний и действий.",
                 bg=self.BG, fg=self.TEXT, font=("Arial", 12), wraplength=720,
                 justify="center").pack(pady=(0, 18))
        tk.Label(card, text="Автор: Пепси Кольный", bg=self.BG,
                 font=("Arial", 12, "bold")).pack(pady=3)
        tk.Label(card, text="Сервер: Байкальский", bg=self.BG,
                 font=("Arial", 11)).pack(pady=2)
        tk.Label(card, text="Discord: okyrihuw", bg=self.BG,
                 font=("Arial", 11)).pack(pady=(2, 8))
        tk.Checkbutton(card, text="Тёмная тема", variable=self.dark_theme,
                       command=self._toggle_theme, bg=self.BG, fg=self.TEXT,
                       activebackground=self.BG, activeforeground=self.TEXT,
                       selectcolor=self.palette["PANEL"], font=("Arial", 10)).pack(pady=(0, 10))
        tk.Button(card, text="Как пользоваться МП", width=30, height=2,
                  command=self._show_gmp_help, font=("Arial", 11)).pack(pady=5)
        tk.Button(card, text="Режим работы программы", width=30, height=2,
                  command=self._show_work_mode, font=("Arial", 11)).pack(pady=5)
        tk.Label(card, text="Настройка игроков и горячих клавиш - во вкладке «МП».",
                 bg=self.BG, font=("Arial", 10)).pack(pady=(15, 5))

    def _show_help_window(self, title: str, body: str) -> None:
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("720x570")
        window.minsize(560, 420)
        window.configure(bg=self.BG)
        window.transient(self.root)
        window.grid_rowconfigure(1, weight=1)
        window.grid_columnconfigure(0, weight=1)
        tk.Label(window, text=title, bg=self.BG, fg=self.TEXT,
                 font=("Arial", 16, "bold")).grid(row=0, column=0, pady=(12, 8))
        text_frame = tk.Frame(window, bg=self.BG)
        text_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)
        text = tk.Text(text_frame, wrap="word", font=("Arial", 11), bg="white",
                       relief="solid", bd=1, padx=12, pady=10)
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.insert("1.0", body)
        text.configure(state="disabled")
        tk.Button(window, text="Закрыть", command=window.destroy,
                  font=("Arial", 10), width=14).grid(row=2, column=0, pady=(0, 10))

    def _show_gmp_help(self) -> None:
        self._show_help_window("Как пользоваться МП", """1. Заполните ID игроков в нужной колонке. Четыре колонки - независимые группы, в каждой можно указать до 15 игроков. Пустые ID пропускаются.

2. Скины делят игроков внутри одной колонки на две последовательные команды. Если заполнены оба поля: первая половина ID получает «Скин 1», вторая - «Скин 2». При нечётном числе игроков первая команда получает на одного игрока больше. Если заполнено только одно поле скина, он применяется ко всем ID этой группы.

3. Укажите до трёх ячеек оружия. Для каждого ID оружия выдаются по порядку: сначала все заполненные слоты первому игроку, затем следующему. Команда `weapongive` получает 1000 патронов.

4. Значения ХР и брони применяются ко всем заполненным ID во всех четырёх колонках. Если поле пустое, соответствующие команды не создаются.

5. Нажмите «Сохранить» для записи текущего пресета. «Обновить из пресета» загружает последнее сохранение, «Очистить поля» очищает только экран.

БИНДЫ
Ctrl+1 - телепорт игроков к себе
Ctrl+2 - выдать скины по командам
Ctrl+3 - выдать оружие
Ctrl+4 - выдать здоровье
Ctrl+5 - выдать броню
Ctrl+6 - возродить игроков""")

    def _show_work_mode(self) -> None:
        self._show_help_window("Режим работы программы", """Для работы с NextRP запускайте NRP Binder от имени администратора.

Программа работает полуавтоматически: она подготавливает команды в буфере обмена, а вставку и Enter вы нажимаете сами.

1. Включите флажок горячих клавиш во вкладке МП.
2. Переключитесь в игру, откройте консоль клавишей F8 и оставьте активным окно игры. Программа не нажимает F8 и не отправляет Enter.
3. Нажмите нужный бинд, например Ctrl+1. Первая команда действия будет скопирована в буфер.
4. Физически нажмите Ctrl+V, затем Enter. Программа замечает физический Ctrl+V и через короткую паузу копирует следующую команду.
5. Повторяйте Ctrl+V и Enter. После вставки последней команды буфер очищается.

Очередь продвигается только при физическом Ctrl+V, когда активно то же игровое приложение, в котором был запущен бинд. Если нажать другой бинд, он заменит текущую очередь. Горячие клавиши доступны, пока программа открыта и включён соответствующий флажок.""")

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
                     font=("Arial", 11, "bold"), anchor="center").grid(
                         row=0, column=0, columnspan=2, sticky="ew", pady=(0, 3), ipady=3)
            for i in range(15):
                self._field(frame, f"id_{group}_{i}", f"ID-{i+1}:", i + 1,
                            width=9, label_width=6, compact=True)
            tk.Label(frame, text="Скины: первая / вторая половина", bg=self.BG,
                     font=("Arial", 9, "bold"), anchor="w").grid(
                         row=16, column=0, columnspan=2, sticky="ew", pady=(4, 1))
            self._field(frame, f"skin_{group}_0", "Скин 1:", 17,
                        width=9, label_width=6, compact=True)
            self._field(frame, f"skin_{group}_1", "Скин 2:", 18,
                        width=9, label_width=6, compact=True)
            tk.Label(frame, text="Оружие по порядку для ID", bg=self.BG,
                     font=("Arial", 9, "bold"), anchor="w").grid(
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
            tk.Checkbutton(controls, text="Включить бинды МП",
                           variable=self.mp_hotkeys_enabled,
                           command=lambda: self._toggle_hotkeys("МП"),
                           bg=self.BG, activebackground=self.BG,
                           font=("Arial", 10, "bold")).grid(row=2, column=0, columnspan=2,
                                                             sticky="w", pady=(5, 4))
        else:
            tk.Label(controls, text="Горячие клавиши доступны в Windows-сборке.",
                     bg=self.BG, font=("Arial", 9)).grid(row=2, column=0, columnspan=2,
                                                          sticky="w", pady=(5, 4))
        tk.Label(controls, text="Действие / активация", bg=self.BG,
                 font=("Arial", 10, "bold")).grid(row=3, column=0, columnspan=2,
                                                   sticky="w", pady=(3, 1))
        for row, (label, key, _action) in enumerate(HOTKEYS, start=4):
            tk.Label(controls, text=label, bg=self.BG, font=("Arial", 9),
                     anchor="w").grid(row=row, column=0, sticky="w", pady=1)
            tk.Label(controls, text=key, bg=self.BG, font=("Arial", 9, "bold"),
                     anchor="e").grid(row=row, column=1, sticky="e", padx=(4, 0), pady=1)

        button_row = 4 + len(HOTKEYS)
        buttons = [
            ("Сохранить", self.save_mp_preset),
            ("Очистить поля", self.clear_mp_fields),
            ("Обновить из пресета", self.load_mp_preset),
        ]
        for index, (text, callback) in enumerate(buttons):
            tk.Button(controls, text=text, command=callback, height=1,
                      font=("Arial", 9)).grid(row=button_row + index, column=0, columnspan=2,
                                              sticky="ew", pady=2)
        tk.Label(controls,
                 text="В очереди: Ctrl+V → Enter. Следующая команда готовится сама.",
                 bg=self.BG, font=("Arial", 9), wraplength=255, justify="left").grid(
                     row=button_row + len(buttons), column=0, columnspan=2,
                     sticky="ew", pady=(5, 0))

    def _build_forum(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        toolbar = tk.Frame(parent, bg=self.BG)
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(7, 4))
        toolbar.grid_columnconfigure(1, weight=1)
        if WINDOWS:
            tk.Checkbutton(toolbar, text="Включить бинд Forum",
                           variable=self.forum_hotkeys_enabled,
                           command=lambda: self._toggle_hotkeys("Forum"),
                           bg=self.BG, activebackground=self.BG,
                           font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w")
        else:
            tk.Label(toolbar, text="Бинд доступен в Windows-сборке",
                     bg=self.BG, font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(toolbar, text="Активация: Alt+Ctrl+1", bg=self.BG,
                 font=("Arial", 10, "bold")).grid(row=0, column=1, sticky="w", padx=12)
        buttons = (("Сохранить", self.save_forum_preset),
                   ("Очистить", self.clear_forum_fields),
                   ("Обновить", self.load_forum_preset))
        button_frame = tk.Frame(toolbar, bg=self.BG)
        button_frame.grid(row=0, column=2, sticky="e")
        for text, callback in buttons:
            tk.Button(button_frame, text=text, command=callback,
                      font=("Arial", 9), width=13).pack(side="left", padx=3)

        columns = tk.Frame(parent, bg=self.BG)
        columns.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        columns.grid_rowconfigure(0, weight=1)
        for col in range(3):
            columns.grid_columnconfigure(col, weight=1, uniform="forum_columns")
        for col, (title, command) in enumerate(FORUM_COLUMNS):
            frame = tk.Frame(columns, bg=self.BG, highlightbackground="#777777",
                             highlightthickness=1, padx=4, pady=4)
            frame.grid(row=0, column=col, sticky="nsew", padx=3)
            frame.grid_columnconfigure(2, weight=1)
            tk.Label(frame, text=title, bg="#e5e5e5", fg=self.TEXT,
                     font=("Arial", 12, "bold"), anchor="center").grid(
                         row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4), ipady=3)
            for field_col, text in enumerate(("ID", "Срок", "Причина")):
                tk.Label(frame, text=text, bg=self.BG, fg=self.TEXT,
                         font=("Arial", 9, "bold"), anchor="w").grid(
                             row=1, column=field_col, sticky="ew", padx=(1, 3), pady=(0, 2))
            for row in range(FORUM_ROWS):
                for field_col, field_name, width in ((0, "id", 6), (1, "duration", 8), (2, "reason", 22)):
                    key = f"forum_{command}_{field_name}_{row}"
                    entry = tk.Entry(frame, width=width, font=("Arial", 9), relief="sunken", bd=1)
                    entry.grid(row=row + 2, column=field_col, sticky="ew", padx=(1, 3), pady=2, ipady=2)
                    self.fields[key] = entry
        tk.Label(parent, text="Заполняйте ID, срок и причину; неполные строки пропускаются. Очередь вставляется вручную через Ctrl+V и Enter.",
                 bg=self.BG, fg="#333333", font=("Arial", 9), anchor="w").grid(
                     row=2, column=0, sticky="ew", padx=10, pady=(1, 5))

    def clear_forum_fields(self) -> None:
        for key, entry in self.fields.items():
            if key.startswith("forum_"):
                entry.delete(0, tk.END)
        self.status.set("Поля Forum очищены; сохранённый пресет не изменён")

    def _forum_commands(self) -> list[str]:
        commands = []
        for _title, command in FORUM_COLUMNS:
            for row in range(FORUM_ROWS):
                player_id = self.fields[f"forum_{command}_id_{row}"].get().strip()
                duration = self.fields[f"forum_{command}_duration_{row}"].get().strip()
                reason = self.fields[f"forum_{command}_reason_{row}"].get().strip()
                if not (player_id and duration and reason):
                    continue
                if not self._valid_token(player_id) or any(ch.isspace() for ch in duration):
                    continue
                if "\n" in reason or "\r" in reason:
                    continue
                commands.append(f"{command} {player_id} {duration} {reason}")
        return commands

    def _field(self, parent: tk.Widget, key: str, label: str, row: int, width: int = 10,
               label_width: int = 8, compact: bool = False) -> None:
        font_size = 10 if compact else 11
        padding_y = 0 if compact else 1
        inner_padding = 1 if compact else 2
        tk.Label(parent, text=label, bg=self.BG, fg=self.TEXT,
                 font=("Arial", font_size), width=label_width, anchor="w").grid(
                     row=row, column=0, sticky="w", pady=padding_y)
        entry = tk.Entry(parent, width=width, font=("Arial", font_size), relief="sunken", bd=1)
        entry.grid(row=row, column=1, sticky="ew", pady=padding_y, ipady=inner_padding)
        self.fields[key] = entry

    def _toggle_hotkeys(self, category: str) -> None:
        active_categories = {
            "МП": self.mp_hotkeys_enabled.get(),
            "Forum": self.forum_hotkeys_enabled.get(),
        }
        conflicts = find_hotkey_conflicts(active_categories)
        if conflicts:
            if category == "МП":
                self.mp_hotkeys_enabled.set(False)
            else:
                self.forum_hotkeys_enabled.set(False)
            details = "\n".join(
                f"{shortcut}: {', '.join(categories)}"
                for shortcut, categories in conflicts.items()
            )
            self.status.set("Ошибка: сочетание занято в нескольких разделах")
            messagebox.showerror(
                "Конфликт горячих клавиш",
                "Одно сочетание включено сразу в нескольких разделах:\n" + details
            )
            return

        enabled_categories = {name for name, enabled in active_categories.items() if enabled}
        ok, failed_ids = self.hotkeys.set_active_categories(enabled_categories)
        if ok:
            if failed_ids:
                failed_keys = [ALL_HOTKEYS[hotkey_id - 1][1] for hotkey_id in failed_ids]
                self.status.set("Горячие клавиши включены частично")
                messagebox.showwarning("Горячие клавиши", "Не зарегистрировались: " + ", ".join(failed_keys))
            else:
                self.status.set("Горячие клавиши включены" if enabled_categories else "Горячие клавиши выключены")
        else:
            self.mp_hotkeys_enabled.set(False)
            self.forum_hotkeys_enabled.set(False)
            self.hotkeys.set_active_categories(set())
            self.status.set("Не удалось включить горячие клавиши")
            messagebox.showwarning(
                "Горячие клавиши",
                "Не удалось зарегистрировать сочетания. Возможно, их заняла другая программа."
            )

    def _queue_hotkey(self, index: int) -> None:
        # Read Tk widgets only on the UI thread; the Win32 message loop stays separate.
        self.events.put(("hotkey", index))

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
            verb = "get" if action == "teleport" else "reviving"
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
                result.extend(f"tempskin {pid} {skin}"
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
                        result.append(f"weapongive {pid} {weapon} 1000")
            return result
        if action in ("health", "armor"):
            value = self.fields[action].get().strip()
            if not self._valid_token(value):
                return []
            verb = "sethp" if action == "health" else "setarmor"
            return [f"{verb} {pid} {value}" for pid in ids if self._valid_token(pid)]
        return []

    def _hotkey_action(self, index: int) -> None:
        commands = self._forum_commands() if index == len(HOTKEYS) else self._commands_for(index)
        if not commands:
            self.status.set("Нет заполненных данных - очередь не создана")
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
        self._show_copy_toast(commands[0], 1, len(commands))

        self.status.set(
            f"Команда 1/{len(commands)} в буфере: Ctrl+V, затем Enter. "
            "После Ctrl+V следующая команда подготовится автоматически."
        )

    def _show_copy_toast(self, command: str, index: int, total: int) -> None:
        if self.toast_after_id is not None:
            try:
                self.root.after_cancel(self.toast_after_id)
            except tk.TclError:
                pass
            self.toast_after_id = None
        if self.toast_window is not None:
            try:
                self.toast_window.destroy()
            except tk.TclError:
                pass
            self.toast_window = None

        window = tk.Toplevel(self.root)
        self.toast_window = window
        window.withdraw()
        window.overrideredirect(True)
        window.configure(bg="#17191b")
        window.attributes("-topmost", True)

        card = tk.Frame(window, bg="#17191b", highlightbackground="#303438",
                        highlightthickness=1, width=440, height=122)
        card.pack(fill="both", expand=True)
        card.pack_propagate(False)
        tk.Frame(card, bg="#36d66b", width=5).pack(side="left", fill="y")
        content = tk.Frame(card, bg="#17191b")
        content.pack(side="left", fill="both", expand=True, padx=(14, 12), pady=10)
        tk.Label(content, text="СКОПИРОВАНО В БУФЕР", bg="#17191b", fg="#45e576",
                 font=("Arial", 9, "bold"), anchor="w").pack(fill="x")
        display_command = command if len(command) <= 100 else command[:97] + "..."
        tk.Label(content, text=display_command, bg="#17191b", fg="#ffffff",
                 font=("Arial", 12, "bold"), anchor="w", justify="left",
                 wraplength=390).pack(fill="x", pady=(4, 2))
        tk.Label(content, text="Открой F8 и нажми Ctrl+V", bg="#17191b", fg="#c3c7cb",
                 font=("Arial", 9), anchor="w").pack(fill="x")
        history_hint = ("Win+V: не сохраняется" if self.hotkeys.last_history_exclusion_ok
                        else "Win+V может сохранить команду")
        tk.Label(content, text=f"Enter нажми сам  -  {index}/{total}  -  {history_hint}",
                 bg="#17191b", fg="#9aa1a8", font=("Arial", 8),
                 anchor="w").pack(fill="x", pady=(2, 0))

        width, height = 440, 122
        target_rect = self.hotkeys.window_rect(self.pending_target_hwnd)
        if target_rect:
            left, top, right, bottom = target_rect
            x = left + (right - left - width) // 2
            y = bottom - height - 32
            x = min(max(x, left + 6), max(left + 6, right - width - 6))
            y = min(max(y, top + 6), max(top + 6, bottom - height - 6))
        else:
            x = max(6, (self.root.winfo_screenwidth() - width) // 2)
            y = max(24, self.root.winfo_screenheight() - height - 48)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.update_idletasks()
        try:
            frame_id = window.wm_frame()
            hwnd = int(frame_id, 0) if isinstance(frame_id, str) else int(frame_id)
        except (AttributeError, ValueError, tk.TclError):
            hwnd = int(window.winfo_id())
        self.hotkeys.configure_nonactivating_overlay(hwnd)
        window.deiconify()
        window.update_idletasks()
        self.hotkeys.show_topmost_overlay(hwnd, x, y, width, height)
        self.toast_after_id = self.root.after(2600, lambda: self._dismiss_copy_toast(window))

    def _dismiss_copy_toast(self, window: tk.Toplevel) -> None:
        if self.toast_window is window:
            self.toast_window = None
            self.toast_after_id = None
        try:
            if window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass

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
        self._show_copy_toast(command, next_index + 1, len(self.pending_commands))
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

    @staticmethod
    def _is_forum_field(key: str) -> bool:
        return key.startswith("forum_")

    def _save_preset_subset(self, forum: bool | None) -> None:
        try:
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            data.update({key: entry.get() for key, entry in self.fields.items()
                         if forum is None or self._is_forum_field(key) == forum})
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            category = "Forum" if forum is True else "МП" if forum is False else "программы"
            self.status.set(f"Пресет {category} сохранён")
            messagebox.showinfo("Пресет", f"Настройки {category} сохранены.")
        except OSError as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))

    def _load_preset_subset(self, forum: bool | None) -> None:
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise json.JSONDecodeError("Ожидался объект настроек", "", 0)
        except FileNotFoundError:
            self.status.set("Сохранённый пресет не найден")
            messagebox.showinfo("Пресет", "Пока нет сохранённого пресета.")
            return
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("Ошибка загрузки", str(exc))
            return
        for key, entry in self.fields.items():
            if forum is None or self._is_forum_field(key) == forum:
                entry.delete(0, tk.END)
                entry.insert(0, str(data.get(key, "")))
        category = "Forum" if forum is True else "МП" if forum is False else "программы"
        self.status.set(f"Пресет {category} загружен")

    def save_mp_preset(self) -> None:
        self._save_preset_subset(forum=False)

    def load_mp_preset(self) -> None:
        self._load_preset_subset(forum=False)

    def save_forum_preset(self) -> None:
        self._save_preset_subset(forum=True)

    def load_forum_preset(self) -> None:
        self._load_preset_subset(forum=True)

    def save_preset(self) -> None:
        self._save_preset_subset(forum=None)

    def load_preset(self) -> None:
        self._load_preset_subset(forum=None)

    def clear_fields(self) -> None:
        self.clear_mp_fields()

    def clear_mp_fields(self) -> None:
        for key, entry in self.fields.items():
            if not key.startswith("forum_"):
                entry.delete(0, tk.END)
        self.status.set("Поля МП очищены; сохранённый пресет не изменён")

    def close(self) -> None:
        self.hotkeys.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
