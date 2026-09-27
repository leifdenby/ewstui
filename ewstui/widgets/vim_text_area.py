"""A TextArea with vim's normal / insert / visual modes, for writing emails.
The mode shows at the bottom right of its border.

Starts in INSERT (type straight away); Esc goes to NORMAL; Esc in NORMAL
is left to the screen (in the compose view: save to Drafts and close).

NORMAL:  h j k l · w b e · 0 ^ $ · gg G (counts: 3j, 2dd, …)
         x X · dd dw de D · cc cw ce C s S · yy yw ye · p P · J · u, Ctrl+R
         i a I A o O (to INSERT) · v V (to VISUAL / V-LINE)
VISUAL:  the motions extend the selection; d/x delete, y yank, c change,
         Esc or v/V back to NORMAL
Yanked and deleted text also goes to the system clipboard.
"""
from __future__ import annotations

import re

from textual import events
from textual.actions import SkipAction
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea
from textual.widgets.text_area import Selection

INSERT, NORMAL, VISUAL, VLINE = "INSERT", "NORMAL", "VISUAL", "V-LINE"
_TOKEN = re.compile(r"\w+|[^\w\s]+")
_MOTIONS = set("hjklwbe0^$G") | {"gg", "left", "right", "up", "down", "home", "end"}
# Non-printable keys handled here in NORMAL / VISUAL (the rest go to the bindings).
_OWN_KEYS = {"enter", "ctrl+r", "left", "right", "up", "down", "home", "end", "backspace", "delete"}


class VimTextArea(TextArea):
    BINDINGS = [Binding("escape", "vim_escape", "Normal mode", show=False)]

    class ModeChanged(Message):
        def __init__(self, mode: str) -> None:
            self.mode = mode
            super().__init__()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mode = INSERT
        self._pending = ""  # a count and/or operator typed so far ("3", "d", "2d", "g")
        self._register: tuple[str, bool] = ("", False)  # (text, linewise)
        self._anchor: tuple[int, int] | None = None  # visual: where it started
        self._vcursor: tuple[int, int] = (0, 0)  # visual: vim's cursor (see _update_visual)

    def on_mount(self) -> None:
        self._show_mode()

    # -- modes ----------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        if mode != self.mode:
            history = getattr(self, "history", None)
            if history is not None:
                history.checkpoint()  # an insert session is one undo step
        changed = mode != self.mode
        self.mode, self._pending = mode, ""
        if mode not in (VISUAL, VLINE):
            self._anchor = None
            row, col = self.cursor_location
            self.selection = Selection.cursor((row, col))
        self._show_mode()
        if changed:
            self.post_message(self.ModeChanged(mode))

    def _show_mode(self) -> None:
        label = f"-- {self.mode} --" if self.mode in (INSERT, VISUAL, VLINE) else self.mode
        self.border_subtitle = f"{label} {self._pending}".rstrip()

    def action_vim_escape(self) -> None:
        if self.mode == INSERT:
            row, col = self.cursor_location
            self._set_mode(NORMAL)
            self.move_cursor((row, max(0, col - 1)))  # vim steps back off the inserted text
        elif self.mode in (VISUAL, VLINE):
            cursor = self._vcursor
            self._set_mode(NORMAL)
            self.move_cursor(cursor)
        elif self._pending:
            self._set_mode(NORMAL)
        else:
            raise SkipAction()  # NORMAL: Esc is the screen's (compose: save & close)

    def check_consume_key(self, key: str, character: str | None = None) -> bool:
        if self.mode != INSERT:
            return character is not None and character.isprintable()  # commands, not text
        return super().check_consume_key(key, character)

    # -- the document as one string (motions work on offsets) ------------------------

    def _lines(self) -> list[str]:
        return self.document.lines

    def _offset(self, loc: tuple[int, int]) -> int:
        lines = self._lines()
        return sum(len(line) + 1 for line in lines[: loc[0]]) + loc[1]

    def _location(self, offset: int) -> tuple[int, int]:
        for row, line in enumerate(self._lines()):
            if offset <= len(line):
                return row, offset
            offset -= len(line) + 1
        lines = self._lines()
        return len(lines) - 1, len(lines[-1])

    def _last_col(self, row: int) -> int:
        """The last column the cursor can be on in NORMAL (on a character)."""
        return max(0, len(self._lines()[row]) - 1)

    # -- motions ------------------------------------------------------------------

    def _motion(self, key: str, count: int, origin: tuple[int, int] | None = None) -> tuple[int, int]:
        row, col = origin if origin is not None else self.cursor_location
        lines = self._lines()
        if key in ("h", "left"):
            return row, max(0, col - count)
        if key in ("l", "right"):
            return row, min(self._last_col(row), col + count)
        if key in ("j", "down", "k", "up"):
            row = min(len(lines) - 1, row + count) if key in ("j", "down") else max(0, row - count)
            return row, min(col, self._last_col(row))
        if key in ("0", "home"):
            return row, 0
        if key == "^":
            line = lines[row]
            return row, len(line) - len(line.lstrip())
        if key in ("$", "end"):
            return row, self._last_col(row)
        if key == "gg":
            return 0, 0
        if key == "G":
            return len(lines) - 1, 0
        text, offset = self.text, self._offset((row, col))
        tokens = list(_TOKEN.finditer(text))
        for _ in range(count):
            if key == "w":
                offset = next((t.start() for t in tokens if t.start() > offset), len(text))
            elif key == "b":
                offset = next((t.start() for t in reversed(tokens) if t.start() < offset), 0)
            elif key == "e":
                offset = next((t.end() - 1 for t in tokens if t.end() - 1 > offset), max(0, len(text) - 1))
        return self._location(offset)

    # -- keys -----------------------------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        if self.mode == INSERT:
            await super()._on_key(event)
            return
        if not event.is_printable and event.key not in _OWN_KEYS:
            return  # Esc, Ctrl+S, Tab…: left to the bindings (ours, the TextArea's, the screen's)
        event.stop()
        event.prevent_default()
        key = event.character if event.is_printable and event.character else event.key
        key = {"backspace": "h", "delete": "x"}.get(key, key)  # (not deleting text in NORMAL)
        if key == "ctrl+r":
            self.redo()
            return
        if self.mode in (VISUAL, VLINE):
            self._visual_key(key)
        else:
            self._normal_key(key)
        self._show_mode()

    def _parse_pending(self) -> tuple[int, str, bool]:
        """What's been typed so far: (count, operator "d"/"c"/"y"/"", a "g"
        waiting for its second g). "2d3" is a count of 6 with d."""
        m = re.fullmatch(r"(\d*)([dcy]?)(\d*)(g?)", self._pending)
        if not m:
            return 1, "", False
        return int(m[1] or 1) * int(m[3] or 1), m[2], bool(m[4])

    def _counting(self, key: str) -> bool:
        """A digit that's part of a count ("0" only after other digits: on
        its own it's the start-of-line motion)."""
        return key.isdigit() and (key != "0" or self._pending[-1:].isdigit())

    def _normal_key(self, key: str) -> None:
        if self._counting(key):
            self._pending += key
            return
        count, op, g = self._parse_pending()
        if g:  # the second g of gg (dgg, ygg too); anything else cancels
            if key != "g":
                self._pending = ""
                return
            key = "gg"
        elif key == "g":
            self._pending += "g"
            return
        if op:
            self._operator(op, key, count)
            return
        self._pending = ""
        row, col = self.cursor_location
        if key in _MOTIONS:
            self.move_cursor(self._motion(key, count))
        elif key in ("d", "c", "y"):
            self._pending = f"{count if count > 1 else ''}{key}"  # the motion (or dd/cc/yy) comes next
        elif key == "x":
            end = min(len(self._lines()[row]), col + count)
            self._cut((row, col), (row, end), linewise=False)
        elif key == "X":
            start = max(0, col - count)
            self._cut((row, start), (row, col), linewise=False)
        elif key == "D":
            self._cut((row, col), (row, len(self._lines()[row])), linewise=False)
        elif key == "C":
            self._cut((row, col), (row, len(self._lines()[row])), linewise=False)
            self._set_mode(INSERT)
        elif key == "s":
            self._cut((row, col), (row, min(len(self._lines()[row]), col + count)), linewise=False)
            self._set_mode(INSERT)
        elif key == "S":
            self._change_lines(row, count)
        elif key == "p":
            self._put(after=True)
        elif key == "P":
            self._put(after=False)
        elif key == "J":  # (vim: 3J joins three lines, i.e. two joins)
            for _ in range(max(1, count - 1)):
                self._join()
        elif key == "u":
            for _ in range(count):
                self.undo()
            r, c = self.cursor_location
            self.move_cursor((r, min(c, self._last_col(r))))
        elif key in ("i", "a", "I", "A", "o", "O"):
            self._enter_insert(key)
        elif key in ("v", "V"):
            self._anchor = self._vcursor = self.cursor_location
            self._set_mode(VISUAL if key == "v" else VLINE)
            self._update_visual()
        elif key == "enter":
            self.move_cursor(self._motion("j", count))

    def _enter_insert(self, key: str) -> None:
        row, col = self.cursor_location
        line = self._lines()[row]
        if key == "a" and line:
            self.move_cursor((row, col + 1))
        elif key == "I":
            self.move_cursor((row, len(line) - len(line.lstrip())))
        elif key == "A":
            self.move_cursor((row, len(line)))
        elif key == "o":
            self.insert("\n", (row, len(line)))
            self.move_cursor((row + 1, 0))
        elif key == "O":
            self.insert("\n", (row, 0))
            self.move_cursor((row, 0))
        self._set_mode(INSERT)

    # -- operators -----------------------------------------------------------------

    def _operator(self, op: str, key: str, count: int) -> None:
        self._pending = ""
        row, col = self.cursor_location
        if key == op:  # dd / cc / yy: whole lines
            if op == "c":
                self._change_lines(row, count)
                return
            last = min(len(self._lines()) - 1, row + count - 1)
            self._yank_or_cut_lines(row, last, cut=op == "d")
            return
        if key not in _MOTIONS:
            return
        if op == "c" and key == "w":
            key = "e"  # vim: cw changes to the end of the word
        target = self._motion(key, count)
        start, end = sorted([(row, col), target])
        if key in ("e", "$", "end"):  # inclusive motions
            end = (end[0], min(len(self._lines()[end[0]]), end[1] + 1))
        if key in ("j", "k", "down", "up", "G", "gg"):  # linewise motions
            if op == "c":
                self._change_lines(start[0], end[0] - start[0] + 1)
            else:
                self._yank_or_cut_lines(start[0], end[0], cut=op == "d")
            return
        if op == "y":
            self._yank(self.get_text_range(start, end), linewise=False)
            self.move_cursor(start)
            return
        self._cut(start, end, linewise=False)
        if op == "c":
            self._set_mode(INSERT)

    def _yank(self, text: str, linewise: bool) -> None:
        self._register = (text, linewise)
        try:
            self.app.copy_to_clipboard(text)
        except Exception:  # noqa: BLE001 - the terminal may not support it
            pass

    def _cut(self, start, end, linewise: bool) -> None:
        if start == end:
            return
        self._yank(self.get_text_range(start, end), linewise)
        self.delete(start, end)
        r, c = start
        self.move_cursor((r, min(c, self._last_col(r))))

    def _yank_or_cut_lines(self, first: int, last: int, cut: bool) -> None:
        lines = self._lines()
        text = "\n".join(lines[first:last + 1]) + "\n"
        self._yank(text, linewise=True)
        if not cut:
            self.move_cursor((first, self.cursor_location[1] if first == self.cursor_location[0] else 0))
            return
        if last + 1 < len(lines):
            self.delete((first, 0), (last + 1, 0))
        elif first > 0:  # the last lines: take the newline before them
            self.delete((first - 1, len(lines[first - 1])), (last, len(lines[last])))
            first -= 1
        else:
            self.delete((0, 0), (last, len(lines[last])))
        row = min(first, len(self._lines()) - 1)
        line = self._lines()[row]
        self.move_cursor((row, len(line) - len(line.lstrip())))

    def _change_lines(self, row: int, count: int) -> None:
        lines = self._lines()
        last = min(len(lines) - 1, row + count - 1)
        self._yank("\n".join(lines[row:last + 1]) + "\n", linewise=True)
        indent = lines[row][: len(lines[row]) - len(lines[row].lstrip())]
        self.replace(indent, (row, 0), (last, len(lines[last])))
        self.move_cursor((row, len(indent)))
        self._set_mode(INSERT)

    def _put(self, after: bool) -> None:
        text, linewise = self._register
        if not text:
            return
        row, col = self.cursor_location
        if linewise:
            body = text[:-1] if text.endswith("\n") else text
            if after:
                self.insert("\n" + body, (row, len(self._lines()[row])))
                self.move_cursor((row + 1, 0))
            else:
                self.insert(body + "\n", (row, 0))
                self.move_cursor((row, 0))
            return
        at = (row, col + 1) if after and self._lines()[row] else (row, col)
        self.insert(text, at)
        self.move_cursor(self._location(self._offset(at) + len(text) - 1))

    def _join(self) -> None:
        row, _ = self.cursor_location
        lines = self._lines()
        if row + 1 >= len(lines):
            return
        left, right = lines[row].rstrip(), lines[row + 1].lstrip()
        joined_at = len(left)
        self.replace(left + (" " if left and right else "") + right, (row, 0), (row + 1, len(lines[row + 1])))
        self.move_cursor((row, joined_at))

    # -- visual ---------------------------------------------------------------------

    # In VISUAL the selection's end is past the character under vim's cursor
    # (the TextArea cursor sits there), so vim's cursor is kept apart: _vcursor.

    def _update_visual(self) -> None:
        cursor = self._vcursor
        if self.mode == VLINE:
            first, last = sorted([self._anchor[0], cursor[0]])
            start, end = (first, 0), (last, len(self._lines()[last]))
            self.selection = Selection(start, end) if cursor[0] >= self._anchor[0] else Selection(end, start)
            return
        start, end = sorted([self._anchor, cursor])
        end = (end[0], min(len(self._lines()[end[0]]), end[1] + 1))  # the character under the cursor is in
        self.selection = Selection(start, end) if cursor >= self._anchor else Selection(end, start)

    def _visual_range(self):
        cursor = self._vcursor
        if self.mode == VLINE:
            first, last = sorted([self._anchor[0], cursor[0]])
            return first, last
        start, end = sorted([self._anchor, cursor])
        return start, (end[0], min(len(self._lines()[end[0]]), end[1] + 1))

    def _visual_key(self, key: str) -> None:
        if self._counting(key):
            self._pending += key
            return
        count, _, g = self._parse_pending()
        self._pending = ""
        if g:
            if key != "g":
                return
            key = "gg"
        elif key == "g":
            self._pending = "g"
            return
        if key in _MOTIONS:
            self._vcursor = self._motion(key, count, self._vcursor)
            self._update_visual()
            return
        if key in ("v", "V"):
            wanted = VISUAL if key == "v" else VLINE
            if wanted == self.mode:
                cursor = self._vcursor
                self._set_mode(NORMAL)
                self.move_cursor(cursor)
            else:
                self.mode = wanted
                self._update_visual()
            return
        if key not in ("d", "x", "y", "c"):
            return
        if self.mode == VLINE:
            first, last = self._visual_range()
            self._set_mode(NORMAL)
            if key == "c":
                self._change_lines(first, last - first + 1)
            else:
                self._yank_or_cut_lines(first, last, cut=key != "y")
            return
        start, end = self._visual_range()
        self._set_mode(NORMAL)
        if key == "y":
            self._yank(self.get_text_range(start, end), linewise=False)
            self.move_cursor(start)
            return
        self._cut(start, end, linewise=False)
        if key == "c":
            self._set_mode(INSERT)
