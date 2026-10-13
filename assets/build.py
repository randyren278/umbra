#!/usr/bin/env python3
"""Regenerates Umbra's artwork: logo.svg, icon.svg, banner.svg, how-it-works.svg and
AppIcon.icns (needs rsvg-convert and iconutil). Run from anywhere; writes beside this file."""
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SANS = "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
INK, PAPER, MUTED, DIM = "#07060d", "#f4efe6", "#b9b3c9", "#8e86a8"

DEFS = """
  <radialGradient id="corona" cx="0" cy="0" r="230" gradientUnits="userSpaceOnUse">
    <stop offset="0.60" stop-color="#ffe7b0" stop-opacity="0"/>
    <stop offset="0.655" stop-color="#fff1d2" stop-opacity="1"/>
    <stop offset="0.70" stop-color="#f6b26b" stop-opacity="0.7"/>
    <stop offset="0.82" stop-color="#c06bd6" stop-opacity="0.22"/>
    <stop offset="1" stop-color="#7c5cf6" stop-opacity="0"/>
  </radialGradient>
  <radialGradient id="moon" cx="-40" cy="-50" r="190" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#1c1830"/>
    <stop offset="1" stop-color="#07060d"/>
  </radialGradient>
  <radialGradient id="bead" cx="0" cy="0" r="46" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#ffffff"/>
    <stop offset="0.18" stop-color="#fff4dc"/>
    <stop offset="0.45" stop-color="#ffd08a" stop-opacity="0.45"/>
    <stop offset="1" stop-color="#ffb35c" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#1b1730"/>
    <stop offset="1" stop-color="#07060d"/>
  </linearGradient>"""


def mark(x, y, size):
    """The eclipse: corona, compass ticks, the moon (with Safari's needle in its shadow) and
    the diamond-ring bead, drawn in a 512 box centred on (x, y) and scaled to size."""
    ticks = []
    for i in range(24):
        major = i % 6 == 0
        inner, outer = (172, 192) if major else (178, 186)
        ticks.append(f'<line x1="0" y1="-{inner}" x2="0" y2="-{outer}" transform="rotate({i * 15})" '
                     f'stroke="#fff1d2" stroke-opacity="{0.55 if major else 0.28}" stroke-width="{3 if major else 2}" stroke-linecap="round"/>')
    return f"""
  <g transform="translate({x} {y}) scale({size / 512})">
    <circle r="230" fill="url(#corona)"/>
    {''.join(ticks)}
    <circle r="150" fill="#fff4dc"/>
    <circle cx="-7" cy="7" r="149" fill="url(#moon)"/>
    <g transform="translate(-7 7) rotate(45)" opacity="0.9">
      <path d="M0 -78 L12 0 L-12 0 Z" fill="#ff6b6b" fill-opacity="0.22"/>
      <path d="M0 78 L12 0 L-12 0 Z" fill="#ffffff" fill-opacity="0.10"/>
      <circle r="4" fill="#ffffff" fill-opacity="0.18"/>
    </g>
    <g transform="translate(103 -103)">
      <circle r="46" fill="url(#bead)"/>
      <path d="M0 -30 L2.2 -2.2 L30 0 L2.2 2.2 L0 30 L-2.2 2.2 L-30 0 L-2.2 -2.2 Z" fill="#ffffff" fill-opacity="0.9"/>
      <circle r="6" fill="#ffffff"/>
    </g>
  </g>"""


def stars(width, height, count, seed, avoid=None):
    rng = random.Random(seed)
    dots = []
    while len(dots) < count:
        sx, sy = rng.uniform(16, width - 16), rng.uniform(16, height - 16)
        if avoid and (sx - avoid[0]) ** 2 + (sy - avoid[1]) ** 2 < avoid[2] ** 2:
            continue
        dots.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{rng.choice([0.8, 1, 1.2, 1.6])}" '
                    f'fill="#ffffff" fill-opacity="{rng.uniform(0.15, 0.6):.2f}"/>')
    return "".join(dots)


def svg(width, height, title, desc, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-labelledby="title desc">
  <title id="title">{title}</title>
  <desc id="desc">{desc}</desc>
  <defs>{DEFS}
  </defs>{body}
</svg>
"""


def logo():
    return svg(512, 512, "Umbra", "A total eclipse: a dark disc rimmed by a glowing corona with compass ticks, "
               "one bright diamond-ring point at its upper right, and a faint compass needle inside the shadow.",
               mark(256, 256, 512))


def icon():
    return svg(1024, 1024, "Umbra app icon", "The Umbra eclipse on a deep indigo rounded square.",
               f"""
  <rect x="100" y="100" width="824" height="824" rx="185" fill="url(#sky)"/>
  <rect x="100.5" y="100.5" width="823" height="823" rx="184.5" fill="none" stroke="#ffffff" stroke-opacity="0.08"/>
  <g>{stars(824, 824, 26, 7, avoid=(412, 412, 330)).replace('cx="', 'data-x="').replace('data-x="', 'cx="')}</g>
  {mark(512, 512, 700)}""".replace("<g><circle", '<g transform="translate(100 100)"><circle', 1))


def banner():
    return svg(1280, 400, "Umbra: a headless WebKit browser for macOS",
               "The Umbra eclipse beside the wordmark umbra, the line 'A headless WebKit browser for macOS' "
               "and the commands read, fetch, download and sign-in, on a night sky.",
               f"""
  <rect width="1280" height="400" rx="28" fill="url(#sky)"/>
  {stars(1280, 400, 90, 3, avoid=(220, 200, 190))}
  {mark(220, 200, 330)}
  <text x="430" y="196" font-family="{SANS}" font-size="124" font-weight="600" letter-spacing="4" fill="{PAPER}">umbra</text>
  <text x="434" y="252" font-family="{SANS}" font-size="32" fill="{MUTED}">A headless WebKit browser for macOS.</text>
  <text x="434" y="296" font-family="{SANS}" font-size="32" fill="{MUTED}">Signed-in web work, done out of sight.</text>
  <text x="435" y="346" font-family="{MONO}" font-size="22" fill="{DIM}">read · fetch · download · sign-in</text>""")


def box(x, y, w, h, heading, lines, accent="#f6b26b", mono_heading=False):
    font = MONO if mono_heading else SANS
    rows = "".join(f'<text x="{x + 24}" y="{y + 78 + i * 30}" font-family="{SANS}" font-size="20" fill="{MUTED}">{line}</text>'
                   for i, line in enumerate(lines))
    return f"""
  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="#ffffff" fill-opacity="0.04" stroke="#ffffff" stroke-opacity="0.14"/>
  <rect x="{x}" y="{y + 18}" width="4" height="30" rx="2" fill="{accent}"/>
  <text x="{x + 24}" y="{y + 42}" font-family="{font}" font-size="24" font-weight="600" fill="{PAPER}">{heading}</text>{rows}"""


def arrow(x1, y1, x2, y2):
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2 - 10}" y2="{y2}" stroke="#f6b26b" stroke-opacity="0.8" stroke-width="2.5"/>'
            f'<path d="M{x2} {y2} l-14 -7 v14 z" fill="#f6b26b" fill-opacity="0.9"/>')


def diagram():
    return svg(1280, 600, "How Umbra works",
               "Your scripts call the umbra command. Umbra loads pages in an off-screen WebKit view that uses its own "
               "cookie store, signed in once, and returns text, JSON or files. Safari is never opened or shared.",
               f"""
  <rect width="1280" height="600" rx="28" fill="url(#sky)"/>
  {stars(1280, 600, 40, 11)}
  <text x="56" y="70" font-family="{SANS}" font-size="30" font-weight="600" fill="{PAPER}">How it works</text>
  {box(56, 110, 330, 300, "Your jobs", ["FIRST: Instagram story links", "FIRST: LinkedIn apply links", "Canvas API and course files", "Kaltura lecture pages", "anything behind a sign-in"])}
  {arrow(386, 260, 470, 260)}
  {box(476, 110, 330, 300, "umbra", ["read   page + async script", "fetch  same-origin GET", "download   file to disk", "sign-in   once per site", "exit 3 = signed out"], mono_heading=True)}
  {arrow(806, 260, 890, 260)}
  {box(896, 110, 330, 140, "Off-screen WebKit", ["no window, no Dock icon", "runs take turns"])}
  {box(896, 270, 330, 140, "Its own cookie store", ["signed in once, kept", "no cookie ever leaves"], accent="#c06bd6")}
  <rect x="56" y="452" width="1170" height="96" rx="16" fill="#ffffff" fill-opacity="0.025" stroke="#ffffff" stroke-opacity="0.10" stroke-dasharray="6 6"/>
  <g transform="translate(112 500)" opacity="0.55">
    <circle r="26" fill="none" stroke="{MUTED}" stroke-width="2.5"/>
    <g transform="rotate(45)"><path d="M0 -20 L5 0 L-5 0 Z" fill="#ff6b6b"/><path d="M0 20 L5 0 L-5 0 Z" fill="{MUTED}"/></g>
  </g>
  <text x="160" y="492" font-family="{SANS}" font-size="24" font-weight="600" fill="{PAPER}">Safari stays yours</text>
  <text x="160" y="524" font-family="{SANS}" font-size="20" fill="{MUTED}">No tabs opened, no hidden windows to catch your links, no shared cookies, nothing in Downloads.</text>""")


def main():
    for name, make in [("logo.svg", logo), ("icon.svg", icon), ("banner.svg", banner), ("how-it-works.svg", diagram)]:
        (HERE / name).write_text(make())
    if shutil.which("rsvg-convert") and shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "AppIcon.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    suffix = "" if scale == 1 else "@2x"
                    subprocess.run(["rsvg-convert", "-w", str(size * scale), "-h", str(size * scale), "-o",
                                    str(iconset / f"icon_{size}x{size}{suffix}.png"), str(HERE / "icon.svg")], check=True)
            subprocess.run(["iconutil", "-c", "icns", "-o", str(HERE / "AppIcon.icns"), str(iconset)], check=True)


if __name__ == "__main__":
    main()
