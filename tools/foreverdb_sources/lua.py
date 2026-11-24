"""A strict reader for ForeverDB's addon data files, as text: never run.

The files are Lua, and a Lua file can do anything when it runs. These are
read, not run, and only one shape is accepted, the shape every data file
has today:

    -- comment lines
    local _, ns = ...
    ns.<name> = <literal>

where a literal is a string ("...", '...' or a [==[long string]==]), a
number (a leading minus allowed), true, false, nil, or a table of literals
(`[literal] = literal`, `name = literal`, or a bare literal, separated by
`,` or `;`). Anything else is refused with the line it is on: a function, a
call, a parenthesis, an operator (`..`, `+`, `#`...), a variable, a second
`local`. A refused file stops the export; nothing in it is used.

Stdlib only. tests/test_foreverdb_sources.py feeds it code it must refuse.
"""

from __future__ import annotations

import re

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_LONG_OPEN = re.compile(r"\[(=*)\[")
_SPACE = re.compile(r"[ \t\r\n]+")
_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "a": "\a", "b": "\b", "f": "\f",
        "v": "\v", "\n": "\n"}
KEYWORDS = {"true": True, "false": False, "nil": None}
HEADER = "local _, ns = ..."


class LuaRefused(ValueError):
    """The file isn't plain data: what and where."""


class Reader:
    def __init__(self, text: str, name: str = "<lua>"):
        self.s, self.i, self.name = text, 0, name
        self.comments: list[str] = []  # top-level comment lines, in order

    def line(self) -> int:
        return self.s.count("\n", 0, self.i) + 1

    def refuse(self, why: str):
        near = self.s[self.i:self.i + 30].split("\n")[0]
        raise LuaRefused(f"{self.name}:{self.line()}: {why} (near {near!r})")

    def skip(self, top: bool = False) -> None:
        """Spaces and comments; a top-level line comment is kept."""
        s = self.s
        while self.i < len(s):
            m = _SPACE.match(s, self.i)
            if m:
                self.i = m.end()
                continue
            if s.startswith("--", self.i):
                m = _LONG_OPEN.match(s, self.i + 2)
                if m:  # --[[ block comment ]]
                    end = s.find("]" + m.group(1) + "]", m.end())
                    if end < 0:
                        self.refuse("unclosed block comment")
                    self.i = end + len(m.group(1)) + 2
                    continue
                end = s.find("\n", self.i)
                end = len(s) if end < 0 else end
                if top:
                    self.comments.append(s[self.i:end].rstrip("\r"))
                self.i = end
                continue
            return

    def peek(self, text: str) -> bool:
        return self.s.startswith(text, self.i)

    def expect(self, text: str) -> None:
        if not self.peek(text):
            self.refuse(f"expected {text!r}")
        self.i += len(text)

    # --- literals
    def value(self):
        self.skip()
        s, c = self.s, self.s[self.i:self.i + 1]
        if c in ('"', "'"):
            return self.short_string(c)
        if c == "[":
            m = _LONG_OPEN.match(s, self.i)
            if not m:
                self.refuse("a bracket that isn't a long string")
            return self.long_string(m)
        if c == "{":
            return self.table()
        m = _NUMBER.match(s, self.i)
        if m and (c != "-" or not s.startswith("--", self.i)):
            self.i = m.end()
            if _NAME.match(s, self.i):
                self.refuse("a number run into a name")
            t = m.group(0)
            neg = t.startswith("-")
            body = t[1:] if neg else t
            v = int(body, 16) if body[:2].lower() == "0x" else (float(body) if re.search(r"[.eE]", body)
                                                                  else int(body))
            return -v if neg else v
        m = _NAME.match(s, self.i)
        if m and m.group(0) in KEYWORDS:
            self.i = m.end()
            return KEYWORDS[m.group(0)]
        if m:
            self.refuse(f"a name in a value ({m.group(0)}): only literals are read")
        self.refuse("not a literal")

    def short_string(self, q: str) -> str:
        s, i, out = self.s, self.i + 1, []
        while True:
            if i >= len(s):
                self.refuse("unclosed string")
            c = s[i]
            if c == q:
                self.i = i + 1
                return "".join(out)
            if c == "\n":
                self.refuse("a line break in a string")
            if c == "\\":
                n = s[i + 1:i + 2]
                if n in _ESC:
                    out.append(_ESC[n])
                    i += 2
                    continue
                m = re.match(r"\d{1,3}", s[i + 1:i + 4])
                if m:
                    out.append(chr(int(m.group(0))))
                    i += 1 + len(m.group(0))
                    continue
                self.i = i
                self.refuse("an escape this reader doesn't know")
            out.append(c)
            i += 1

    def long_string(self, m: re.Match) -> str:
        close = "]" + m.group(1) + "]"
        end = self.s.find(close, m.end())
        if end < 0:
            self.refuse("unclosed long string")
        body = self.s[m.end():end]
        self.i = end + len(close)
        # Lua drops a line break right after the opening bracket.
        if body.startswith("\r\n"):
            return body[2:]
        return body[1:] if body.startswith("\n") else body

    def table(self):
        self.expect("{")
        arr: list = []
        rec: dict = {}
        while True:
            self.skip()
            if self.peek("}"):
                self.i += 1
                break
            if self.peek("[") and not _LONG_OPEN.match(self.s, self.i):
                self.i += 1
                key = self.value()
                self.skip()
                self.expect("]")
                self.skip()
                self.expect("=")
                if key is None or isinstance(key, (dict, list)):
                    self.refuse("a table key that isn't a string or a number")
                rec[key] = self.value()
            else:
                m = _NAME.match(self.s, self.i)
                after = _SPACE.match(self.s, m.end()) if m else None
                eq = (after.end() if after else m.end()) if m else None
                if m and m.group(0) not in KEYWORDS and self.s.startswith("=", eq) and not self.s.startswith("==", eq):
                    self.i = eq + 1
                    rec[m.group(0)] = self.value()
                else:
                    arr.append(self.value())
            self.skip()
            if self.peek(",") or self.peek(";"):
                self.i += 1
                continue
            if self.peek("}"):
                continue
            self.refuse("expected ',' or '}' in a table")
        if rec and arr:
            for n, v in enumerate(arr, 1):
                if n in rec:
                    self.refuse("a table index given twice")
                rec[n] = v
            return rec
        return arr if arr or not rec else rec


def read(text: str, name: str = "<lua>") -> tuple[dict, list[str]]:
    """({ns field: value}, the top-level comment lines) of a data file, or
    LuaRefused."""
    if text.startswith("﻿"):
        text = text[1:]
    r = Reader(text, name)
    r.skip(top=True)
    if not r.peek(HEADER):
        r.refuse(f"the file doesn't start with {HEADER!r}")
    r.i += len(HEADER)
    out: dict = {}
    while True:
        r.skip(top=True)
        if r.i >= len(r.s):
            return out, r.comments
        r.expect("ns.")
        m = _NAME.match(r.s, r.i)
        if not m:
            r.refuse("expected a field name after ns.")
        r.i = m.end()
        r.skip()
        if r.peek("==") or not r.peek("="):
            r.refuse("expected '=' after the field name")
        r.i += 1
        if m.group(0) in out:
            r.refuse(f"ns.{m.group(0)} set twice")
        out[m.group(0)] = r.value()
        # One statement a line: nothing may follow a value but a comment.
        j = r.i
        while j < len(r.s) and r.s[j] in " \t\r":
            j += 1
        if j < len(r.s) and r.s[j] != "\n" and not r.s.startswith("--", j):
            r.i = j
            r.refuse("something after a value (an operator or a call?)")
