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
import re
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


def runtime_picture(kind, mobile):
    directory = ROOT / 'docs/evaluation/runtime'
    comparisons = json.loads((directory / 'primary-v2/summary.json').read_text())['pairs']
    pairs = [p for p in comparisons if p['route'] == 'matched' and p['concurrency'] == 1 and p['validCompletePair']]
    assert len(pairs) == 3
    observation = json.loads((directory / 'verification/final-observation/results.json').read_text())
    closeout = json.loads((directory / 'verification/closeout.json').read_text())
    width, margin = (600, 36) if mobile else (1200, 48)
    usable = width - 2 * margin
    if kind == 'runtime-wins':
        low, high = min(p['throughputRatio'] for p in pairs), max(p['throughputRatio'] for p in pairs)
        a, b = min(p['peakPhysicalFootprintReduction'] for p in pairs), max(p['peakPhysicalFootprintReduction'] for p in pairs)
        metrics = [(f'{low:.2f}–{high:.2f}×', 'More tasks per second', 'Same WebKit workload', BLUE),
                   (f'{a:.0%}–{b:.0%}', 'Less sampled peak memory', 'Daemon + responsible helpers', VIOLET)]
        title, subtitle = 'More work. Less weight.', 'Matched single-worker fixture · three complete paired repeats'
        footer = 'All measured tasks and warmups succeeded in each pair. Whole-grid targets remain unmet; heavy matched runs hit the RAM guard.'
    elif kind == 'runtime-observations':
        metrics = []
        for label, key, color in [('CLI JSON', 'cli-json-without-human-text', BLUE), ('MCP', 'mcp-tool-result', CYAN), ('Python SDK', 'python-sdk-raw-result', VIOLET)]:
            value = observation['summary'][key]
            assert value['fiftyPercentGate'] and value['completeValidJourney']
            metrics.append((f"{value['tokenReduction']:.1%}", label + ' · fewer tokens', f"{value['fullTokens']:,} → {value['deltaTokens']:,} tokens", color))
        title, subtitle = 'Smaller context. Same page.', 'Full → incremental responses · exact text, refs and metadata retained'
        footer = '16 states, including initial and navigation-reset full responses. Pinned tiktoken 0.12.0 / o200k_base. Observation responses only.'
    else:
        assert closeout['finalConcurrencyAllPassed'] and closeout['liveApp']
        metrics = [(str(closeout['fullRegression']['tests']), 'Regression tests', 'One expected skip', BLUE),
                   (str(closeout['fullRegression']['smokeChecks']), 'Smoke checks', 'All passed', CYAN),
                   (str(max(closeout['finalConcurrencyLevels'])), 'Concurrent clients', 'Ownership · progress · cancel', VIOLET)]
        title, subtitle = 'Built. Exercised. Verified.', 'Final build · native app, live loading and responsive inspector checked'
        footer = 'Concurrency is a semantic check, not full-workload performance. The requested release excludes a 24-hour soak.'
    body, used = paragraph(margin, 74, title, usable, 36, PAPER, 650)
    y = 74 + used
    chunk, used = paragraph(margin, y, subtitle, usable, 23, MUTED)
    body += chunk; y += used + 26
    columns = 1 if mobile else len(metrics)
    gap = 24
    cell_width = (usable - gap * (columns - 1)) / columns
    start = y
    for index, (value, label, caption, color) in enumerate(metrics):
        x = margin if mobile else margin + index * (cell_width + gap)
        top = start + index * 254 if mobile else start
        body += f'<rect x="{x:g}" y="{top:g}" width="{cell_width:g}" height="230" rx="18" fill="#11192b" stroke="#2f3b55"/>'
        body += text(x + 24, top + 78, value, 56 if len(value) > 8 else 66, color, cell_width - 48, 700)
        chunk, _ = paragraph(x + 24, top + 124, label, cell_width - 48, 24, PAPER, 600)
        body += chunk
        chunk, _ = paragraph(x + 24, top + 186, caption, cell_width - 48, 20, MUTED)
        body += chunk
    y = start + (len(metrics) * 254 if mobile else 254) + 12
    chunk, used = paragraph(margin, y, footer, usable, 22, MUTED)
    body += chunk
    return frame(width, y + used + 28, title, subtitle + '. ' + '; '.join(f'{value}: {label}; {caption}' for value, label, caption, _ in metrics) + '. ' + footer, body)


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
    for kind in ['runtime-wins', 'runtime-observations', 'runtime-verification']:
        names = []
        for mobile in (False, True):
            source = runtime_picture(kind, mobile)
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
            for ident in ['runtime-wins', 'runtime-observations', 'runtime-verification']:
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
    print(f'Generated {len(files)} responsive SVGs for {len(manifest)} documentation figures and three benchmark panels')


if __name__ == '__main__':
    main()
