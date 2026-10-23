#!/usr/bin/env python3
"""Generate responsive documentation figures and their copyable text equivalents.

Run --import once to collect new Markdown tables and quantitative callouts. Edit
doc_figures.json for those figures afterward, then run this script to regenerate.
Benchmark panels read the retained measurement JSON directly.
"""
import argparse
import hashlib
import html
import json
import math
import re
import statistics
import textwrap
from pathlib import Path

from build import BLUE, CYAN, VIOLET, PAPER, MUTED, DIM, SANS, card, svg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST = HERE / 'doc_figures.json'
OUT = HERE / 'docs'
START = '<!-- umbra-figure: '
END = '<!-- /umbra-figure -->'
METRIC = re.compile(r'\d[\d,.]*(?:\s*(?:%|×|ms\b|MiB\b|GiB\b|MB\b|GB\b|seconds?\b|minutes?\b|hours?\b|runs?\b|tasks?\b|cases?\b|clients?\b|tests?\b|episodes?\b|samples?\b|workers?\b|states?\b))|\d+\s*/\s*\d+', re.I)


def documents():
    return sorted({ROOT / p for p in ['README.md', 'ROADMAP.md', 'CHANGELOG.md', 'sdk/README.md', 'bench/README.md', 'bench/external/README.md']} | set((ROOT / 'docs').rglob('*.md')))


def plain(value):
    value = re.sub(r'<br\s*/?>', '\n', value, flags=re.I)
    value = re.sub(r'!?\[([^]]+)\]\([^)]+\)', r'\1', value)
    value = re.sub(r'</?(?:strong|em|b|i|sup|sub)>', '', value)
    return html.unescape(value.replace('`', '').replace('**', '').replace('\\|', '|')).strip()


def cells(line):
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|') and not line.endswith('\\|'):
        line = line[:-1]
    parts, buf, tick, i = [], '', 0, 0
    while i < len(line):
        c = line[i]
        if c == '\\' and i + 1 < len(line):
            buf += line[i:i + 2]; i += 2; continue
        if c == '`':
            end = i + 1
            while end < len(line) and line[end] == '`': end += 1
            n = end - i
            tick = 0 if tick == n else n if tick == 0 else tick
            buf += line[i:end]; i = end; continue
        if c == '|' and not tick:
            parts.append(buf.strip()); buf = ''
        else:
            buf += c
        i += 1
    return parts + [buf.strip()]


def separator(line):
    values = cells(line)
    return len(values) > 1 and all(re.fullmatch(r':?-{3,}:?', v) for v in values)


def source_id(path, kind, source):
    slug = re.sub(r'[^a-z0-9]+', '-', path.with_suffix('').as_posix().lower()).strip('-')[:80]
    return f'{slug}-{kind}-{hashlib.sha256(source.encode()).hexdigest()[:10]}'


def ingest(manifest):
    changes = {}
    for path in documents():
        lines = path.read_text().splitlines(keepends=True)
        result, heading, fence, i = [], path.stem, None, 0
        while i < len(lines):
            line = lines[i]
            if line.startswith(START):
                end = next(n for n in range(i, len(lines)) if lines[n].strip() == END)
                result.extend(lines[i:end + 1]); i = end + 1; continue
            marker = re.match(r'^\s*(`{3,}|~{3,})', line)
            if marker:
                fence = None if fence and marker[1][0] == fence else marker[1][0] if not fence else fence
                result.append(line); i += 1; continue
            if fence:
                result.append(line); i += 1; continue
            if line.startswith('#'):
                heading = plain(line.lstrip('#').strip())
            if i + 1 < len(lines) and '|' in line and separator(lines[i + 1]):
                headers, rows, j = cells(line), [], i + 2
                while j < len(lines) and lines[j].strip().startswith('|'):
                    row = cells(lines[j])
                    if len(row) != len(headers):
                        raise ValueError(f'{path}:{j + 1}: {len(row)} cells, expected {len(headers)}')
                    rows.append(row); j += 1
                source = ''.join(lines[i:j]).rstrip()
                entry = {'kind': 'table', 'document': str(path.relative_to(ROOT)), 'title': heading, 'headers': headers, 'rows': rows}
            elif line.strip() and not line.lstrip().startswith(('#', '<', '    ')):
                j = i + 1
                if line.startswith('- '):
                    while j < len(lines) and lines[j].strip() and not lines[j].startswith(('- ', '#', '```', '~~~', '<!--')): j += 1
                else:
                    while j < len(lines) and lines[j].strip() and not lines[j].startswith(('#', '|', '```', '~~~', '<!--', '- ')): j += 1
                source = ''.join(lines[i:j]).rstrip()
                if not METRIC.search(re.sub(r'`[^`]+`', '', source)):
                    result.append(line); i += 1; continue
                entry = {'kind': 'facts', 'document': str(path.relative_to(ROOT)), 'title': heading, 'source': source}
            else:
                result.append(line); i += 1; continue
            ident = source_id(path.relative_to(ROOT), entry['kind'], source)
            manifest[ident] = entry
            result.append(f'{START}{ident} -->\n{END}\n')
            i = j
        changes[path] = ''.join(result)
    return changes


def lines(value, width, size):
    return [part for paragraph in plain(value).splitlines() for part in (textwrap.wrap(paragraph, max(8, int(width / (size * .64))), break_long_words=True, break_on_hyphens=False) or [''])]


def text(x, y, value, size=24, color=PAPER, width=1104, weight=400):
    return f'<text x="{x:g}" y="{y:g}" font-family="{SANS}" font-size="{size}" font-weight="{weight}" fill="{color}" data-max-width="{width:g}">{html.escape(str(value))}</text>'


def paragraph(x, y, value, width, size=24, color=PAPER, weight=400):
    wrapped = lines(value, width, size)
    return ''.join(text(x, y + n * (size + 10), line, size, color, width, weight) for n, line in enumerate(wrapped)), len(wrapped) * (size + 10)


def frame(width, height, title, desc, body):
    return svg(width, height, html.escape(title), html.escape(desc), card(width, height) + body)


def table_picture(entry, rows, mobile, page, pages):
    width, margin = (600, 36) if mobile else (1200, 48)
    usable = width - margin * 2
    body, used = paragraph(margin, 66, entry['title'], usable, 30, PAPER, 650)
    y = 66 + used + 12
    body += text(margin, y, f'REFERENCE / {page} OF {pages}', 18, BLUE, usable, 600)
    y += 38
    headers = entry['headers']
    if mobile:
        for row in rows:
            content, size = paragraph(margin + 20, y + 38, row[0], usable - 40, 26, BLUE, 600)
            cursor = y + 38 + size
            for label, value in zip(headers[1:], row[1:]):
                chunk, size = paragraph(margin + 20, cursor + 8, plain(label).upper(), usable - 40, 18, DIM, 600)
                content += chunk; cursor += size + 8
                chunk, size = paragraph(margin + 20, cursor, value, usable - 40)
                content += chunk; cursor += size + 18
            height = cursor - y + 6
            body += f'<rect x="{margin}" y="{y}" width="{usable}" height="{height}" rx="14" fill="#121a2c" stroke="#2b3852"/>' + content
            y += height + 18
    else:
        weights = [min(48, max(12, max(len(plain(row[n])) for row in [headers] + rows))) ** .65 for n in range(len(headers))]
        widths = [usable * weight / sum(weights) for weight in weights]
        header_height = max(len(lines(h, w - 28, 19)) for h, w in zip(headers, widths)) * 29 + 20
        x = margin
        for label, col in zip(headers, widths):
            chunk, _ = paragraph(x + 14, y + 24, plain(label).upper(), col - 28, 19, BLUE, 600)
            body += chunk; x += col
        y += header_height
        for index, row in enumerate(rows):
            height = max(len(lines(value, col - 28, 22)) for value, col in zip(row, widths)) * 32 + 26
            body += f'<rect x="{margin}" y="{y}" width="{usable}" height="{height}" rx="8" fill="{("#121a2c" if index % 2 == 0 else "#0d1423")}"/>'
            x = margin
            for n, (value, col) in enumerate(zip(row, widths)):
                chunk, _ = paragraph(x + 14, y + 30, value, col - 28, 22, PAPER if n else CYAN)
                body += chunk; x += col
            y += height + 4
    body += text(margin, y + 22, 'Copyable values and source links are below.', 18, MUTED, usable)
    desc = '; '.join(', '.join(f'{plain(label)}: {plain(value)}' for label, value in zip(headers, row)) for row in rows)
    return frame(width, y + 54, entry['title'], desc, body)


def facts_picture(entry, mobile):
    width, margin = (600, 36) if mobile else (1200, 48)
    usable = width - margin * 2
    body, height = paragraph(margin, 66, entry['title'], usable, 30, BLUE, 650)
    y = 66 + height + 18
    chunk, height = paragraph(margin, y, entry['source'], usable, 25 if mobile else 27)
    body += chunk
    return frame(width, y + height + 36, entry['title'], plain(entry['source']), body)


def competitive_picture(mobile):
    data = json.loads((ROOT / 'docs/evaluation/latency/results.json').read_text())
    assert not data['errors']
    width, margin = (600, 36) if mobile else (1200, 48)
    usable = width - 2 * margin
    title = 'Built for the agent loop.'
    body, used = paragraph(margin, 74, title, usable, 38, PAPER, 700)
    y = 74 + used
    chunk, used = paragraph(margin, y, 'Read. Click. Fill. Capture.', usable, 24, MUTED)
    body += chunk
    top = y + used + 26
    cell_width = usable if mobile else (usable - 24) / 2
    descriptions = []
    metrics = [('click', 'CLICKS + DOM UPDATE', BLUE, 'webkit'),
               ('snapshot', 'ACCESSIBILITY SNAPSHOTS', VIOLET, 'webkit'),
               ('fill', 'CONTROLLED FORM FILLING', CYAN, 'webkit'),
               ('screenshot', 'PNG SCREENSHOTS', BLUE, 'chromium')]
    for index, (metric, label, color, primary) in enumerate(metrics):
        values = data['metrics'][metric]
        native = values['umbra_socket']['p50']
        chromium, webkit = values['chromium']['p50'], values['webkit']['p50']
        for key in ['umbra_socket', 'chromium', 'webkit']:
            assert len(values[key]['samples']) == data['runs']
        assert webkit > native and chromium > native
        x = margin if mobile else margin + (index % 2) * (cell_width + 24)
        card_y = top + (index if mobile else index // 2) * 404
        first, second = (webkit, chromium) if primary == 'webkit' else (chromium, webkit)
        first_name, second_name = ('WebKit', 'Chromium') if primary == 'webkit' else ('Chromium', 'WebKit')
        inner = cell_width - 48
        body += f'<rect x="{x:g}" y="{card_y:g}" width="{cell_width:g}" height="380" rx="18" fill="#11192b" stroke="#2f3b55"/>'
        body += text(x + 24, card_y + 36, label, 20, color, inner, 650)
        body += text(x + 24, card_y + 116, f'{first / native:.1f}×', 78, PAPER, inner, 700)
        body += text(x + 24, card_y + 154, f'faster than Playwright {first_name}', 22, MUTED, inner)
        body += text(x + 24, card_y + 190, f'{second / native:.1f}× vs Playwright {second_name}', 22, color, inner, 600)
        for row, (name, value, fill) in enumerate([('Umbra daemon', native, CYAN), ('Playwright Chromium', chromium, BLUE), ('Playwright WebKit', webkit, VIOLET)]):
            baseline = card_y + 234 + row * 48
            body += text(x + 24, baseline, name, 20, MUTED, inner - 112)
            body += text(x + cell_width - 120, baseline, f'{value:.2f} ms', 20, PAPER, 100)
            body += f'<rect x="{x + 24:g}" y="{baseline + 10:g}" width="{inner:g}" height="6" rx="3" fill="#ffffff" fill-opacity="0.08"/>'
            body += f'<rect x="{x + 24:g}" y="{baseline + 10:g}" width="{inner * value / max(native, chromium, webkit):g}" height="6" rx="3" fill="{fill}"/>'
        descriptions.append(f'{plain(label)}: Umbra {native:.2f} ms; Playwright Chromium {chromium:.2f} ms ({chromium / native:.1f}×); Playwright WebKit {webkit:.2f} ms ({webkit / native:.1f}×)')
    y = top + (1616 if mobile else 808) + 12
    footer = f"Same local fixtures · {data['runs']}-run medians · {data['machine']['cpu']} · recorded {data['date'][:10]}."
    scope = f"Umbra daemon socket vs Playwright {data['playwright']} drivers. Excludes fresh CLI/MCP overhead. Clicks include each tool’s actionability policy."
    for words in [footer, scope]:
        chunk, used = paragraph(margin, y, words, usable, 21, MUTED)
        body += chunk
        y += used + 8
    description = '; '.join(descriptions) + '. ' + footer + ' ' + scope
    token_path = ROOT / 'docs/evaluation/observation-tokens/results.json'
    token_data = json.loads(token_path.read_text())
    audit = json.loads(token_path.with_name('verification.json').read_text())
    assert audit['resultsSha256'] == hashlib.sha256(token_path.read_bytes()).hexdigest() and not audit['errors']
    assert token_data['eligibleForHeadline'] and not token_data['pilot'] and not token_data['errors']
    summaries = token_data['summary']
    assert len(token_data['runs']) == 9 and all(len(r['states']) == 27 and not r['errors'] for r in token_data['runs'])
    totals = {arm: statistics.median(summaries[arm]['tokens']) for arm in summaries}
    reduction = math.floor(100 * min(1 - max(summaries['umbra']['tokens']) / min(summaries[arm]['tokens']) for arm in ['chromium', 'webkit']))
    assert reduction >= 10
    y += 18
    token_top = y
    token_body = text(margin + 24, y + 38, 'STATEFUL MCP OBSERVATIONS', 20, CYAN, usable - 48, 650)
    token_body += text(margin + 24, y + 112, f'{reduction}% fewer tokens', 42 if mobile else 54, PAPER, usable - 48, 700)
    y += 156
    chunk, used = paragraph(margin + 24, y, 'vs Playwright MCP on Chromium and WebKit', usable - 48, 23, CYAN, 600)
    token_body += chunk; y += used + 12
    for name, arm, color in [('Umbra: deltas + full text', 'umbra', CYAN), ('Playwright Chromium', 'chromium', BLUE), ('Playwright WebKit', 'webkit', VIOLET)]:
        token_body += text(margin + 24, y, name, 20, MUTED, usable - 200)
        token_body += text(width - margin - 156, y, f'{totals[arm]:,.0f} tokens', 20, PAPER, 132)
        token_body += f'<rect x="{margin + 24}" y="{y + 10}" width="{usable - 48}" height="6" rx="3" fill="#ffffff" fill-opacity="0.08"/>'
        token_body += f'<rect x="{margin + 24}" y="{y + 10}" width="{(usable - 48) * totals[arm] / max(totals.values()):g}" height="6" rx="3" fill="{color}"/>'
        y += 48
    token_scope = [
        'Complete task prose, form state and actionable controls retained.',
        f"{summaries['umbra']['statesPerRepeat']} local states × {len(summaries['umbra']['tokens'])} repeats per browser · median totals · {token_data['tokenizer']['encoding']} · {token_data['recordedUTC'][:10]} UTC.",
        f"Umbra opt-in observe deltas + full text on every state vs Playwright MCP {token_data['environment']['playwrightMCP']} full snapshots. Initial/reset snapshots included. Canonical result JSON only; setup/actions excluded. Not total agent or billed tokens.",
    ]
    for words in token_scope:
        chunk, used = paragraph(margin + 24, y, words, usable - 48, 20, MUTED)
        token_body += chunk; y += used + 8
    body += f'<rect x="{margin}" y="{token_top}" width="{usable}" height="{y - token_top + 12}" rx="18" fill="#11192b" stroke="#2f3b55"/>' + token_body
    description += f' STATEFUL MCP OBSERVATIONS: {reduction}% fewer canonical result tokens vs Playwright MCP Chromium and WebKit; Umbra {totals["umbra"]:,.0f}, Chromium {totals["chromium"]:,.0f}, WebKit {totals["webkit"]:,.0f}. ' + ' '.join(token_scope)
    return frame(width, y + 36, title, description, body)


def why_picture(mobile):
    width, margin = (600, 36) if mobile else (1200, 48)
    usable = width - 2 * margin
    title = 'Your browser. Agent ready.'
    body, used = paragraph(margin, 74, title, usable, 38, PAPER, 700)
    y = 74 + used
    chunk, used = paragraph(margin, y, 'Already native. Always in the flow.', usable, 24, MUTED)
    body += chunk
    top = y + used + 22
    cell_width = usable if mobile else (usable - 24) / 2
    benefits = [
        ('Native to your Mac', 'WebKit is already there. A small Swift app gives your agent direct browser commands.', BLUE),
        ('Sign in once. Keep going.', 'Persistent accounts in Umbra’s own store. Your agents pick up the session across commands.', VIOLET),
        ('Your screen stays yours', 'Browse invisibly in the background. Bring a page forward for sign-in or a human handoff.', CYAN),
        ('One session. Every tool.', 'Shell, MCP, Python, TypeScript and Swift share live tabs. Bring Codex, Claude Code or Cursor.', BLUE),
        ('Agents that work together', 'Named tabs, ownership leases, bounded admission, deadlines and cancellation coordinate parallel work.', VIOLET),
        ('Read less. Do more.', 'Observe changes, extract typed records, batch checked steps and resume explicit checkpoints.', CYAN),
    ]
    descriptions = []
    for index, (heading, detail, color) in enumerate(benefits):
        x = margin if mobile else margin + (index % 2) * (cell_width + 24)
        card_y = top + (index if mobile else index // 2) * 244
        body += f'<rect x="{x:g}" y="{card_y:g}" width="{cell_width:g}" height="224" rx="18" fill="#11192b" stroke="#2f3b55"/>'
        body += f'<rect x="{x + 24:g}" y="{card_y + 26:g}" width="32" height="4" rx="2" fill="{color}"/>'
        chunk, used = paragraph(x + 24, card_y + 66, heading, cell_width - 48, 28, color, 650)
        body += chunk
        chunk, _ = paragraph(x + 24, card_y + 66 + used + 8, detail, cell_width - 48, 23, PAPER)
        body += chunk
        descriptions.append(f'{heading.rstrip(".")}: {detail}')
    return frame(width, top + (6 if mobile else 3) * 244 + 12, title, ' '.join(descriptions), body)


def picture(desktop, mobile, document, alt):
    import os
    relative = lambda filename: Path(os.path.relpath(OUT / filename, (ROOT / document).parent)).as_posix()
    return f'<picture>\n  <source media="(max-width: 600px)" srcset="{relative(mobile)}">\n  <img src="{relative(desktop)}" width="100%" alt="{html.escape(alt, quote=True)}">\n</picture>'


def figure(entry, ident):
    files, images = {}, []
    chunks = [entry['rows'][n:n + 8] for n in range(0, len(entry['rows']), 8)] if entry['kind'] == 'table' else [None]
    for n, rows in enumerate(chunks, 1):
        names = []
        for mobile in (False, True):
            source = table_picture(entry, rows, mobile, n, len(chunks)) if rows is not None else facts_picture(entry, mobile)
            digest = hashlib.sha256(source.encode()).hexdigest()[:10]
            name = f'{ident}-{n}{"-mobile" if mobile else ""}-{digest}.svg'
            files[name] = source; names.append(name)
        images.append(picture(*names, entry['document'], entry['title'] + (f'; page {n} of {len(chunks)}' if len(chunks) > 1 else '') + '. Exact values and source links in the expandable text below.'))
    if entry['kind'] == 'table':
        original = '\n\n'.join('\n'.join(f'- **{label}:** {value}'.rstrip() for label, value in zip(entry['headers'], row)) for row in entry['rows'])
    else:
        original = entry['source']
    fallback = f'<details>\n<summary>Read the text, values and source links</summary>\n\n{original}\n\n</details>'
    block = f'{START}{ident} -->\n' + '\n\n'.join(images) + '\n\n' + fallback + f'\n{END}'
    return files, block


def outputs(manifest):
    import xml.etree.ElementTree as ET
    files, blocks = {}, {}
    for ident, entry in manifest.items():
        generated, block = figure(entry, ident)
        files.update(generated); blocks[ident] = block
    for kind, renderer in [('competitive-benchmarks', competitive_picture), ('why-umbra', why_picture)]:
        names = []
        for mobile in (False, True):
            source = renderer(mobile)
            name = f'{kind}{"-mobile" if mobile else ""}-{hashlib.sha256(source.encode()).hexdigest()[:10]}.svg'
            files[name] = source; names.append(name)
        description = ET.fromstring(files[names[0]]).find('{http://www.w3.org/2000/svg}desc').text
        blocks[kind] = picture(*names, 'README.md', description)
    return files, blocks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import', dest='collect', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    changes = ingest(manifest) if args.collect else {}
    files, blocks = outputs(manifest)
    rendered = {}
    for path in documents():
        source = changes.get(path, path.read_text())
        for ident, entry in manifest.items():
            if entry['document'] != str(path.relative_to(ROOT)): continue
            pattern = re.escape(START + ident + ' -->') + r'.*?' + re.escape(END)
            source, count = re.subn(pattern, lambda _: blocks[ident], source, flags=re.S)
            if count != 1: raise ValueError(f'{path}: missing/duplicate figure slot {ident}')
        if path == ROOT / 'README.md':
            for ident in ['competitive-benchmarks', 'why-umbra']:
                source = re.sub(r'<!-- runtime-panel: ' + ident + r' -->.*?<!-- /runtime-panel -->', lambda _: '<!-- runtime-panel: ' + ident + ' -->\n' + blocks[ident] + '\n<!-- /runtime-panel -->', source, flags=re.S)
        rendered[path] = source
    if args.collect:
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    OUT.mkdir(exist_ok=True)
    for path in OUT.glob('*.svg'):
        if path.name not in files: path.unlink()
    for name, source in files.items(): (OUT / name).write_text(source)
    for path, source in rendered.items():
        if path.read_text() != source: path.write_text(source)
    print(f'Generated {len(files)} responsive SVGs for {len(manifest)} documentation figures and two README panels')


if __name__ == '__main__':
    main()
