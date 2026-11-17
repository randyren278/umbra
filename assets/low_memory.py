#!/usr/bin/env python3
"""Generate the low-memory README figures from public, synthetic measurements."""
import json
from html import escape
from pathlib import Path

from build import BLUE, CYAN, MUTED, PAPER, SANS, VIOLET, card, svg

HERE = Path(__file__).resolve().parent
DATA = HERE.parent/'docs/evaluation/low-memory/results.json'


def text(x, y, words, size=24, fill=MUTED, weight=400, anchor='start'):
    return (f'<text x="{x}" y="{y}" font-family="{SANS}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(words)}</text>')


def panel(x, y, w, h):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="#11192b" stroke="#2f3b55"/>'


def span(values, digits=0):
    low, high = min(values), max(values)
    return f'{low:.{digits}f}–{high:.{digits}f}'


def comparison(data, mobile=False):
    w, h = (600, 940) if mobile else (1200, 644)
    c3 = data['originalC3']; saving = c3['pairedPostNativeSavingPercent']
    body = [card(w, h), text(36, 64, 'Keep the session. Use less RAM.', 32, PAPER, 650),
            text(36, 108, 'Matched synthetic session · four active surfaces', 21),
            text(36, 180, f'{min(saving):.0f}–{max(saving):.0f}% less RAM', 52, CYAN, 700),
            text(36, 220, 'Immediately after repeated native work', 24, PAPER)]
    for i, (stage, title) in enumerate((('mix_9', 'AFTER 10 TASK MIXES'), ('steady_promoted', 'AFTER 10 NATIVE ACTIONS'))):
        x, y, width = (36, 256+i*246, 528) if mobile else (36+i*576, 256, 552)
        body += [panel(x, y, width, 220), text(x+24, y+38, title, 20, CYAN, 650)]
        for j, (mode, name, color) in enumerate((('baseline', 'Stock · four WebKit views', BLUE), ('hybrid', 'Hybrid · 3 API + 1 WebKit', CYAN))):
            vals = [r[stage]['medianMiB'] for r in c3['arms'][mode]['stages']]
            yy = y+83+j*74
            body += [text(x+24, yy, name, 21), text(x+width-24, yy+29, span(vals)+' MiB', 24, PAPER, 600, 'end'),
                     f'<rect x="{x+24}" y="{yy+40}" width="{width-48}" height="7" rx="3.5" fill="#ffffff" fill-opacity="0.08"/>',
                     f'<rect x="{x+24}" y="{yy+40}" width="{(width-48)*sum(vals)/len(vals)/450:.2f}" height="7" rx="3.5" fill="{color}"/>']
    y = 792 if mobile else 528
    lines = (['All views retained. Promotion keeps the same document.', '3 reordered sessions per mode · native footprint + helpers', 'M1 Pro · 16 GiB · macOS 27.0.1 · MiB = 2²⁰ bytes', 'Local signed-in-like fixtures, not live-site measurements.'] if not mobile else
             ['All views retained; same-document promotion.', '3 reordered sessions per mode.', 'Native physical footprint, including helpers.', 'M1 Pro · 16 GiB · macOS 27.0.1', 'Local fixtures; not live-site measurements.'])
    for i, line in enumerate(lines):body.append(text(36, y+i*29, line, 20))
    desc = ('Matched local signed-in-like fixture, three reordered sessions per mode on M1 Pro. '
            'After ten task mixes, stock four-WebKit footprint was 371–379 MiB versus 75–76 MiB with three API views and one retained WebKit view. '
            'After ten native actions, stock was 438–443 MiB versus 221–222 MiB hybrid, a paired 49.3–50.1 percent reduction. '
            'All views retained. Native physical footprint includes client helpers, not summed RSS or globally deduplicated RAM. No live-site benchmark.')
    return svg(w, h, 'Lower memory with supported hybrid tasks', desc, ''.join(body))


def coverage(data, mobile=False):
    w, h = (600, 1130) if mobile else (1200, 650)
    d = data['explicitReads']['arms']['dom']
    body = [card(w, h), text(36, 64, 'Read first. Render when needed.', 32, PAPER, 650),
            text(36, 106, 'New bounded CLI / MCP reads · explicit opt-in', 23)]
    cards = [('engine_free_reads', 'API ONLY', '3 supported API views', 'No WebKit execution engine', CYAN),
             ('before_graphics', 'WINDOWLESS', '3 API + 1 live WebKit view', 'JavaScript and DOM retained', BLUE),
             ('post_native_burst', 'POST-PROMOTION', 'Same view after native work', 'Full native / pixel capability', VIOLET)]
    for i, (stage, label, context, note, color) in enumerate(cards):
        x, y, width = (36, 146+i*210, 528) if mobile else (36+i*384, 146, 360)
        values = [s['MiB'] for s in d['stages'][stage]]
        body += [panel(x, y, width, 190), text(x+24, y+36, label, 19, color, 650),
                 text(x+24, y+96, span(values, 1 if stage == 'engine_free_reads' else 0)+' MiB', 39, PAPER, 700), text(x+24, y+133, context, 21), text(x+24, y+165, note, 19)]
    y = 796 if mobile else 372
    body += [panel(36, y, w-72, 115), text(60, y+44, '180 reads · 0 host promotions', 29 if mobile else 34, CYAN, 650),
             text(60, y+82, '90 API + 90 DOM tasks · 3 candidate sessions', 21)]
    lines = (['Native / pixel work: one promotion per session; pixels matched.', 'C3 reads already stayed windowless. No new RAM reduction demonstrated.', 'Separate read cohort · native footprint + helpers · synthetic localhost fixture.'] if not mobile else
             ['Native / pixels: one promotion per session.', 'Same document and state; pixels matched.', 'C3 reads already stayed windowless.', 'No new RAM reduction demonstrated.', 'Separate cohort · footprint + helpers.', 'Synthetic localhost fixture; not live sites.'])
    yy = y+155
    for i, line in enumerate(lines):body.append(text(36, yy+i*29, line, 20))
    desc = ('Separate matched read cohort with three sessions per mode. Candidate footprint: 20.1–20.2 MiB for three API views without WebKit; '
            '68.2–71.4 MiB with a retained windowless JavaScript WebKit view; 225.2–228.0 MiB after native work. '
            'Across three candidate sessions, 90 API reads and 90 DOM read/find/link tasks passed without render-host promotion. '
            'Native and pixel work promoted once per session, preserving the same view and matching pixels. '
            'C3 equivalent reads also stayed windowless; no additional RAM reduction demonstrated. Not live-site measurements.')
    return svg(w, h, 'Explicit reads extend no-render task coverage', desc, ''.join(body))


def outputs():
    data = json.loads(DATA.read_text())
    return {f'low-memory-{name}{"-mobile" if mobile else ""}.svg': make(data, mobile)
            for name, make in (('comparison', comparison), ('reads', coverage)) for mobile in (False, True)}


if __name__ == '__main__':
    for name, source in outputs().items():
        (HERE/name).write_text(source)
        print(name)
