"""BBCode rendering for user pages ("me!" sections).

Follows Shiina-Web's approach: the raw BBCode is HTML-escaped and processed
server-side into safe HTML; both raw and html are stored on the user row.
The `bbcode` library escapes HTML in text by default, so rendered output is
XSS-safe. Supported tags: the library defaults (b, i, u, s, sub, sup, hr,
quote, code, list/*, center, color, url) plus [img], [size] and the British
[centre] spelling that osu! players are used to.
"""

from __future__ import annotations

import bbcode

MAX_PAGE_LENGTH = 10_000  # same cap as Shiina-Web


def _render_img(tag_name, value, options, parent, context):  # noqa: ANN001, ARG001
    url = (value or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    url = url.replace('"', "%22")
    return f'<img src="{url}" alt="" loading="lazy" />'


def _render_size(tag_name, value, options, parent, context):  # noqa: ANN001, ARG001
    try:
        size = int(options.get("size", "100"))
    except (TypeError, ValueError):
        size = 100
    size = max(30, min(size, 200))
    return f'<span style="font-size:{size}%">{value}</span>'


def _build_parser() -> bbcode.Parser:
    parser = bbcode.Parser()
    parser.add_formatter("img", _render_img, replace_links=False, replace_cosmetic=False)
    parser.add_formatter("size", _render_size)
    parser.add_simple_formatter("centre", '<div style="text-align:center">%(value)s</div>')
    return parser


_parser = _build_parser()


def render_user_page(raw: str) -> str:
    """Render raw BBCode into safe HTML."""
    return _parser.format(raw)
