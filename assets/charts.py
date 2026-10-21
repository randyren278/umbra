#!/usr/bin/env python3
"""Regenerates the data pictures from the recorded measurements, in the artwork's style:

  showcase-v1.svg, showcase-mobile-v1.svg  the README highlights, derived from docs/evaluation

  speed.svg, speed-mobile.svg      from docs/evaluation/latency/results.json (Umbra vs Playwright, median ms)
  eval.svg, eval-mobile.svg        from docs/evaluation/regression/scores.json (every agent arm, task by task)
  memory.svg, memory-mobile.svg    from docs/evaluation/memory/results.json (tabs agents leave behind, before and after)
  sign-in.svg, sign-in-mobile.svg  the sign-in window's rules (words defined below)

Run from anywhere: python3 assets/charts.py. Writes beside this file.
"""
import json
import math
from pathlib import Path

from build import AMBER, DIM, GREEN, INK, MONO, MUTED, ORCHID, PAPER, SANS, card, stars, svg

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent / "docs/evaluation"
RED = "#ff7a7a"


def text(x, y, words, size=24, fill=MUTED, weight=None, anchor=None, family=SANS, opacity=None):
    extra = (f' font-weight="{weight}"' if weight else "") + (f' text-anchor="{anchor}"' if anchor else "") \
        + (f' fill-opacity="{opacity}"' if opacity else "")
    return f'<text x="{x:g}" y="{y:g}" font-family="{family}" font-size="{size}" fill="{fill}"{extra}>{words}</text>'


def heading(title, subtitle, width):
    return (text(48, 74, title, 34, PAPER, 600) + text(48, 114, subtitle, 23, MUTED)
            + stars(width, 150, 14, 21, avoid=[(width / 2, 90, width / 2 - 140)]))


# MARK: Showcase

def showcase_data():
    latency = json.loads((EVAL / 'latency/results.json').read_text())
    external = json.loads((EVAL / 'miniwob/2026-10-13/summary.json').read_text())['umbra']
    regression = eval_data()['arms']
    metrics = latency['metrics']
    return metrics, external, sum(a['passed'] for a in regression), sum(a['total'] for a in regression)


def proof_panel(x, y, width, metric, label, caption, color):
    native, baseline = metric['umbra_socket']['p50'], metric['chromium']['p50']
    ratio = baseline / native
    body = [f'<rect x="{x}" y="{y}" width="{width}" height="260" rx="18" fill="#07060d" fill-opacity="0.52" stroke="{color}" stroke-opacity="0.25"/>',
            text(x + 24, y + 34, label, 20, color, 600),
            text(x + 24, y + 118, f'{ratio:.1f}×', 78, PAPER, 700),
            text(x + 24, y + 151, caption, 23, MUTED)]
    for offset, name, value, fill in [(190, 'Umbra', native, color), (234, 'Playwright Chromium', baseline, DIM)]:
        body += [text(x + 24, y + offset - 8, name, 18, MUTED),
                 text(x + width - 24, y + offset - 8, f'{value:.2f} ms', 18, PAPER, anchor='end'),
                 f'<rect x="{x + 24}" y="{y + offset}" width="{width - 48}" height="6" rx="3" fill="#ffffff" fill-opacity="0.08"/>',
                 f'<rect x="{x + 24}" y="{y + offset}" width="{(width - 48) * value / baseline:.2f}" height="6" rx="3" fill="{fill}"/>']
    return ''.join(body)


def reward_panel(x, y, width, result):
    rate = result['strict_rate']
    cx, cy, radius = x + width - 65, y + 215, 33
    perimeter = math.tau * radius
    return (f'<rect x="{x}" y="{y}" width="{width}" height="260" rx="18" fill="#07060d" fill-opacity="0.52" stroke="{GREEN}" stroke-opacity="0.25"/>'
            + text(x + 24, y + 34, 'EXTERNAL AGENT TASKS', 20, GREEN, 600)
            + text(x + 24, y + 118, f'{rate:.1%}', 74, PAPER, 700)
            + text(x + 24, y + 151, 'Full reward on MiniWoB++', 22, MUTED)
            + text(x + 24, y + 190, f'{result["strict_successes"]} / {result["episodes"]} cases', 23, GREEN, 600)
            + text(x + 24, y + 222, '125 tasks · 5 repetitions', 18, MUTED)
            + f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="#ffffff" stroke-opacity="0.09" stroke-width="7"/>'
            + f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{GREEN}" stroke-width="7" stroke-linecap="round" stroke-dasharray="{perimeter * rate:.2f} {perimeter:.2f}" transform="rotate(-90 {cx} {cy})"/>')


def showcase_picture(mobile=False):
    metrics, result, passed, total = showcase_data()
    width, height = (600, 1140) if mobile else (1200, 588)
    body = [card(width, height),
            text(36, 68, 'Fast loops. Real workflows.', 34, PAPER, 600),
            text(36, 106, 'Measured on a Mac. Built for your agent.', 22, MUTED)]
    panel_width = 528 if mobile else 360
    positions = [(36, 140), (36, 418), (36, 696)] if mobile else [(36, 140), (420, 140), (804, 140)]
    body += [proof_panel(*positions[0], panel_width, metrics['snapshot'], 'READ THE PAGE', 'Faster snapshots', AMBER),
             proof_panel(*positions[1], panel_width, metrics['click'], 'TAKE THE NEXT STEP', 'Faster clicks + DOM update', ORCHID),
             reward_panel(*positions[2], panel_width, result)]
    ribbon = 980 if mobile else 426
    body += [f'<rect x="36" y="{ribbon}" width="{width - 72}" height="82" rx="16" fill="{GREEN}" fill-opacity="0.08"/>',
             text(58, ribbon + 54, f'{passed}/{total}', 42, GREEN, 700),
             text(230, ribbon + 35, 'Workflow runs passed', 24, PAPER, 600),
             text(230, ribbon + 62, '14 custom tasks · 5 agent setups', 18, MUTED)]
    if mobile:
        body += [text(36, 1092, 'Speed: local fixture · 20-run medians · daemon socket', 18, MUTED),
                 text(36, 1118, 'vs Playwright Chromium. MiniWoB++: adapted MCP.', 18, MUTED)]
    else:
        body += [text(36, 542, 'Speed: same local fixture · median of 20 runs · daemon socket vs Playwright Chromium', 18, MUTED),
                 text(36, 568, 'MiniWoB++: adapted MCP setup. Complete evaluations, comparison data and methods in docs/evaluation.', 18, MUTED)]
    desc = (f'On the same local fixture, Umbra daemon socket snapshots took {metrics["snapshot"]["umbra_socket"]["p50"]} ms versus '
            f'{metrics["snapshot"]["chromium"]["p50"]} ms for Playwright Chromium, {metrics["snapshot"]["chromium"]["p50"] / metrics["snapshot"]["umbra_socket"]["p50"]:.1f} times faster. '
            f'Clicks plus DOM update took {metrics["click"]["umbra_socket"]["p50"]} versus {metrics["click"]["chromium"]["p50"]} ms, '
            f'{metrics["click"]["chromium"]["p50"] / metrics["click"]["umbra_socket"]["p50"]:.1f} times faster. Twenty measured runs per cell; excludes MCP framing. '
            f'Umbra earned full reward on {result["strict_successes"]}/{result["episodes"]} MiniWoB++ cases in an adapted MCP setup, '
            f'and passed {passed}/{total} project-authored workflow regression runs. These are scoped observations, not a general superiority claim.')
    return svg(width, height, 'Umbra: measured speed and completed agent workflows', desc, ''.join(body))


def showcase():
    return showcase_picture()


def showcase_mobile():
    return showcase_picture(mobile=True)


# MARK: Speed

SPEED_ROWS = [  # (label, metric in results.json)
    ("Accessibility snapshot", "snapshot"),
    ("Click + DOM update", "click"),
    ("Fill a React-controlled field", "fill"),
    ("Eval round trip", "eval"),
    ("Viewport screenshot to a file", "screenshot"),
    ("Warm navigate (local page)", "navigate"),
]
SPEED_SERIES = [("umbra_socket", "Umbra daemon socket", AMBER),
                ("chromium", "Playwright Chromium", ORCHID),
                ("webkit", "Playwright WebKit", DIM)]


def speed_data():
    results = json.loads((EVAL / "latency/results.json").read_text())
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
            "in one warm Node process; Umbra daemon socket; MCP framing is not timed.")


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
                     "docs/evaluation/latency/RESULTS.md", 18, DIM))
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
    body.append(text(40, height - 54, "Umbra daemon socket; MCP framing is not timed.", 19, DIM))
    body.append(text(40, height - 28, "Playwright 1.62.1 · docs/evaluation/latency/RESULTS.md", 19, DIM))
    return svg(width, height, "Umbra against Playwright, time per action", speed_desc(results, rows), "\n  ".join(body))


# MARK: Eval

def eval_data():
    return json.loads((EVAL / "regression/scores.json").read_text())


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
    body.append(text(48, height - 30, f"Each agent ran each task once on one Mac · {data['date']} · docs/evaluation/regression/RESULTS.md", 18, DIM))
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
    body.append(text(40, height - 26, "docs/evaluation/regression/RESULTS.md", 19, DIM))
    return svg(width, height, "Agent eval: fresh agents complete real browser tasks with Umbra", eval_desc(data), "\n  ".join(body))


# MARK: Memory

MEMORY_SERIES = [("before", "Before", ORCHID), ("after", "Now", AMBER)]


def memory_data():
    return json.loads((EVAL / "memory/results.json").read_text())


def memory_tiles(data):
    """(headline, what it compares, the numbers) for each stat tile."""
    runs = {label: {name: run["summary"] for name, run in data["runs"][label].items()} for label in ("before", "after")}
    one, four = (runs["before"]["one_page"], runs["after"]["one_page"]), (runs["before"]["four_pages"], runs["after"]["four_pages"])
    return [(f"{one[0]['end_mb'] / one[1]['end_mb']:.1f}× less memory", "after 10 agents leave a tab each",
             f"{one[0]['end_mb']:,} → {one[1]['end_mb']:,} MB", AMBER),
            (f"{one[0]['idle_cpu_s_per_min'] / one[1]['idle_cpu_s_per_min']:.1f}× less CPU", "while those tabs sit unused",
             f"{one[0]['idle_cpu_s_per_min']:.1f} → {one[1]['idle_cpu_s_per_min']:.1f} CPU s / min", GREEN),
            (f"{four[0]['end_mb'] / four[1]['end_mb']:.1f}× less memory", "when each tab visited 4 pages",
             f"{four[0]['end_mb']:,} → {four[1]['end_mb']:,} MB · peak {four[0]['peak_mb']:,} → {four[1]['peak_mb']:,}", AMBER)]


def memory_desc(data):
    tiles = memory_tiles(data)
    idle = data["runs"]["after"]["one_page"]["options"]["idle"]
    return ("Physical memory of Umbra and its WebKit processes while one agent keeps working and ten others have each "
            f"left a heavy page open. Before, the abandoned tabs stayed open: about 2.7 GB the whole time. Now each tab "
            f"closes once unused for UMBRA_IDLE ({idle} s in this run, 15 minutes by default), and memory falls to one tab's worth. "
            + "; ".join(f"{head} {what}, {numbers}" for head, what, numbers, _ in tiles)
            + f". bench/memory.py on synthetic pages, {data['machine']['cpu']}, macOS {data['machine']['macos']}.")


def memory_plot(data, x0, y0, width, height, size=19):
    """Memory over time after the agents leave, before and now, with the idle limit marked."""
    runs = {label: data["runs"][label]["one_page"] for label, _, _ in MEMORY_SERIES}
    idle, watch = runs["after"]["options"]["idle"], runs["after"]["options"]["watch"]
    top_mb = 3000
    def px(t): return x0 + width * t / watch
    def py(mb): return y0 + height * (1 - mb / top_mb)
    out = []
    for gb in range(4):
        y = py(gb * 1000)
        out.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + width}" y2="{y:.1f}" stroke="#ffffff" stroke-opacity="{0.16 if gb == 0 else 0.07}" stroke-width="1.5"/>'
                   + text(x0 - 12, y + 6, f"{gb} GB", size - 1, DIM, anchor="end"))
    for t in range(0, watch + 1, 30):
        out.append(text(px(t), y0 + height + 30, f"{t} s", size - 1, DIM, anchor="middle"))
    out.append(f'<line x1="{px(idle):.1f}" y1="{y0 - 6}" x2="{px(idle):.1f}" y2="{y0 + height}" stroke="{PAPER}" stroke-opacity="0.35" stroke-width="1.5" stroke-dasharray="5 6"/>'
               + text(px(idle) + 10, y0 + 12, "idle limit", size - 1, MUTED))
    for label, name, color in MEMORY_SERIES:
        samples = runs[label]["samples"]
        points = " ".join(f"{px(s['t']):.1f},{py(s['total']):.1f}" for s in samples)
        end = samples[-1]
        out.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round">'
                   f'<title>{name}: {round(samples[0]["total"]):,} MB when the agents left, {round(end["total"]):,} MB {end["t"]} s later</title></polyline>'
                   f'<circle cx="{px(end["t"]):.1f}" cy="{py(end["total"]):.1f}" r="5" fill="{color}"/>'
                   + text(px(end["t"]) - 4, py(end["total"]) - 16, f"{name} {round(end['total']):,} MB", size, PAPER, 600, anchor="end"))
    out.append(text(x0, y0 + height + 58, "Seconds after the agents left their tabs", size - 1, DIM))
    return "".join(out)


def memory_tile(x, y, width, head, what, numbers, color, size=1.0):
    return (f'<rect x="{x}" y="{y}" width="{width}" height="{round(112 * size)}" rx="16" fill="#07060d" fill-opacity="0.52" stroke="{color}" stroke-opacity="0.25"/>'
            + text(x + 22, y + round(42 * size), head, round(30 * size), PAPER, 700)
            + text(x + 22, y + round(72 * size), what, round(19 * size), MUTED)
            + text(x + 22, y + round(98 * size), numbers, round(19 * size), color, 600))


def memory():
    data = memory_data()
    width, height = 1200, 620
    body = [card(width, height), heading("Tabs agents leave behind let go",
                                         "Umbra and its WebKit processes while one agent works and ten others have left", width)]
    for (label, name, color), x in zip(MEMORY_SERIES, (48, 360)):
        body.append(f'<rect x="{x}" y="138" width="22" height="14" rx="4" fill="{color}"/>'
                    + text(x + 32, 151, "Before (tabs never closed)" if label == "before" else "Now", 20, MUTED))
    body.append(memory_plot(data, 120, 200, 600, 300))
    for i, (head, what, numbers, color) in enumerate(memory_tiles(data)):
        body.append(memory_tile(780, 186 + i * 128, 372, head, what, numbers, color))
    m = data["machine"]
    body.append(text(48, height - 30, f"Idle limit {data['runs']['after']['one_page']['options']['idle']} s in this run (15 minutes by default) · "
                     f"bench/memory.py, synthetic pages · {m['cpu']}, macOS {m['macos']} · docs/evaluation/memory", 18, DIM))
    return svg(width, height, "Umbra's memory after agents leave their tabs, before and now", memory_desc(data), "\n  ".join(body))


def memory_mobile():
    data = memory_data()
    width, height = 600, 1150
    body = [card(width, height), text(40, 70, "Tabs agents leave", 32, PAPER, 600), text(40, 108, "behind let go", 32, PAPER, 600),
            text(40, 146, "Memory while one agent works on", 21, MUTED), text(40, 172, "and ten others have left a tab", 21, MUTED)]
    for (label, name, color), x in zip(MEMORY_SERIES, (40, 300)):
        body.append(f'<rect x="{x}" y="196" width="20" height="13" rx="4" fill="{color}"/>'
                    + text(x + 28, 208, "Before" if label == "before" else "Now", 19, MUTED))
    body.append(memory_plot(data, 100, 258, 450, 280, size=18))
    for i, (head, what, numbers, color) in enumerate(memory_tiles(data)):
        body.append(memory_tile(40, 646 + i * 140, 520, head, what, numbers, color, size=1.08))
    body.append(text(40, height - 52, f"Idle limit {data['runs']['after']['one_page']['options']['idle']} s here, 15 minutes by default.", 19, DIM))
    body.append(text(40, height - 26, "bench/memory.py · docs/evaluation/memory", 19, DIM))
    return svg(width, height, "Umbra's memory after agents leave their tabs, before and now", memory_desc(data), "\n  ".join(body))


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
    outputs = [("showcase-v1.svg", showcase), ("showcase-mobile-v1.svg", showcase_mobile), ("speed.svg", speed), ("speed-mobile.svg", speed_mobile), ("sign-in.svg", sign_in),
               ("sign-in-mobile.svg", sign_in_mobile)]
    if (EVAL / "regression/scores.json").exists():
        outputs += [("eval.svg", eval_chart), ("eval-mobile.svg", eval_mobile)]
    if (EVAL / "memory/results.json").exists():
        outputs += [("memory.svg", memory), ("memory-mobile.svg", memory_mobile)]
    for name, make in outputs:
        (HERE / name).write_text(make())
        print("wrote", name)


if __name__ == "__main__":
    main()
