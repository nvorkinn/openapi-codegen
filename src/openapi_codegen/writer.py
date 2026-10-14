"""A small line-oriented emitter for Python source, plus the formatting helpers the generators share.

It lays code out the way `ruff format` would (double quotes, trailing commas, 120 columns), so generated
files need no post-processing."""

import builtins
import json
import re
import textwrap
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

WIDTH = 120
INDENT = "    "


class Raw(str):
    """Source text to emit as it is, inside a literal."""


class Arg(NamedTuple):
    """A function argument as source, with the lint rules it breaks that are fine here (`# noqa` codes)."""

    text: str
    noqa: tuple[str, ...] = ()


def argument(text: str, name: str, *, positional_bool: bool = False) -> Arg:
    """An `Arg` for the parameter `name`, noting the lint rules the generated signature can't avoid."""
    codes = []
    if positional_bool:
        codes.append("FBT001")
    if hasattr(builtins, name):
        codes.append("A002")
    return Arg(text, tuple(codes))


def quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def inline(value: Any) -> str:
    """`value` as a one-line Python literal."""
    return _flat(value)


def _flat(value: Any) -> str:
    if isinstance(value, Raw):
        return str(value)
    if isinstance(value, str):
        return quote(value)
    if value is None or isinstance(value, bool | int | float):
        return repr(value)
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{_flat(k)}: {_flat(v)}" for k, v in value.items()) + "}"
    if isinstance(value, tuple):
        inner = ", ".join(_flat(v) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    if isinstance(value, Sequence):
        return "[" + ", ".join(_flat(v) for v in value) + "]"
    raise TypeError(f"can't write {value!r} as a literal")


def literal(value: Any, indent: int, column: int, trailing: int = 0) -> str:
    """`value` as source. `indent` is the column of the statement it is in and `column` where the literal
    starts on its first line; a value that doesn't fit (with `trailing` characters after it) is broken one
    element per line."""
    flat = _flat(value)
    if column + len(flat) + trailing <= WIDTH or isinstance(value, Raw | str) or not value:
        return flat
    pad = " " * (indent + 4)
    if isinstance(value, Mapping):
        rows = []
        for key, item in value.items():
            head = f"{pad}{_flat(key)}: "
            rows.append(f"{head}{literal(item, indent + 4, len(head))},")
        opening, closing = "{", "}"
    else:
        rows = [f"{pad}{literal(item, indent + 4, len(pad))}," for item in value]
        opening, closing = ("(", ")") if isinstance(value, tuple) else ("[", "]")
    return opening + "\n" + "\n".join(rows) + "\n" + " " * indent + closing


def string_expr(text: str, indent: int, column: int) -> str:
    """A string literal, split into adjacent literals inside parentheses when it would run past the margin."""
    flat = quote(text)
    if column + len(flat) <= WIDTH:
        return flat
    pad = " " * (indent + 4)
    chunks = textwrap.wrap(text, width=WIDTH - len(pad) - 3, drop_whitespace=False, break_long_words=False)
    if len(chunks) < 2:
        return flat
    return "(\n" + "\n".join(f"{pad}{quote(chunk)}" for chunk in chunks) + "\n" + " " * indent + ")"


_SUMMARY_WIDTH = 88
_SENTENCE = re.compile(r"(.+?\.)(?:\s+|$)")


def split_summary(text: str, fallback: str) -> tuple[str, str]:
    """A docstring's one-line summary for `text`, and what is left of `text` to follow it.

    The summary is the first sentence when that fits on a line and reads as a summary (it ends with a period and
    doesn't start with "This"); otherwise it is `fallback` and all of `text` follows. Pydocstyle wants the summary
    alone on the first line, in the imperative for a function, so spec text can't always be one."""
    words = " ".join(text.split())
    match = _SENTENCE.match(words)
    if match is None and words and words[-1] not in ".!?":
        words += "."
        match = _SENTENCE.match(words)
    if match is None:
        return fallback, words
    sentence = match.group(1)
    sentence = sentence[:1].upper() + sentence[1:]
    if len(sentence) > _SUMMARY_WIDTH or sentence.startswith("This ") or not sentence[:1].isalpha():
        return fallback, words
    return sentence, words[match.end() :].strip()


def docstring_lines(parts: Sequence[str | tuple[str, ...]], indent: int) -> list[str]:
    """A docstring from `parts`: each string is a paragraph, re-wrapped to fit; each tuple is lines to keep
    as they are (an `Args:` list, say). The first part is the summary and should be a single line."""
    raw = any("\\" in part for item in parts for part in ((item,) if isinstance(item, str) else item))
    width = WIDTH - indent - 6
    blocks: list[list[str]] = []
    for part in parts:
        if isinstance(part, tuple):
            blocks.append([_escape(row, raw=raw) for row in part])
        elif part.strip():
            for paragraph in part.strip().split("\n\n"):
                words = _escape(" ".join(paragraph.split()), raw=raw)
                blocks.append(textwrap.wrap(words, width=min(100, width), break_long_words=False) or [""])
    rows = [row for index, block in enumerate(blocks) for row in ([""] if index else []) + block]
    prefix = 'r"""' if raw else '"""'
    if len(rows) == 1 and len(rows[0]) <= width:
        return [f'{prefix}{rows[0]}"""']
    return [prefix + rows[0], *rows[1:], '"""']


def _escape(text: str, *, raw: bool) -> str:
    if raw:
        # A raw string can't escape a quote: end with a character that isn't one, and avoid a closing run.
        text = text.replace('"""', "'''")
        return text + " ." if text.endswith('"') else text
    text = text.replace('"""', '\\"\\"\\"')
    return text[:-1] + '\\"' if text.endswith('"') else text


def import_lines(module: str, names: Iterable[str]) -> list[str]:
    ordered = sorted(set(names), key=_import_key)
    one_line = f"from {module} import {', '.join(ordered)}"
    if len(one_line) <= WIDTH:
        return [one_line]
    return [f"from {module} import (", *(f"{INDENT}{name}," for name in ordered), ")"]


def _import_key(name: str) -> tuple[int, str]:
    # isort's default order: CONSTANTS, then Classes, then functions and variables.
    if len(name) > 1 and name.isupper():
        return (0, name)
    return (1 if name[:1].isupper() else 2, name)


class CodeWriter:
    def __init__(self) -> None:
        self._lines: list[str] = []
        self.level = 0

    @property
    def column(self) -> int:
        return self.level * len(INDENT)

    def line(self, text: str = "") -> None:
        self._lines.append(f"{INDENT * self.level}{text}" if text else "")

    def raw(self, text: str) -> None:
        """Lines whose indentation is already absolute (what `literal` and `string_expr` return)."""
        self._lines.extend(text.split("\n"))

    def blank(self, count: int = 1) -> None:
        if not self._lines:
            return
        trailing = 0
        for existing in reversed(self._lines):
            if existing:
                break
            trailing += 1
        self._lines.extend([""] * max(0, count - trailing))

    def statement(self, prefix: str, value: Any) -> None:
        """`prefix` followed by `value` as a literal, e.g. `statement("_X = ", {...})`."""
        self.raw(f"{INDENT * self.level}{prefix}{literal(value, self.column, self.column + len(prefix))}")

    def signature(self, name: str, arguments: Sequence[str | Arg], returns: str, noqa: Sequence[str] = ()) -> None:
        """A `def` line: on one line when it fits, else one argument per line. Trailing `# noqa` comments go on
        the line each rule is reported on: `noqa` for the function, `Arg.noqa` for an argument."""
        args = [item if isinstance(item, Arg) else Arg(item) for item in arguments]
        header = f"def {name}({', '.join(a.text for a in args)}) -> {returns}:"
        if self.column + len(header) <= WIDTH:
            codes = sorted({*noqa, *(code for a in args for code in a.noqa)})
            self.line(header + _noqa(codes))
            return
        self.line(f"def {name}(" + _noqa(sorted(noqa)))
        for a in args:
            self.line(f"{INDENT}{a.text}," + _noqa(a.noqa))
        self.line(f") -> {returns}:")

    def argument(self, name: str, value: Any) -> None:
        """`name=value,` as one line of a call's arguments, broken up when it doesn't fit."""
        prefix = f"{name}="
        self.raw(f"{INDENT * self.level}{prefix}{literal(value, self.column, self.column + len(prefix), 1)},")

    def docstring(self, *parts: str | tuple[str, ...]) -> None:
        for row in docstring_lines(parts, self.column):
            self.line(row)

    def block(self, header: str | None = None) -> _Block:
        """`with w.block("class A:"):` writes the header and indents what follows (no header: only indents)."""
        if header is not None:
            self.line(header)
        return _Block(self)

    def getvalue(self) -> str:
        while self._lines and not self._lines[-1]:
            self._lines.pop()
        return "\n".join(self._lines) + "\n"


def _noqa(codes: Iterable[str]) -> str:
    ordered = sorted(set(codes))
    return f"  # noqa: {', '.join(ordered)}" if ordered else ""


class _Block:
    def __init__(self, writer: CodeWriter) -> None:
        self._writer = writer

    def __enter__(self) -> None:
        self._writer.level += 1

    def __exit__(self, *exc: object) -> None:
        self._writer.level -= 1
