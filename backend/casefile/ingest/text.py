"""Turn email/HTML bodies into plain text, keeping text a reader would *not* see separately.

Hidden text (``display:none``, ``visibility:hidden``, zero font size, the ``hidden`` attribute,
zero-width characters) is a classic way to smuggle instructions to an AI. We keep it apart so the
Phase 3 injection detector can flag it while the evidence shown to people stays readable.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d])", re.I)
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table", "blockquote"}
VOID_TAGS = {"br", "img", "hr", "meta", "link", "input"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible: list[str] = []
        self.hidden: list[str] = []
        self._stack: list[bool] = []  # hidden flag per open element
        self._skip = 0  # inside <script>/<style>

    def _is_hidden(self) -> bool:
        return any(self._stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
            return
        attr = dict(attrs)
        hidden = "hidden" in attr or bool(HIDDEN_STYLE.search(attr.get("style") or ""))
        if tag in BLOCK_TAGS:
            (self.hidden if self._is_hidden() else self.visible).append("\n")
        if tag not in VOID_TAGS:
            self._stack.append(hidden)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = max(0, self._skip - 1)
            return
        if tag not in VOID_TAGS and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        (self.hidden if self._is_hidden() else self.visible).append(data)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def html_to_text(html: str) -> tuple[str, str]:
    """Return (visible_text, hidden_text)."""
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    return _tidy("".join(parser.visible)), _tidy("".join(parser.hidden))


def strip_zero_width(text: str) -> tuple[str, bool]:
    cleaned = ZERO_WIDTH.sub("", text)
    return cleaned, cleaned != text


def snippet(text: str, limit: int = 500) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
