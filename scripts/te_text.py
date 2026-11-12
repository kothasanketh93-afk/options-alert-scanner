"""Mixed Telugu + English text drawing for Pillow images (added 2026-11-11, learning content in Telugu).

Pillow draws one font per call and has no font fallback, so a line is split into runs: Telugu characters use the bundled
Noto Sans Telugu (scripts/fonts, SIL Open Font License), everything else (English, digits, symbols) uses DejaVu like
the other report images. Telugu conjuncts need Pillow's raqm layout, which the standard Pillow wheels include.
"""
import os

from eod_image import _font as _latin_font
from PIL import ImageFont

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_CACHE = {}


def _is_te(ch: str) -> bool:
    return "ఀ" <= ch <= "౿" or ch in "‌‍"


def _te_font(size, bold):
    key = (size, bold)
    if key not in _CACHE:
        try:      # Telugu glyphs look small next to DejaVu at the same point size
            _CACHE[key] = ImageFont.truetype(os.path.join(_DIR, f"NotoSansTelugu-{'Bold' if bold else 'Regular'}.ttf"),
                                             round(size * 1.12))
        except OSError:
            _CACHE[key] = _latin_font(size, bold)
    return _CACHE[key]


def clean(text: str) -> str:
    """Drop emoji / pictographs (neither bundled font has them -- they drew as empty boxes, 2026-11-12)."""
    return "".join(ch for ch in str(text) if not ("\u2600" <= ch <= "\u27bf" or ch in "\ufe0f\u200b"
                                                  or ord(ch) >= 0x1F000)).replace("  ", " ")


def runs(text: str):
    """[(chunk, is_telugu)] -- spaces stick to the run before them."""
    text = clean(text)
    out = []
    for ch in text:
        te = _is_te(ch) if ch != " " else (out[-1][1] if out else False)
        if out and out[-1][1] == te:
            out[-1][0] += ch
        else:
            out.append([ch, te])
    return [(c, t) for c, t in out]


def width(d, text: str, size: int, bold: bool = False) -> float:
    return sum(d.textlength(c, font=_te_font(size, bold) if te else _latin_font(size, bold)) for c, te in runs(text))


def draw(d, xy, text: str, size: int, fill, bold: bool = False) -> float:
    """Draw mixed text with (x, y) as the top-left like d.text; every run shares one baseline. Returns the x after it."""
    x, y = xy
    base = y + _latin_font(size, bold).getmetrics()[0]
    for chunk, te in runs(text):
        f = _te_font(size, bold) if te else _latin_font(size, bold)
        d.text((x, base), chunk, font=f, fill=fill, anchor="ls")
        x += d.textlength(chunk, font=f)
    return x


def wrap(d, text: str, size: int, max_w: float, bold: bool = False) -> list:
    """Greedy word wrap to max_w pixels."""
    lines, cur = [], ""
    for word in text.split(" "):
        cand = (cur + " " + word).strip()
        if cur and width(d, cand, size, bold) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines
