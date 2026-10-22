"""
NexLink Core (NLC) palette and SVG building blocks shared by the profile card
generators. Everything renders inside <img> on GitHub, so: no JS, no web fonts,
CSS/SMIL animation only.
"""

from __future__ import annotations

from html import escape

# Palette (exact NLC hex values)
PRIMARY = "#0a51c2"
PRIMARY_LIGHT = "#3b7de6"
PRIMARY_DARK = "#083d94"
SECONDARY = "#2bc56b"
SECONDARY_LIGHT = "#4ed88a"
SECONDARY_DARK = "#22a057"
ACCENT = "#18a099"
ACCENT_LIGHT = "#1fc2b9"
ACCENT_DARK = "#138580"

BG_DEFAULT = "#1A1A1E"
BG_PAPER = "#16181D"
BG_SECONDARY = "#25262B"
BG_TERTIARY = "#2C2E33"
SURFACE = "#1F2937"

TEXT = "#E5E7EB"
TEXT_MUTED = "#9CA3AF"
SUCCESS = "#10B981"
ERROR = "#EF4444"
WARNING = "#F59E0B"
INFO = "#06B6D4"
DIVIDER = "#374151"

SANS = "Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
HEADING = "Poppins, Inter, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', 'Cascadia Code', Consolas, 'Courier New', monospace"

BASE_CSS = f"""
  text {{ font-family: {SANS}; }}
  .mono {{ font-family: {MONO}; }}
  .h {{ font-family: {HEADING}; font-weight: 600; fill: {TEXT}; }}
  .muted {{ fill: {TEXT_MUTED}; }}
  .fade-in {{ animation: fadeIn 0.3s ease-in-out both; }}
  .slide-up {{ animation: slideUp 0.4s ease-out both; }}
  @keyframes fadeIn {{ 0% {{ opacity: 0; }} 100% {{ opacity: 1; }} }}
  @keyframes slideUp {{ 0% {{ transform: translateY(20px); opacity: 0; }} 100% {{ transform: translateY(0); opacity: 1; }} }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
  .pulse-slow {{ animation: pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite; }}
  @media (prefers-reduced-motion: reduce) {{
    * {{ animation: none !important; }}
  }}
"""


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def fmt(num: int | float) -> str:
    """1234 -> 1.2k, 1208855 -> 1.2M."""
    num = float(num)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(num) >= div:
            val = num / div
            return f"{val:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{int(num)}"


def background(w: int, h: int, uid: str = "nlc") -> str:
    """The NLC signature background: base color, 60px grid, three brand glows.

    Mirrors the mandatory `body` CSS: grid lines rgba(255,255,255,.02) every 60px,
    ellipse 1000x600 @ 15% 0% blue .12, 800x500 @ 85% 0% green .08,
    600x400 @ 50% 100% teal .06.
    """
    glows = [
        ("g1", 0.15 * w, 0, 1000, 600, "10,81,194", 0.12),
        ("g2", 0.85 * w, 0, 800, 500, "43,197,107", 0.08),
        ("g3", 0.50 * w, h, 600, 400, "24,160,153", 0.06),
    ]
    defs = [
        f'<pattern id="{uid}-grid" width="60" height="60" patternUnits="userSpaceOnUse">'
        f'<path d="M0 0.5H60M0.5 0V60" stroke="#ffffff" stroke-opacity="0.02" stroke-width="1" fill="none"/>'
        f"</pattern>"
    ]
    shapes = []
    for gid, cx, cy, rx, ry, rgb, alpha in glows:
        defs.append(
            f'<radialGradient id="{uid}-{gid}">'
            f'<stop offset="0" stop-color="rgb({rgb})" stop-opacity="{alpha}"/>'
            f'<stop offset="1" stop-color="rgb({rgb})" stop-opacity="0"/>'
            f"</radialGradient>"
        )
        shapes.append(
            f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx}" ry="{ry}" fill="url(#{uid}-{gid})"/>'
        )
    return (
        f'<defs>{"".join(defs)}<clipPath id="{uid}-clip"><rect width="{w}" height="{h}" rx="12"/></clipPath></defs>'
        f'<g clip-path="url(#{uid}-clip)">'
        f'<rect width="{w}" height="{h}" fill="{BG_DEFAULT}"/>'
        f'<rect width="{w}" height="{h}" fill="url(#{uid}-grid)"/>'
        f'{"".join(shapes)}'
        f"</g>"
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" fill="none" stroke="{DIVIDER}"/>'
    )


def svg(w: int, h: int, body: str, css: str = "", title: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{esc(title)}">'
        f"<title>{esc(title)}</title>"
        f"<style>{BASE_CSS}{css}</style>"
        f"{body}</svg>\n"
    )


def chip(x: float, y: float, label: str, color: str = SUCCESS, fill: str = "rgba(4,120,87,0.1)",
         stroke: str = "rgba(16,185,129,0.3)", char_w: float = 7.2) -> tuple[str, float]:
    """Pill badge. Returns (svg, width)."""
    w = len(label) * char_w + 28
    return (
        f'<g transform="translate({x},{y})">'
        f'<rect width="{w:.1f}" height="26" rx="13" fill="{fill}" stroke="{stroke}"/>'
        f'<circle cx="13" cy="13" r="3.5" fill="{color}" class="pulse-slow"/>'
        f'<text x="22" y="17.5" font-size="11.5" font-weight="600" fill="{color}" letter-spacing="0.05em">{esc(label)}</text>'
        f"</g>",
        w,
    )
