"""Keep the Windows console's Shift modifier on the explicit push shortcut.

Textual's Windows monitor normally converts KEY_EVENT_RECORD to UnicodeChar
alone. Both Ctrl+S and Ctrl+Shift+S have character U+0013, so that conversion
loses their distinction even though Windows provides the modifier bits.
"""
from __future__ import annotations

import asyncio
import codecs
import os
import re
import time
from typing import TYPE_CHECKING

from textual import events
from textual._xterm_parser import XTermParser
from textual.keys import Keys

if TYPE_CHECKING:
    from textual.driver import Driver


CTRL_PRESSED = 0x0004 | 0x0008
SHIFT_PRESSED = 0x0010
ALT_PRESSED = 0x0001 | 0x0002
VK_S = 0x53
SHIFT_SAVE_SEQUENCE = "\x1b[115;6u"
WIN32_INPUT_ENABLE = "\x1b[?9001h"
WIN32_INPUT_DISABLE = "\x1b[?9001l"
_WIN32_KEY = re.compile(r"\x1b\[([0-9;]*)_\Z")
_MODIFIER_KEYS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
_SPECIAL_KEYS = {
    0x08: ("backspace", "\x7f"), 0x09: ("tab", "\t"),
    0x0D: ("enter", "\r"), 0x1B: ("escape", "\x1b"),
    0x20: ("space", " "),
    0x21: ("pageup", None), 0x22: ("pagedown", None),
    0x23: ("end", None), 0x24: ("home", None),
    0x25: ("left", None), 0x26: ("up", None),
    0x27: ("right", None), 0x28: ("down", None),
    0x2C: ("print_screen", None), 0x2D: ("insert", None),
    0x2E: ("delete", None), 0x13: ("pause", None), 0x5D: ("menu", None),
}


class WindowsXTermParser(XTermParser):
    """Decode Windows Terminal keyboard frames alongside ordinary VT input.

    Mode 9001 preserves modifiers where ordinary ConPTY input has already lost
    them. Let Textual frame the stream: mouse, paste, focus and escape timing
    retain the stock parser's behavior, including across separate reads.
    """

    def __init__(self, *args, **kwargs):
        self._high_surrogate: int | None = None
        super().__init__(*args, **kwargs)

    def _sequence_to_key_events(self, sequence: str, alt: bool = False):
        match = _WIN32_KEY.fullmatch(sequence)
        if match is None:
            yield from super()._sequence_to_key_events(sequence, alt=alt)
            return
        fields = match[1].split(";")
        # An ignored key tells Textual that the complete sequence was consumed.
        ignored = events.Key(Keys.Ignore, sequence)
        if len(fields) > 6:
            yield ignored
            return
        defaults = (0, 0, 0, 0, 0, 1)
        values = [int(value) if value else defaults[index]
                  for index, value in enumerate(fields)]
        values.extend(defaults[len(values):])
        virtual_key, scan, unit, down, modifiers, repeat = values
        if (virtual_key > 0xFF or scan > 0xFFFF or unit > 0xFFFF
                or down not in (0, 1) or modifiers > 0x1FF or repeat > 0xFFFF):
            yield ignored
            return
        if not down or virtual_key in _MODIFIER_KEYS or not repeat:
            yield ignored
            return
        if 0xD800 <= unit <= 0xDBFF:
            self._high_surrogate = unit
            yield ignored
            return
        if 0xDC00 <= unit <= 0xDFFF:
            unit = (0x10000 + ((self._high_surrogate - 0xD800) << 10) + unit - 0xDC00
                    if self._high_surrogate is not None else 0xFFFD)
            self._high_surrogate = None
        elif self._high_surrogate is not None:
            self._high_surrogate = None
            yield from super()._sequence_to_key_events("\ufffd")
        keys = self._windows_key(virtual_key, unit, modifiers)
        if not keys:
            yield ignored
        else:
            for _ in range(repeat):
                for key in keys:
                    yield key.copy()

    def _windows_key(self, virtual_key: int, unit: int, modifiers: int):
        ctrl = bool(modifiers & CTRL_PRESSED)
        alt = bool(modifiers & ALT_PRESSED)
        shift = bool(modifiers & SHIFT_PRESSED)
        character = chr(unit)
        # Windows reports AltGr as left Ctrl + right Alt. Its translated text
        # must remain text, including non-ASCII keyboard layouts.
        if unit >= 0x20 and modifiers & 0x08 and modifiers & 0x01:
            return list(super()._sequence_to_key_events(character))
        if 0x70 <= virtual_key <= 0x87:
            name, character = f"f{virtual_key - 0x6F}", None
        elif virtual_key in _SPECIAL_KEYS:
            name, character = _SPECIAL_KEYS[virtual_key]
        elif ctrl and 0x41 <= virtual_key <= 0x5A:
            name, character = chr(virtual_key + 0x20), None
        elif ctrl and virtual_key == 0x20:
            name, character = "space", None
        elif ctrl and 0x30 <= virtual_key <= 0x39:
            name, character = chr(virtual_key), None
        elif unit:
            keys = list(super()._sequence_to_key_events(character))
            if not (ctrl or alt):
                # Shift alone is represented by the actual translated letter
                # or punctuation, as in Textual's ordinary text input.
                return keys
            name = keys[0].key
            if name.startswith("ctrl+"):
                name = name[5:]
            if len(name) == 1:
                name = name.lower()
            if ctrl and unit == 0x1B:
                name = "left_square_bracket"
            character = None
        elif ctrl:
            name, character = "at", None
        else:
            return []
        prefixes = [name for enabled, name in ((alt, "alt"), (ctrl, "ctrl"), (shift, "shift")) if enabled]
        return [events.Key("+".join([*prefixes, name]), None if prefixes else character)]


def console_key_text(key_event) -> str:
    """Translate one native key record without collapsing Shift+Ctrl+S.

    Preserve Textual's treatment of VT-generated records, key releases and
    ordinary text. The CSI-u sequence goes through its existing parser, so a
    physical key and a terminal already reporting CSI-u use identical bindings.
    """
    if not key_event.bKeyDown:
        return ""
    modifiers = key_event.dwControlKeyState
    virtual_key = key_event.wVirtualKeyCode
    if modifiers and virtual_key == 0:
        return ""
    character = key_event.uChar.UnicodeChar
    if (virtual_key == VK_S and character == "\x13"
            and modifiers & CTRL_PRESSED and modifiers & SHIFT_PRESSED
            and not modifiers & ALT_PRESSED):
        return SHIFT_SAVE_SEQUENCE
    return character


def preserve_windows_modifiers(driver: type[Driver]) -> type[Driver]:
    """Replace the stock Windows driver; leave custom and other drivers alone."""
    if os.name == "nt":
        from textual.drivers.windows_driver import WindowsDriver
        if driver is WindowsDriver:
            return TaskmanWindowsDriver
    return driver


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    from textual import constants
    from textual._xterm_parser import XTermParser
    from textual.drivers import win32
    from textual.drivers._writer_thread import WriterThread
    from textual.drivers.windows_driver import WindowsDriver

    class WindowsEventMonitor(win32.EventMonitor):
        """Use native modifier information before Textual parses console text."""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._pending_save = False
            self._pending_save_modifiers: int | None = None
            self._pending_save_deadline = 0.0
            self._modifiers = 0
            self._utf16 = codecs.getincrementaldecoder("utf-16-le")("replace")

        def _key_text(self, record) -> str:
            # ENABLE_VIRTUAL_TERMINAL_INPUT also strips modifiers from native
            # key-down records: Ctrl+Shift+S becomes VK=0 / U+0013. Its native
            # S key-up still carries the modifiers. Defer this one ambiguous
            # chord until release instead of changing terminal/mouse modes or
            # guessing from the keyboard's current global state.
            virtual_key = record.wVirtualKeyCode
            if (record.bKeyDown and virtual_key == 0
                    and record.uChar.UnicodeChar == "\x13"):
                if self._modifiers & CTRL_PRESSED:
                    # Modifier key records preserve their native state even
                    # when S is flattened. Act on key-down when that state is
                    # available, including when the chord is held to repeat.
                    return (SHIFT_SAVE_SEQUENCE if self._modifiers & SHIFT_PRESSED
                            and not self._modifiers & ALT_PRESSED else "\x13")
                if not self._pending_save:
                    self._pending_save_modifiers = None
                    self._pending_save_deadline = time.monotonic() + 1.0
                self._pending_save = True
                return ""
            if not record.bKeyDown and virtual_key == VK_S and self._pending_save:
                modifiers = (record.dwControlKeyState if self._pending_save_modifiers is None
                             else self._pending_save_modifiers)
                self._pending_save = False
                self._pending_save_modifiers = None
                return (SHIFT_SAVE_SEQUENCE if modifiers & SHIFT_PRESSED
                        and not modifiers & ALT_PRESSED else "\x13")
            if virtual_key in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
                if self._pending_save and self._pending_save_modifiers is None and not record.bKeyDown:
                    # A terminal may omit modifier presses but retain their
                    # releases. A released modifier was held for the pending S.
                    released = (SHIFT_PRESSED if virtual_key in (0x10, 0xA0, 0xA1)
                                else CTRL_PRESSED if virtual_key in (0x11, 0xA2, 0xA3)
                                else ALT_PRESSED)
                    self._pending_save_modifiers = record.dwControlKeyState | released
                self._modifiers = record.dwControlKeyState
                if record.uChar.UnicodeChar == "\x00":
                    return ""
            return console_key_text(record)

        def _expire_pending_save(self) -> str:
            if self._pending_save and time.monotonic() >= self._pending_save_deadline:
                # VT-only streams may have no native key-up. Preserve their
                # existing local Ctrl+S behavior after a bounded wait. A late
                # key-up cannot turn that editor save into a publish action.
                self._pending_save = False
                self._pending_save_modifiers = None
                return "\x13"
            return ""

        def dispatch_records(self, records, parser: XTermParser) -> None:
            keys: list[str] = [self._expire_pending_save()]
            new_size = None
            for record in records:
                if record.EventType == 0x0001:  # KEY_EVENT
                    keys.append(self._key_text(record.Event.KeyEvent))
                elif record.EventType == 0x0004:  # WINDOW_BUFFER_SIZE_EVENT
                    size = record.Event.WindowBufferSizeEvent.dwSize
                    new_size = (size.X, size.Y)
            if any(keys):
                # Native WCHAR records may contain UTF-16 surrogate pairs.
                text = self._utf16.decode("".join(keys).encode("utf-16-le", "surrogatepass"))
                if text:
                    for event in parser.feed(text):
                        self.process_event(event)
            if new_size is not None:
                self.on_size_change(*new_size)

        def run(self) -> None:
            parser = WindowsXTermParser(debug=constants.DEBUG)
            input_handle = win32.GetStdHandle(win32.STD_INPUT_HANDLE)
            read = win32.KERNEL32.ReadConsoleInputW
            read.argtypes = [wintypes.HANDLE, ctypes.POINTER(win32.INPUT_RECORD),
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            read.restype = wintypes.BOOL
            records = (win32.INPUT_RECORD * 1024)()
            count = wintypes.DWORD()
            try:
                while not self.exit_event.is_set():
                    expired = self._expire_pending_save()
                    if expired:
                        for event in parser.feed(expired):
                            self.process_event(event)
                    for event in parser.tick():
                        self.process_event(event)
                    if win32.wait_for_handles([input_handle], 100) is None:
                        continue
                    if not read(input_handle, records, len(records), ctypes.byref(count)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    self.dispatch_records(records[:count.value], parser)
            except Exception as error:
                self.app.log.error("EVENT MONITOR ERROR", error)

    class TaskmanWindowsDriver(WindowsDriver):
        """Stock Textual Windows rendering and teardown, with corrected input."""

        def start_application_mode(self) -> None:
            # Match Textual 8's startup sequence; only its input monitor changes.
            # Rendering, mouse parsing, console restoration and worker shutdown
            # remain owned by the installed Textual driver.
            loop = asyncio.get_running_loop()
            self._restore_console = win32.enable_application_mode()
            self._writer_thread = WriterThread(self._file)
            self._writer_thread.start()
            self.write("\x1b[?1049h")
            self._enable_mouse_support()
            self.write("\x1b[?25l")
            self.write("\x1b[?1004h")
            self.write("\x1b[>1u")
            self.write(WIN32_INPUT_ENABLE)
            self.flush()
            self._enable_bracketed_paste()
            self._event_thread = WindowsEventMonitor(loop, self._app, self.exit_event,
                                                    self.process_message)
            self._event_thread.start()

        def stop_application_mode(self) -> None:
            self.write(WIN32_INPUT_DISABLE)
            self.flush()
            super().stop_application_mode()
