#!/usr/bin/env python3
"""Regenerates Umbra's artwork: logo.svg, mark.svg, icon.svg, banner.svg, how-it-works.svg,
social-preview.png and AppIcon.icns (the last two need rsvg-convert, and the icon iconutil). Run from
anywhere (or `make icon`); writes beside this file. The words on the artwork and the how-it-works
diagram are data, defined just below the palette."""
import math
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SANS = "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
INK, PAPER, MUTED, DIM = "#07060d", "#f4efe6", "#c3bdd3", "#9a93b3"
AMBER, ORCHID, GREEN = "#f6b26b", "#c06bd6", "#5fd38d"

TAGLINE = ["The headless browser built for agents.", "Native WebKit, invisible, signed in."]
COMMANDS = "read · fetch · download · sign-in"  # what ships today; keep it to commands that exist

# The how-it-works diagram: cards on a grid of (row, column), and arrows between cards (horizontal
# within a row, vertical within a column; "both" draws two heads). A line starting with "✓ " gets a
# green check. Each line should stay under about 24 characters to fit its card.
DIAGRAM = {
    "title": "How Umbra works",
    "desc": "Your script or agent runs the umbra command and gets back text, JSON or a file plus an exit code. "
            "umbra takes the profile's lock and drives WebKit off screen, with no window or Dock icon. WebKit "
            "presents Safari's user agent, runs your JavaScript, fetches and downloads, using Umbra's own cookie "
            "store on disk. umbra sign-in fills that store once, in one window with a check per site. Safari is "
            "untouched: no tabs, no focus taken, no shared cookies.",
    "cards": {
        "script": dict(at=(0, 0), heading="Your script", lines=["agents, launchd, shell", "gets text, JSON or files", "plus an exit code"]),
        "umbra": dict(at=(0, 1), heading="umbra", mono=True, lines=["takes the profile lock", "no window, no Dock icon", "starts in about 0.4 s"]),
        "webkit": dict(at=(0, 2), heading="WebKit, off screen", lines=["Safari's user agent", "runs your JavaScript", "fetches and downloads"]),
        "safari": dict(at=(1, 0), heading="Safari, untouched", lines=["no tabs, no focus taken", "no shared cookies"],
                       accent=DIM, dashed=True, icon="compass"),
        "signin": dict(at=(1, 1), heading="umbra sign-in", mono=True, accent=GREEN, lines=["once, in one window", "✓ when signed in"]),
        "store": dict(at=(1, 2), heading="Its own cookie store", accent=ORCHID, lines=["on disk, kept per profile", "values never printed"]),
    },
    "arrows": [("script", "umbra"), ("umbra", "webkit"), ("webkit", "store", "both"), ("signin", "store")],
}

DEFS = """
  <radialGradient id="corona" cx="0" cy="0" r="230" gradientUnits="userSpaceOnUse">
    <stop offset="0.60" stop-color="#ffe7b0" stop-opacity="0"/>
    <stop offset="0.655" stop-color="#fff1d2" stop-opacity="1"/>
    <stop offset="0.70" stop-color="#f6b26b" stop-opacity="0.7"/>
    <stop offset="0.82" stop-color="#c06bd6" stop-opacity="0.22"/>
    <stop offset="1" stop-color="#7c5cf6" stop-opacity="0"/>
  </radialGradient>
  <radialGradient id="rays" cx="0" cy="0" r="262" gradientUnits="userSpaceOnUse">
    <stop offset="0.56" stop-color="#fff1d2" stop-opacity="0.75"/>
    <stop offset="0.72" stop-color="#f6b26b" stop-opacity="0.32"/>
    <stop offset="1" stop-color="#c06bd6" stop-opacity="0"/>
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
  <radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">
    <stop offset="0" stop-color="#7c5cf6" stop-opacity="0.20"/>
    <stop offset="1" stop-color="#7c5cf6" stop-opacity="0"/>
  </radialGradient>
  <filter id="soft" x="-25%" y="-25%" width="150%" height="150%">
    <feGaussianBlur stdDeviation="2.6"/>
  </filter>
  <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#1b1730"/>
    <stop offset="1" stop-color="#07060d"/>
  </linearGradient>"""


def streamers(seed=5):
    """Corona streamers: thin tapered rays, longest along a tilted equator as at solar minimum."""
    rng = random.Random(seed)
    rays = []
    for i in range(44):
        angle = i * 360 / 44 + rng.uniform(-3, 3)
        equator = abs(math.cos(math.radians(2 * (angle - 28))))
        length = 22 + 92 * equator ** 3 * rng.uniform(0.55, 1) + rng.uniform(0, 14)
        half = rng.uniform(1.8, 4.2)
        tip = 148 + length
        a, b = math.radians(-half), math.radians(half)
        rays.append(f'<path d="M{148 * math.sin(a):.1f} {-148 * math.cos(a):.1f} L0 {-tip:.1f} '
                    f'L{148 * math.sin(b):.1f} {-148 * math.cos(b):.1f} Z" transform="rotate({angle:.1f})" '
                    f'fill-opacity="{rng.uniform(0.25, 0.7):.2f}"/>')
    return f'<g fill="url(#rays)" filter="url(#soft)">{"".join(rays)}</g>'


def mark(x, y, size):
    """The eclipse: corona and streamers, compass ticks, the moon (with Safari's needle in its
    shadow) and the diamond-ring bead, drawn in a 512 box centred on (x, y) and scaled to size."""
    ticks = []
    for i in range(24):
        major = i % 6 == 0
        inner, outer = (172, 194) if major else (179, 187)
        ticks.append(f'<line x1="0" y1="-{inner}" x2="0" y2="-{outer}" transform="rotate({i * 15})" '
                     f'stroke="#fff1d2" stroke-opacity="{0.6 if major else 0.3}" stroke-width="{3.5 if major else 2}" stroke-linecap="round"/>')
    return f"""
  <g transform="translate({x} {y}) scale({size / 512})">
    <circle r="230" fill="url(#corona)"/>
    {streamers()}
    {''.join(ticks)}
    <circle r="150" fill="#fff4dc"/>
    <circle cx="-7" cy="7" r="149" fill="url(#moon)"/>
    <g transform="translate(-7 7) rotate(45)">
      <path d="M0 -84 L12 0 L-12 0 Z" fill="#ff6b6b" fill-opacity="0.30"/>
      <path d="M0 84 L12 0 L-12 0 Z" fill="#ffffff" fill-opacity="0.12"/>
      <circle r="4.5" fill="#ffffff" fill-opacity="0.22"/>
    </g>
    <g transform="translate(103 -103)">
      <circle r="46" fill="url(#bead)"/>
      <path d="M0 -30 L2.2 -2.2 L30 0 L2.2 2.2 L0 30 L-2.2 2.2 L-30 0 L-2.2 -2.2 Z" fill="#ffffff" fill-opacity="0.9"/>
      <circle r="6" fill="#ffffff"/>
    </g>
  </g>"""


def small_mark(x, y, size):
    """The eclipse redrawn for 16 and 32 pixel icons: a bold ring and a large bead, nothing finer."""
    return f"""
  <g transform="translate({x} {y}) scale({size / 512})">
    <circle r="236" fill="url(#corona)"/>
    <circle r="184" fill="#fff1d2"/>
    <circle cx="-10" cy="10" r="160" fill="url(#moon)"/>
    <g transform="translate(124 -124)">
      <circle r="100" fill="url(#bead)"/>
      <circle r="44" fill="#ffffff"/>
    </g>
  </g>"""


def stars(width, height, count, seed, avoid=()):
    rng = random.Random(seed)
    dots = []
    while len(dots) < count:
        sx, sy = rng.uniform(16, width - 16), rng.uniform(16, height - 16)
        if any((sx - ax) ** 2 + (sy - ay) ** 2 < ar ** 2 for ax, ay, ar in avoid):
            continue
        dots.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{rng.choice([0.8, 1, 1.2, 1.6])}" '
                    f'fill="#ffffff" fill-opacity="{rng.uniform(0.15, 0.6):.2f}"/>')
    return "".join(dots)


def svg(width, height, title, desc, body):
    return "\n".join(line.rstrip() for line in f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-labelledby="title desc">
  <title id="title">{title}</title>
  <desc id="desc">{desc}</desc>
  <defs>{DEFS}
  </defs>{body}
</svg>
""".splitlines()) + "\n"


def card(width, height, radius=28):
    """The night-sky card every README image sits on; its faint rim keeps it distinct on GitHub's dark page."""
    return (f'<rect width="{width}" height="{height}" rx="{radius}" fill="url(#sky)"/>'
            f'<rect x="0.75" y="0.75" width="{width - 1.5}" height="{height - 1.5}" rx="{radius - 0.75}" '
            f'fill="none" stroke="#ffffff" stroke-opacity="0.10" stroke-width="1.5"/>')


def mark_svg():
    return svg(512, 512, "Umbra mark", "The Umbra eclipse on a transparent background, for dark backgrounds: a dark disc "
               "rimmed by a glowing corona with streamers and compass ticks, one bright diamond-ring point at its upper "
               "right, and a faint compass needle inside the shadow.", mark(256, 256, 512))


def logo():
    return svg(512, 512, "Umbra", "The Umbra eclipse on a round night-sky badge: a dark disc rimmed by a glowing corona "
               "with compass ticks, one bright diamond-ring point at its upper right, and a faint compass needle inside "
               "the shadow.", f"""
  <clipPath id="badge"><circle cx="256" cy="256" r="256"/></clipPath>
  <circle cx="256" cy="256" r="256" fill="url(#sky)"/>
  <g clip-path="url(#badge)">{stars(512, 512, 22, 17, avoid=[(256, 256, 214)])}</g>
  <circle cx="256" cy="256" r="255.25" fill="none" stroke="#ffffff" stroke-opacity="0.10" stroke-width="1.5"/>
  {mark(256, 256, 430)}""")


def icon(small=False):
    art = small_mark(512, 512, 800) if small else f"""
  <g transform="translate(100 100)">{stars(824, 824, 26, 7, avoid=[(412, 412, 330)])}</g>
  {mark(512, 512, 700)}"""
    return svg(1024, 1024, "Umbra app icon", "The Umbra eclipse on a deep indigo rounded square.", f"""
  <rect x="100" y="100" width="824" height="824" rx="185" fill="url(#sky)"/>
  <rect x="100.5" y="100.5" width="823" height="823" rx="184.5" fill="none" stroke="#ffffff" stroke-opacity="0.10"/>{art}""")


def banner():
    return svg(1280, 400, "Umbra: " + TAGLINE[0].rstrip("."),
               f"The Umbra eclipse beside the wordmark umbra, the lines '{TAGLINE[0]}' and '{TAGLINE[1]}', "
               f"and the commands {COMMANDS.replace(' · ', ', ')}, on a night sky.",
               f"""
  {card(1280, 400)}
  <circle cx="220" cy="200" r="250" fill="url(#halo)"/>
  {stars(1280, 400, 90, 3, avoid=[(220, 200, 190), (760, 200, 330)])}
  {mark(220, 200, 340)}
  <text x="430" y="196" font-family="{SANS}" font-size="124" font-weight="600" letter-spacing="4" fill="{PAPER}">umbra</text>
  <text x="434" y="254" font-family="{SANS}" font-size="33" fill="{PAPER}" fill-opacity="0.92">{TAGLINE[0]}</text>
  <text x="434" y="298" font-family="{SANS}" font-size="33" fill="{MUTED}">{TAGLINE[1]}</text>
  <text x="435" y="350" font-family="{MONO}" font-size="24" fill="{AMBER}" fill-opacity="0.85">{COMMANDS}</text>""")


def social():
    return svg(1280, 640, "Umbra: " + TAGLINE[0].rstrip("."),
               f"The Umbra eclipse, the wordmark umbra and the lines '{TAGLINE[0]}' and '{TAGLINE[1]}'",
               f"""
  <rect width="1280" height="640" fill="url(#sky)"/>
  <circle cx="300" cy="320" r="330" fill="url(#halo)"/>
  {stars(1280, 640, 130, 13, avoid=[(300, 320, 240), (860, 320, 320)])}
  {mark(300, 320, 450)}
  <text x="560" y="300" font-family="{SANS}" font-size="140" font-weight="600" letter-spacing="4" fill="{PAPER}">umbra</text>
  <text x="565" y="368" font-family="{SANS}" font-size="35" fill="{PAPER}" fill-opacity="0.92">{TAGLINE[0]}</text>
  <text x="565" y="418" font-family="{SANS}" font-size="35" fill="{MUTED}">{TAGLINE[1]}</text>
  <text x="566" y="472" font-family="{MONO}" font-size="26" fill="{AMBER}" fill-opacity="0.85">{COMMANDS}</text>""")


def compass(x, y):
    return f"""
  <g transform="translate({x} {y})" opacity="0.7">
    <circle r="20" fill="none" stroke="{MUTED}" stroke-width="2.5"/>
    <g transform="rotate(45)"><path d="M0 -15 L4.5 0 L-4.5 0 Z" fill="#ff6b6b"/><path d="M0 15 L4.5 0 L-4.5 0 Z" fill="{MUTED}"/></g>
  </g>"""


def box(x, y, w, h, heading, lines, accent=AMBER, mono=False, dashed=False, icon=None):
    rows = []
    for i, line in enumerate(lines):
        base, indent = y + 96 + i * 36, 0
        if line.startswith("✓ "):
            line, indent = line[2:], 34
            rows.append(f'<g transform="translate({x + 40} {base - 8})"><circle r="12" fill="{GREEN}"/>'
                        f'<path d="M-5.5 0.5 L-1.5 4.5 L6 -4" fill="none" stroke="{INK}" stroke-width="2.8" '
                        f'stroke-linecap="round" stroke-linejoin="round"/></g>')
        rows.append(f'<text x="{x + 28 + indent}" y="{base}" font-family="{SANS}" font-size="24" fill="{MUTED}">{line}</text>')
    border = 'stroke-dasharray="7 7" stroke-opacity="0.22"' if dashed else 'stroke-opacity="0.16"'
    fill = "0.02" if dashed else "0.05"
    return f"""
  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="#ffffff" fill-opacity="{fill}" stroke="#ffffff" stroke-width="1.5" {border}/>
  <rect x="{x}" y="{y + 26}" width="5" height="34" rx="2.5" fill="{accent}"/>
  <text x="{x + 28}" y="{y + 54}" font-family="{MONO if mono else SANS}" font-size="{27 if mono else 28}" font-weight="600" fill="{PAPER}">{heading}</text>
  {"".join(rows)}{compass(x + w - 40, y + h - 36) if icon == "compass" else ""}"""


def arrow(x1, y1, x2, y2, both=False):
    """A straight amber arrow; horizontal or vertical only."""
    dx, dy = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
    head = lambda px, py, ux, uy: (f'<path d="M{px} {py} L{px - 16 * ux - 8 * uy} {py - 16 * uy - 8 * ux} '
                                   f'L{px - 16 * ux + 8 * uy} {py - 16 * uy + 8 * ux} Z" fill="{AMBER}"/>')
    sx, sy = (x1 + 14 * dx, y1 + 14 * dy) if both else (x1, y1)
    return (f'<line x1="{sx}" y1="{sy}" x2="{x2 - 14 * dx}" y2="{y2 - 14 * dy}" stroke="{AMBER}" stroke-width="3"/>'
            + head(x2, y2, dx, dy) + (head(x1, y1, -dx, -dy) if both else ""))


def diagram(spec=DIAGRAM):
    """Lays out DIAGRAM: 330-wide cards in columns, each row as tall as its longest card."""
    cards, w, gap, top, row_gap = spec["cards"], 330, 57, 108, 70
    rows = max(c["at"][0] for c in cards.values()) + 1
    columns = max(c["at"][1] for c in cards.values()) + 1
    heights = [104 + 36 * max(len(c["lines"]) for c in cards.values() if c["at"][0] == r) for r in range(rows)]
    ys = [top + sum(heights[:r]) + row_gap * r for r in range(rows)]
    width, height = 96 + columns * w + (columns - 1) * gap, ys[-1] + heights[-1] + 42

    def frame(name):
        row, column = cards[name]["at"]
        return 48 + column * (w + gap), ys[row], w, heights[row]

    body = [box(*frame(name), c["heading"], c["lines"], c.get("accent", AMBER), c.get("mono", False),
                c.get("dashed", False), c.get("icon")) for name, c in cards.items()]
    for start, end, *both in spec["arrows"]:
        (ax, ay, aw, ah), (bx, by, bw, bh) = frame(start), frame(end)
        if ay == by:
            x1, x2 = (ax + aw + 6, bx - 6) if bx > ax else (ax - 6, bx + bw + 6)
            body.append(arrow(x1, ay + ah / 2, x2, by + bh / 2, bool(both)))
        else:
            y1, y2 = (ay + ah + 6, by - 6) if by > ay else (ay - 6, by + bh + 6)
            body.append(arrow(ax + aw / 2, y1, bx + bw / 2, y2, bool(both)))
    return svg(width, height, spec["title"], spec["desc"], f"""
  {card(width, height)}
  {stars(width, height, 40, 11)}
  <text x="48" y="74" font-family="{SANS}" font-size="32" font-weight="600" fill="{PAPER}">{spec["title"]}</text>
  {"".join(body)}""")


def render(svg_text, out, width, height=None):
    with tempfile.NamedTemporaryFile("w", suffix=".svg") as source:
        source.write(svg_text)
        source.flush()
        size = ["-w", str(width)] + (["-h", str(height)] if height else [])
        subprocess.run(["rsvg-convert", *size, "-o", str(out), source.name], check=True)


def main():
    for name, make in [("logo.svg", logo), ("mark.svg", mark_svg), ("icon.svg", icon), ("banner.svg", banner),
                       ("how-it-works.svg", diagram)]:
        (HERE / name).write_text(make())
    if not shutil.which("rsvg-convert"):
        return print("rsvg-convert not found: skipped social-preview.png and AppIcon.icns")
    render(social(), HERE / "social-preview.png", 1280, 640)
    if not shutil.which("iconutil"):
        return print("iconutil not found: skipped AppIcon.icns")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = size * scale
                suffix = "" if scale == 1 else "@2x"
                render(icon(small=pixels <= 32), iconset / f"icon_{size}x{size}{suffix}.png", pixels, pixels)
        subprocess.run(["iconutil", "-c", "icns", "-o", str(HERE / "AppIcon.icns"), str(iconset)], check=True)


if __name__ == "__main__":
    main()
