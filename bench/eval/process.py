#!/usr/bin/env python3
"""Process checks for `codex exec` candidates, read from each one's JSON transcript.

  process.py RUN [RUN ...]     one line per candidate, then a summary per run (JSON with --json)

Critical failures: driving Safari or AppleScript, leaving its own profile (--profile or
UMBRA_PROFILE in a command), reading this repo's sources (the graders and the site's answers),
and any browser or screen tool other than Umbra (another MCP server, Playwright, Chrome, Firefox,
screencapture). Also counted: tool calls, `umbra help` lookups, `sleep` instead of `wait`, and
fetching the site without the browser (curl and the like, worth knowing but not critical).
"""
import json, re, statistics, sys
from pathlib import Path

ROOT = Path('/private/tmp/umbra-eval')
REPO = Path(__file__).resolve().parents[2]
# The repo however a command names it: absolute, from ~ or $HOME, in any case (macOS paths ignore it),
# or by a cd into it. Only the skill is fair to read.
HOME_RELATIVE = str(REPO.relative_to(Path.home())) if REPO.is_relative_to(Path.home()) else None
REPO_PATH = '(?:' + '|'.join([re.escape(str(REPO))] + ([rf'(?:~|\$HOME|\$\{{HOME\}})/{re.escape(HOME_RELATIVE)}'] if HOME_RELATIVE else [])) + ')'
CRITICAL = [
    ('Safari or AppleScript', re.compile(r'osascript|applescript|safari', re.I)),
    ('left its profile', re.compile(r'--profile\b|UMBRA_PROFILE=')),
    # The repo itself (not a sibling such as umbra-old), except the skill; and the answer sources by name, however reached.
    ('read the repo', re.compile(REPO_PATH + r'(?!/skill(?:/SKILL\.md)?(?:[\s\'"]|$))(?=[/\s\'"]|$)', re.I)),
    ('read the repo', re.compile(r'\bgrade\.py\b|\btasks\.json\b|bench/site\b', re.I)),
    ('another browser or screen tool', re.compile(r'playwright|chrom(e|ium)|firefox|screencapture|open -[ab]\b', re.I)),
]


def check(work):
    events = [json.loads(line) for line in (work / 'transcript.jsonl').read_text().splitlines() if line.startswith('{')]
    items = [e['item'] for e in events if e.get('type') == 'item.completed']
    commands = [i.get('command', '') for i in items if i.get('type') == 'command_execution']
    tools = [f"{i.get('server')}/{i.get('tool')}" for i in items if i.get('type') == 'mcp_tool_call']
    critical = sorted({name for name, pattern in CRITICAL for c in commands if pattern.search(c)}
                      | {f'MCP {t}' for t in tools if not t.startswith('umbra/')})
    run = json.loads((work / 'run.json').read_text()) if (work / 'run.json').exists() else {}
    usage = next((e['usage'] for e in events if e.get('type') == 'turn.completed'), {})
    return {'task': work.name, 'calls': len(commands) + len(tools), 'mcp': len(tools),
            'help': sum('umbra help' in c or 'umbra --help' in c for c in commands),
            'sleep': sum(bool(re.search(r'\bsleep\b', c)) for c in commands),
            'direct_fetch': sum(bool(re.search(r'\b(curl|wget|urllib|requests\.get)\b', c)) for c in commands),
            'critical': critical, 'seconds': run.get('seconds'), 'exit': run.get('exit'),
            'output_tokens': usage.get('output_tokens')}


def main(runs, as_json):
    report = {}
    for run in runs:
        folder = Path(run) if Path(run).is_absolute() else ROOT / run
        if not folder.is_dir():
            sys.exit(f'no run at {folder}')
        rows = [check(work) for work in sorted(folder.iterdir()) if (work / 'transcript.jsonl').exists()]
        if not rows:
            sys.exit(f'no transcripts in {folder}: run `eval.py run-codex --run {run}` first')
        report[run] = rows
        if as_json:
            continue
        for r in rows:
            flags = ', '.join(r['critical']) or '-'
            print(f"{run:14} {r['task']:22} calls {r['calls']:3}  help {r['help']}  sleep {r['sleep']}  "
                  f"fetch {r['direct_fetch']}  {r['seconds']} s  critical: {flags}")
        print(f"{run}: {len(rows)} runs, {sum(bool(r['critical']) for r in rows)} with critical failures, "
              f"median calls {statistics.median(r['calls'] for r in rows)}, total calls {sum(r['calls'] for r in rows)}, "
              f"help lookups {sum(r['help'] for r in rows)}, runs using sleep {sum(bool(r['sleep']) for r in rows)}, "
              f"runs fetching directly {sum(bool(r['direct_fetch']) for r in rows)}, "
              f"median {statistics.median(r['seconds'] or 0 for r in rows)} s\n")
    if as_json:
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--json']
    if not args:
        sys.exit(__doc__)
    main(args, '--json' in sys.argv)
