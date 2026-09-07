"""Win32 input protocol regressions without a desktop or network connection."""
from __future__ import annotations

import os
import threading

import pytest
from textual import events
from textual._xterm_parser import XTermParser

from tui import terminal


def frame(vk=0x53, char=19, modifiers=0x18, down=1, repeat=1, scan=31):
    return f"\x1b[{vk};{scan};{char};{down};{modifiers};{repeat}_"


def keys(text, *, split=0):
    parser = terminal.WindowsXTermParser()
    messages = []
    for part in ([text] if not split else [text[index:index + split] for index in range(0, len(text), split)]):
        messages.extend(parser.feed(part))
    return [(event.key, event.character) for event in messages if isinstance(event, events.Key)]


@pytest.mark.parametrize("split", [0, 1, 2, 7])
def test_complete_keyboard_chords_decode_across_read_boundaries(split):
    text = (frame(0x11, 0, 8) + frame(0x10, 0, 24)
            + frame() + frame(down=0) + frame(0x10, 0, 8, down=0)
            + frame(0x11, 0, 0, down=0) + frame(modifiers=8))
    assert keys(text, split=split) == [("ctrl+shift+s", None), ("ctrl+s", None)]


@pytest.mark.parametrize("vk,char,modifiers,expected", [
    (0x41, 97, 0, ("a", "a")),
    (0x41, 65, 16, ("A", "A")),
    (0x31, 33, 16, ("exclamation_mark", "!")),
    (0x45, 233, 0, ("é", "é")),
    (0, 0x4E2D, 0, ("中", "中")),
    (0x51, 64, 9, ("at", "@")),  # AltGr text: Ctrl-left + Alt-right.
    (0x45, 8364, 9, ("euro_sign", "€")),
    (0x41, 97, 2, ("alt+a", None)),
    (0x41, 65, 18, ("alt+shift+a", None)),
    (0x41, 1, 10, ("alt+ctrl+a", None)),
    (0x48, 8, 8, ("ctrl+h", None)),
    (0x49, 9, 8, ("ctrl+i", None)),
    (0x4D, 13, 8, ("ctrl+m", None)),
    (0x4B, 11, 8, ("ctrl+k", None)),
    (0x53, 19, 0x18 | 0xA0, ("ctrl+shift+s", None)),  # Caps/Num don't change shortcut.
    (0x20, 0, 8, ("ctrl+space", None)),
    (0x20, 32, 16, ("shift+space", None)),
    (0xDB, 27, 8, ("ctrl+left_square_bracket", None)),
    (0xDC, 28, 8, ("ctrl+backslash", None)),
    (0x31, 0, 8, ("ctrl+1", None)),
    (0x09, 9, 0, ("tab", "\t")),
    (0x09, 9, 16, ("shift+tab", None)),
    (0x0D, 13, 0, ("enter", "\r")),
    (0x0D, 13, 2, ("alt+enter", None)),
    (0x08, 8, 0, ("backspace", "\x7f")),
    (0x08, 127, 8, ("ctrl+backspace", None)),
    (0x1B, 27, 0, ("escape", "\x1b")),
])
def test_translated_text_and_shortcuts_preserve_their_meaning(vk, char, modifiers, expected):
    assert keys(frame(vk, char, modifiers), split=1) == [expected]


@pytest.mark.parametrize("vk,name", [
    (0x21, "pageup"), (0x22, "pagedown"), (0x23, "end"), (0x24, "home"),
    (0x25, "left"), (0x26, "up"), (0x27, "right"), (0x28, "down"),
    (0x2D, "insert"), (0x2E, "delete"),
    *[(0x70 + index, f"f{index + 1}") for index in range(24)],
])
def test_navigation_and_function_keys(vk, name):
    assert keys(frame(vk, 0, 0)) == [(name, None)]
    assert keys(frame(vk, 0, 0x1A)) == [(f"alt+ctrl+shift+{name}", None)]


def test_defaults_releases_repeat_and_maximum_native_record():
    assert keys("\x1b[65;;97;1_") == [("a", "a")]
    assert keys(frame(0x41, 97, 0, repeat=3)) == [("a", "a")] * 3
    assert keys(frame(down=0) + frame(0x11, 0, 8) + frame(repeat=0)) == []
    assert keys(frame(0xFF, 65535, 511, scan=65535))


def test_supplementary_unicode_spans_frames_and_key_releases():
    text = frame(0, 0xD83D, 0) + frame(0, 0xD83D, 0, down=0)
    text += frame(0, 0xDE00, 0) + frame(0, 0xDE00, 0, down=0)
    assert keys(text, split=1) == [("grinning_face", "😀")]


@pytest.mark.parametrize("text", [
    frame(down=2), frame(char=65536), frame(vk=256), frame(repeat=65536),
    "\x1b[1;2;3;4;5;6;7_",
])
def test_invalid_complete_frames_do_not_type_protocol_numbers(text):
    assert keys(text + frame(0x41, 97, 0), split=1) == [("a", "a")]


def test_vt_mouse_focus_navigation_and_kitty_remain_stock():
    text = "\x1b[<0;5;7M\x1b[<0;5;7m\x1b[I\x1b[O\x1b[A\x1b[1;5D\x1b[115;6u"
    expected = list(XTermParser().feed(text))
    parser = terminal.WindowsXTermParser()
    actual = [event for character in text for event in parser.feed(character)]
    assert [type(event) for event in actual] == [type(event) for event in expected]
    assert [(event.key, event.character) for event in actual if isinstance(event, events.Key)] == [
        (event.key, event.character) for event in expected if isinstance(event, events.Key)]
    assert [(event.x, event.y, event.button) for event in actual if isinstance(event, events.MouseEvent)] == [
        (event.x, event.y, event.button) for event in expected if isinstance(event, events.MouseEvent)]


def test_bracketed_paste_does_not_execute_protocol_shaped_text():
    pasted = "Before " + frame() + " after 😀"
    text = "\x1b[200~" + pasted + "\x1b[201~"
    parser = terminal.WindowsXTermParser()
    messages = [event for character in text for event in parser.feed(character)]
    # Textual itself reissues unknown embedded ESC sequences as literal keys.
    # Keep that behavior, and never decode pasted protocol text as a shortcut.
    expected = list(XTermParser().feed(text))
    summarize = lambda items: [(type(event), getattr(event, "key", None),
                                getattr(event, "text", None)) for event in items]
    assert summarize(messages) == summarize(expected)
    assert not any(isinstance(event, events.Key) and event.key == "ctrl+shift+s"
                   for event in messages)


def test_ordinary_multiline_unicode_paste_remains_one_paste_event():
    pasted = "First line\nSecond line 😀\t中"
    parser = terminal.WindowsXTermParser()
    text = "\x1b[200~" + pasted + "\x1b[201~"
    messages = [event for character in text for event in parser.feed(character)]
    assert len(messages) == 1 and isinstance(messages[0], events.Paste)
    assert messages[0].text == pasted


@pytest.mark.skipif(os.name != "nt", reason="Windows console structures")
def test_native_surrogate_pair_can_span_readconsole_batches():
    from textual.drivers import win32

    received = []
    monitor = terminal.WindowsEventMonitor(None, None, threading.Event(), received.append)
    parser = terminal.WindowsXTermParser()
    for character in "\ud83d\ude00":
        record = win32.INPUT_RECORD()
        record.EventType = 1
        record.Event.KeyEvent.bKeyDown = True
        record.Event.KeyEvent.uChar.UnicodeChar = character
        monitor.dispatch_records([record], parser)
    assert [(event.key, event.character) for event in received] == [("grinning_face", "😀")]


@pytest.mark.skipif(os.name != "nt", reason="Windows driver lifecycle")
def test_win32_mode_is_disabled_before_normal_teardown(monkeypatch):
    from textual.drivers.windows_driver import WindowsDriver

    calls = []
    driver = object.__new__(terminal.TaskmanWindowsDriver)
    monkeypatch.setattr(driver, "write", lambda text: calls.append(text))
    monkeypatch.setattr(driver, "flush", lambda: calls.append("flush"))
    monkeypatch.setattr(WindowsDriver, "stop_application_mode", lambda self: calls.append("stock teardown"))
    driver.stop_application_mode()
    assert calls == [terminal.WIN32_INPUT_DISABLE, "flush", "stock teardown"]
