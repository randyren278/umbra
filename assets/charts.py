#!/usr/bin/env python3
"""Regenerates the README's data pictures from the repository's own numbers, in the artwork's style:

  speed.svg, speed-mobile.svg      from bench/results.json (Umbra vs Playwright, median ms)
  eval.svg, eval-mobile.svg        from bench/eval/scores.json (every agent arm, task by task)
  sign-in.svg, sign-in-mobile.svg  the sign-in window's rules (words defined below)

Run from anywhere: python3 assets/charts.py. Writes beside this file.
"""
import json
import math
from pathlib import Path

from build import AMBER, DIM, GREEN, INK, MONO, MUTED, ORCHID, PAPER, SANS, card, stars, svg

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "bench"
RED = "#ff7a7a"


def text(x, y, words, size=24, fill=MUTED, weight=None, anchor=None, family=SANS, opacity=None):
    extra = (f' font-weight="{weight}"' if weight else "") + (f' text-anchor="{anchor}"' if anchor else "") \
        + (f' fill-opacity="{opacity}"' if opacity else "")
    return f'<text x="{x:g}" y="{y:g}" font-family="{family}" font-size="{size}" fill="{fill}"{extra}>{words}</text>'


def heading(title, subtitle, width):
    return (text(48, 74, title, 34, PAPER, 600) + text(48, 114, subtitle, 23, MUTED)
            + stars(width, 150, 14, 21, avoid=[(width / 2, 90, width / 2 - 140)]))


# MARK: Speed

SPEED_ROWS = [  # (label, metric in results.json)
    ("Accessibility snapshot", "snapshot"),
    ("Click + DOM update", "click"),
    ("Fill a React-controlled field", "fill"),
    ("Eval round trip", "eval"),
    ("Viewport screenshot to a file", "screenshot"),
    ("Warm navigate (local page)", "navigate"),
]
SPEED_SERIES = [("umbra_socket", "Umbra (socket, as MCP uses it)", AMBER),
                ("chromium", "Playwright Chromium", ORCHID),
                ("webkit", "Playwright WebKit", DIM)]


def speed_data():
    results = json.loads((BENCH / "results.json").read_text())
    rows = [(label, [results["metrics"][metric][key]["p50"] for key, _, _ in SPEED_SERIES]) for label, metric in SPEED_ROWS]
    return results, rows


def number(ms):
    return f"{ms:.1f}".rstrip("0").rstrip(".") if ms < 10 else f"{ms:.0f}"


def scale(ms, x0, width):
    """Log scale from 0.1 to 100 ms."""
    return x0 + width * (math.log10(max(ms, 0.1)) + 1) / 3


def speed_desc(results, rows):
    parts = [f"{label}: Umbra {number(v[0])}, Chromium {number(v[1])}, WebKit {number(v[2])}" for label, v in rows]
    return ("Median milliseconds per action on the same local pages, 20 runs each, log scale. " + "; ".join(parts)
            + f". Playwright 1.62.1 with Chromium {results['browsers']['chromium']} and WebKit {results['browsers']['webkit']}, "
            "in one warm Node process; Umbra over its socket, as MCP uses it.")


def speed():
    results, rows = speed_data()
    width, x0, bar_w, row_h, top = 1200, 430, 640, 96, 186
    height = top + row_h * len(rows) + 94
    body = [card(width, height), heading("Read and act in about a millisecond",
                                         "Median time per action, same local pages, 20 runs each (log scale)", width)]
    for (key, label, color), x in zip(SPEED_SERIES, (48, 410, 660)):
        body.append(f'<rect x="{x}" y="138" width="22" height="14" rx="4" fill="{color}"/>' + text(x + 32, 151, label, 20, MUTED))
    for tick in (0.1, 1, 10, 100):
        x = scale(tick, x0, bar_w)
        body.append(f'<line x1="{x:.1f}" y1="{top - 10}" x2="{x:.1f}" y2="{top + row_h * len(rows) - 14}" '
                    f'stroke="#ffffff" stroke-opacity="0.08" stroke-width="1.5"/>'
                    + text(x, top + row_h * len(rows) + 14, f"{number(tick)} ms", 19, DIM, anchor="middle"))
    for r, (label, values) in enumerate(rows):
        y = top + r * row_h
        body.append(text(48, y + 40, label, 24, PAPER, 600))
        for i, ((_, _, color), value) in enumerate(zip(SPEED_SERIES, values)):
            by, end = y + 8 + i * 24, scale(value, x0, bar_w)
            best = value == min(values)
            body.append(f'<rect x="{x0}" y="{by}" width="{end - x0:.1f}" height="17" rx="5" fill="{color}" '
                        f'fill-opacity="{1 if i == 0 else 0.75}"/>'
                        + text(end + 10, by + 15, number(value), 19, PAPER if best else MUTED, 700 if best else None))
    body.append(text(48, height - 30, f"Umbra {results['umbra_version'].removeprefix('umbra ')} · "
                     f"Playwright 1.62.1, one warm Node process · {results['machine']['cpu']}, macOS {results['machine']['macos']} · "
                     "bench/results.md", 18, DIM))
    return svg(width, height, "Umbra against Playwright, time per action", speed_desc(results, rows), "\n  ".join(body))


def speed_mobile():
    results, rows = speed_data()
    width, x0, bar_w, group_h, top = 600, 48, 430, 128, 200
    height = top + group_h * len(rows) + 96
    body = [card(width, height), text(40, 70, "Read and act in", 32, PAPER, 600), text(40, 108, "about a millisecond", 32, PAPER, 600),
            text(40, 146, "Median ms per action, log scale", 21, MUTED)]
    for i, (key, label, color) in enumerate(SPEED_SERIES):
        name = ["Umbra", "Chromium", "WebKit"][i]
        x = 40 + i * 172
        body.append(f'<rect x="{x}" y="166" width="20" height="13" rx="4" fill="{color}"/>' + text(x + 28, 178, name, 19, MUTED))
    for g, (label, values) in enumerate(rows):
        y = top + g * group_h
        body.append(text(40, y + 34, label, 22, PAPER, 600))
        for i, ((_, _, color), value) in enumerate(zip(SPEED_SERIES, values)):
            by, end = y + 48 + i * 24, scale(value, x0, bar_w)
            best = value == min(values)
            body.append(f'<rect x="{x0}" y="{by}" width="{end - x0:.1f}" height="16" rx="5" fill="{color}" '
                        f'fill-opacity="{1 if i == 0 else 0.75}"/>'
                        + text(end + 8, by + 14, number(value), 18, PAPER if best else MUTED, 700 if best else None))
    body.append(text(40, height - 54, "Umbra over its socket, as MCP uses it.", 19, DIM))
    body.append(text(40, height - 28, "Playwright 1.62.1 · bench/results.md", 19, DIM))
    return svg(width, height, "Umbra against Playwright, time per action", speed_desc(results, rows), "\n  ".join(body))


# MARK: Eval

def eval_data():
    return json.loads((BENCH / "eval" / "scores.json").read_text())


def discs(x, y, tasks, step, radius):
    out = []
    for i, (task, result) in enumerate(tasks):
        cx = x + i * step
        if result == "PASS":
            out.append(f'<circle cx="{cx}" cy="{y}" r="{radius}" fill="{GREEN}"><title>{task}: passed</title></circle>'
                       f'<path d="M{cx - radius * 0.42:.1f} {y + 0.5:.1f} L{cx - radius * 0.1:.1f} {y + radius * 0.36:.1f} '
                       f'L{cx + radius * 0.45:.1f} {y - radius * 0.32:.1f}" fill="none" stroke="{INK}" stroke-width="2.6" '
                       f'stroke-linecap="round" stroke-linejoin="round"/>')
        else:
            out.append(f'<circle cx="{cx}" cy="{y}" r="{radius - 1.5}" fill="none" stroke="{RED}" stroke-width="3">'
                       f'<title>{task}: failed</title></circle>'
                       f'<path d="M{cx - 5} {y - 5} L{cx + 5} {y + 5} M{cx + 5} {y - 5} L{cx - 5} {y + 5}" stroke="{RED}" '
                       f'stroke-width="2.6" stroke-linecap="round"/>')
    return "".join(out)


def eval_desc(data):
    arms = "; ".join(f"{a['agent']} ({a['guidance']}): {a['passed']} of {a['total']} passed, {a['critical']} critical failures, "
                     f"median {a['median_calls']:g} tool calls" for a in data["arms"])
    return (f"Fresh agents given {data['tasks_run']} realistic browser tasks on the Northwind Ops test site, each graded from "
            f"the site's server-side state. {arms}.")


def eval_chart():
    data = eval_data()
    arms, width = data["arms"], 1200
    row_h, top = 92, 214
    height = top + row_h * len(arms) + 92
    passed = sum(a["passed"] for a in arms)
    total = sum(a["total"] for a in arms)
    body = [card(width, height),
            heading(f"{passed} of {total} runs passed: {data['tasks_run']} tasks × {len(arms)} agent setups",
                    "Fresh agents, real tasks, graded from the site's own state. Zero critical failures." if
                    not any(a["critical"] for a in arms) else "Fresh agents, real tasks, graded from the site's own state.", width)]
    cols = [(48, "Agent"), (520, f"{data['tasks_run']} tasks, one disc each"), (1000, "Passed"), (1104, "Calls")]
    for x, label in cols:
        body.append(text(x, top - 30, label.upper(), 17, DIM, 600))
    for r, arm in enumerate(arms):
        y = top + r * row_h
        if r:
            body.append(f'<line x1="48" y1="{y - 18}" x2="{width - 48}" y2="{y - 18}" stroke="#ffffff" stroke-opacity="0.08" stroke-width="1.5"/>')
        accent = AMBER if arm["family"] == "Claude" else ORCHID
        body.append(f'<rect x="48" y="{y + 2}" width="5" height="44" rx="2.5" fill="{accent}"/>')
        body.append(text(68, y + 22, arm["agent"], 24, PAPER, 600) + text(68, y + 48, arm["guidance"], 19, MUTED))
        body.append(discs(536, y + 26, arm["tasks"], 33, 13))
        score_color = GREEN if arm["passed"] == arm["total"] else AMBER
        body.append(text(1000, y + 36, f"{arm['passed']}/{arm['total']}", 30, score_color, 700))
        body.append(text(1104, y + 36, f"{arm['median_calls']:g}", 26, PAPER))
    body.append(text(48, height - 58, "Calls: median tool calls per task. Critical: Safari or AppleScript, leaving its own profile, "
                     "reading the graders, or another browser.", 18, DIM))
    body.append(text(48, height - 30, f"Each agent ran each task once on one Mac · {data['date']} · bench/eval/RESULTS.md", 18, DIM))
    return svg(width, height, "Agent eval: fresh agents complete real browser tasks with Umbra", eval_desc(data), "\n  ".join(body))


def eval_mobile():
    data = eval_data()
    arms, width = data["arms"], 600
    group_h, top = 150, 200
    height = top + group_h * len(arms) + 84
    passed = sum(a["passed"] for a in arms)
    total = sum(a["total"] for a in arms)
    body = [card(width, height), text(40, 70, f"{passed} of {total} runs passed", 32, PAPER, 600),
            text(40, 108, f"{data['tasks_run']} tasks × {len(arms)} agent setups", 32, PAPER, 600),
            text(40, 148, "Graded from the site's own state.", 21, MUTED),
            text(40, 176, "Zero critical failures." if not any(a["critical"] for a in arms) else "", 21, MUTED)]
    for g, arm in enumerate(arms):
        y = top + g * group_h
        accent = AMBER if arm["family"] == "Claude" else ORCHID
        body.append(f'<rect x="40" y="{y + 4}" width="5" height="40" rx="2.5" fill="{accent}"/>')
        body.append(text(58, y + 22, arm["agent"], 23, PAPER, 600) + text(58, y + 47, arm["guidance"], 20, MUTED))
        score_color = GREEN if arm["passed"] == arm["total"] else AMBER
        body.append(text(width - 40, y + 34, f"{arm['passed']}/{arm['total']}", 28, score_color, 700, anchor="end"))
        tasks = arm["tasks"]
        body.append(discs(54, y + 80, tasks[:7], 36, 13) + discs(54, y + 116, tasks[7:], 36, 13))
    body.append(text(40, height - 52, f"Each agent ran each task once, on one Mac.", 19, DIM))
    body.append(text(40, height - 26, "bench/eval/RESULTS.md", 19, DIM))
    return svg(width, height, "Agent eval: fresh agents complete real browser tasks with Umbra", eval_desc(data), "\n  ".join(body))


# MARK: Sign-in

SIGN_IN = {
    "title": "A sign-in window, not a browser",
    "subtitle": "You sign in once. Agents use the session in the background.",
    "rows": [("Canvas", "canvas.example.edu", True), ("LinkedIn", "linkedin.com", True), ("GitHub", "github.com", False)],
    "steps": [
        ("Checks without loading", "A cookie, or a request that is cancelled", "before anything renders."),
        ("Shows only the login page", "For a site it can check, every page is", "checked first; signed in, it never shows."),
        ("Closes the moment you're in", "The sign-in lands, the page closes;", "agents take it from there."),
    ],
    "desc": "Umbra's sign-in window lists each account and whether it is signed in, with Sign In, Sign Out and Add "
            "Account. It checks each site without loading it. For a site it can check, it shows the page only to sign "
            "in, checks every page before it appears, and closes the page the moment the sign-in lands, so the window "
            "can't be used to browse. Add Account takes a pasted login link; after the first sign-in (ended with Done) "
            "it learns the account's sign-in cookie and checks it like the others.",
}


def window_mock(x, y, w, rows, k=1.0):
    """The list window, drawn: the eclipse, each account with its status, and Add Account. k scales the type."""
    h = 140 + 74 * len(rows) + 86
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="#0d0b16" stroke="#ffffff" stroke-opacity="0.16" stroke-width="1.5"/>']
    for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        out.append(f'<circle cx="{x + 28 + i * 22}" cy="{y + 26}" r="7" fill="{color}" fill-opacity="0.85"/>')
    out.append(f'<circle cx="{x + 58}" cy="{y + 88}" r="26" fill="#07060d" stroke="#fff1d2" stroke-width="3" stroke-opacity="0.9"/>'
               f'<circle cx="{x + 74}" cy="{y + 72}" r="4" fill="#fff4dc"/>')
    out.append(text(x + 100, y + 86, "Umbra", round(26 * k), PAPER, 600) + text(x + 100, y + 112, "Sign in to each site once.", round(18 * k), MUTED))
    for i, (name, domain, signed) in enumerate(rows):
        ry = y + 140 + i * 74
        out.append(f'<line x1="{x + 24}" y1="{ry}" x2="{x + w - 24}" y2="{ry}" stroke="#ffffff" stroke-opacity="0.08"/>')
        out.append(f'<rect x="{x + 24}" y="{ry + 16}" width="42" height="42" rx="10" fill="{AMBER}" fill-opacity="0.16"/>'
                   + text(x + 45, ry + 45, name[0], round(21 * k), AMBER, 700, anchor="middle"))
        out.append(text(x + 80, ry + 34, name, round(20 * k), PAPER, 600) + text(x + 80, ry + 56, domain, round(16 * k), MUTED))
        bx = x + w - 140 - round((k - 1) * 90)  # bigger type needs the status further left
        if signed:
            out.append(f'<circle cx="{bx - 98}" cy="{ry + 37}" r="9" fill="{GREEN}"/>'
                       f'<path d="M{bx - 102} {ry + 37} L{bx - 99} {ry + 40} L{bx - 93} {ry + 33}" fill="none" stroke="{INK}" stroke-width="2.2" stroke-linecap="round"/>'
                       + text(bx - 84, ry + 43, "Signed in", round(17 * k), PAPER))
        else:
            out.append(f'<circle cx="{bx - 98}" cy="{ry + 37}" r="8" fill="none" stroke="{MUTED}" stroke-width="2"/>'
                       + text(bx - 84, ry + 43, "Signed out", round(17 * k), MUTED))
        out.append(f'<rect x="{x + w - 126}" y="{ry + 20}" width="102" height="34" rx="9" fill="#ffffff" fill-opacity="0.09"/>'
                   + text(x + w - 75, ry + 43, "Sign Out" if signed else "Sign In", round(16 * k), PAPER, anchor="middle"))
    ay = y + 140 + 74 * len(rows) + 22
    out.append(f'<rect x="{x + 24}" y="{ay}" width="166" height="38" rx="10" fill="#ffffff" fill-opacity="0.09"/>'
               + text(x + 107, ay + 25, "+ Add Account…", round(17 * k), PAPER, anchor="middle"))
    return "".join(out), h


def step(x, y, n, title, lines, accent):
    return (f'<circle cx="{x + 22}" cy="{y + 20}" r="20" fill="{accent}" fill-opacity="0.16" stroke="{accent}" stroke-width="2"/>'
            + text(x + 22, y + 28, str(n), 21, accent, 700, anchor="middle")
            + text(x + 60, y + 28, title, 25, PAPER, 600)
            + "".join(text(x + 60, y + 62 + i * 30, line, 21, MUTED) for i, line in enumerate(lines)))


def sign_in():
    spec, width = SIGN_IN, 1200
    mock, mock_h = window_mock(48, 160, 520, spec["rows"])
    height = 160 + mock_h + 56
    body = [card(width, height), heading(spec["title"], spec["subtitle"], width), mock]
    accents = [AMBER, ORCHID, GREEN]
    for i, (title, *lines) in enumerate(spec["steps"]):
        body.append(step(620, 176 + i * 132, i + 1, title, lines, accents[i]))
        if i < 2:
            body.append(f'<line x1="642" y1="{176 + i * 132 + 48}" x2="642" y2="{176 + (i + 1) * 132 - 8}" stroke="#ffffff" stroke-opacity="0.18" stroke-width="2" stroke-dasharray="4 6"/>')
    body.append(text(620, height - 40, "An added account: Done the first time, then checked too.", 20, DIM))
    return svg(width, height, spec["title"], spec["desc"], "\n  ".join(body))


def sign_in_mobile():
    spec, width = SIGN_IN, 600
    mock, mock_h = window_mock(40, 190, 520, spec["rows"], k=1.12)
    steps_top = 190 + mock_h + 44
    height = steps_top + 3 * 140 + 70
    body = [card(width, height), text(40, 70, "A sign-in window,", 32, PAPER, 600), text(40, 108, "not a browser", 32, PAPER, 600),
            text(40, 146, "Sign in once. Agents use the session.", 21, MUTED), mock]
    accents = [AMBER, ORCHID, GREEN]
    for i, (title, *lines) in enumerate(spec["steps"]):
        body.append(step(40, steps_top + i * 140, i + 1, title, lines, accents[i]))
    body.append(text(40, height - 34, "An added account: Done the first time, then checked.", 19, DIM))
    return svg(width, height, spec["title"], spec["desc"], "\n  ".join(body))


def main():
    outputs = [("speed.svg", speed), ("speed-mobile.svg", speed_mobile), ("sign-in.svg", sign_in),
               ("sign-in-mobile.svg", sign_in_mobile)]
    if (BENCH / "eval" / "scores.json").exists():
        outputs += [("eval.svg", eval_chart), ("eval-mobile.svg", eval_mobile)]
    for name, make in outputs:
        (HERE / name).write_text(make())
        print("wrote", name)


if __name__ == "__main__":
    main()
