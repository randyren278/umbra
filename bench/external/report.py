#!/usr/bin/env python3
"""Report every frozen episode, including infrastructure failures and denied calls."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

import numpy as np

import run


def quantiles(values):
    if not values:
        return None
    return {'n': len(values), 'p50': float(np.percentile(values, 50)),
            'p95': float(np.percentile(values, 95))}


def parse_case(root, case):
    work = root / f'{case["task"]}-{case["repeat"]}-{case["arm"]}'
    row = dict(case)
    for filename in ['run.json', 'score.json', 'cleanup.json', 'broker-error.json']:
        path = work / filename
        if path.exists():
            row[filename[:-5]] = json.loads(path.read_text())
    calls = [json.loads(l) for l in (work / 'calls.jsonl').read_text().splitlines()] if (work / 'calls.jsonl').exists() else []
    row['calls'] = len(calls)
    row['tool_seconds'] = [c['seconds'] for c in calls if c['tool'] != 'task']
    row['tool_errors'] = sum(bool(c['result'].get('isError')) for c in calls)
    unbatched = 0
    for call in calls:
        name, args = call['tool'], call['arguments']
        if name in run.UMBRA_ACT:
            unbatched += max(1, len(args.get('keys', []))) if name == 'press' else 1
        elif name in run.PW_ACT:
            unbatched += len(args.get('fields', [])) if name == 'browser_fill_form' else 1
        if call.get('done') and call.get('reward', 0) > 0:
            break
    row['unbatched_interaction_units'] = unbatched
    row['usage'] = {}
    row['trace_violations'] = []
    trace = work / 'transcript.jsonl'
    turn_started = False
    if trace.exists():
        for number, line in enumerate(trace.read_text().splitlines(), 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                row['trace_violations'].append(f'malformed transcript line {number}')
                continue
            if event.get('type') == 'turn.started':
                turn_started = True
            if event.get('type') == 'turn.completed':
                row['usage'] = event.get('usage', {})
            item = event.get('item', {})
            if event.get('type') not in ['item.started', 'item.completed']:
                continue
            if item.get('type') in ['command_execution', 'file_change']:
                row['trace_violations'].append(item['type'])
            if item.get('type') == 'mcp_tool_call' and item.get('server') != 'browser':
                row['trace_violations'].append('outside MCP: ' + str(item.get('server')))
        if not turn_started:
            row['trace_violations'].append('missing turn-start evidence')
    else:
        row['trace_violations'].append('missing transcript')
    score = row.get('score', {})
    row['accepted_success'] = bool('run' in row and score.get('browsergym_success')
                                   and not row['trace_violations'] and 'broker-error' not in row)
    row['strict_success'] = bool(score.get('strict_success') and row['accepted_success'])
    row['unbatched_budget_positive'] = bool(row['accepted_success'] and unbatched <= 10)
    row['unbatched_budget_strict'] = bool(row['strict_success'] and unbatched <= 10)
    if 'run' not in row:
        row['failure'] = 'pending'
    elif 'broker-error' in row or not score:
        row['failure'] = 'infrastructure / setup'
    elif row['trace_violations']:
        row['failure'] = 'invalid trace evidence'
    elif row['accepted_success']:
        row['failure'] = None
    elif score.get('done'):
        row['failure'] = 'upstream rejected'
    elif row['run']['exit'] == 'timeout':
        row['failure'] = 'wall time limit'
    elif score.get('actions', 0) >= 10:
        row['failure'] = 'action limit'
    elif score.get('observations', 0) >= 40:
        row['failure'] = 'observation limit'
    else:
        row['failure'] = 'agent stopped unfinished'
    return row


def report(root, output):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / 'manifest.json').read_text())
    rows = [parse_case(root, case) for case in manifest['cases']]
    task_names = sorted(set(r['task'] for r in rows))
    task_values = {}
    strict_values = {}
    summary = {}
    for arm in ['umbra', 'playwright']:
        group = [r for r in rows if r['arm'] == arm]
        successes = sum(r['accepted_success'] for r in group)
        task_values[arm] = np.array([statistics.mean(r['accepted_success'] for r in group if r['task'] == name) for name in task_names])
        strict_values[arm] = np.array([statistics.mean(r['strict_success'] for r in group if r['task'] == name) for name in task_names])
        summary[arm] = {
            'successes': successes, 'episodes': len(group),
            'finished': sum('run' in r for r in group),
            'rate': successes / len(group),
            'strict_successes': sum(r['strict_success'] for r in group),
            'strict_rate': sum(r['strict_success'] for r in group) / len(group),
            'unbatched_budget_positive': sum(r['unbatched_budget_positive'] for r in group),
            'unbatched_budget_strict': sum(r['unbatched_budget_strict'] for r in group),
            'failures': dict(Counter(r['failure'] for r in group if r['failure'])),
            'episode_seconds': quantiles([r['run']['seconds'] for r in group if 'run' in r]),
            'tool_seconds': quantiles([v for r in group for v in r['tool_seconds']]),
            'calls': quantiles([r['calls'] for r in group if 'run' in r]),
            'actions': quantiles([r['score']['actions'] for r in group if 'score' in r]),
            'input_tokens': quantiles([r['usage']['input_tokens'] for r in group if 'input_tokens' in r['usage']]),
            'cached_input_tokens': quantiles([r['usage']['cached_input_tokens'] for r in group if 'cached_input_tokens' in r['usage']]),
            'output_tokens': quantiles([r['usage']['output_tokens'] for r in group if 'output_tokens' in r['usage']]),
            'cleanup_failures': sum(not r.get('cleanup', {}).get('clean', False) for r in group if 'run' in r),
            'trace_violations': sum(bool(r['trace_violations']) for r in group),
            'splits': {},
        }
        for split in ['train', 'test']:
            part = [r for r in group if r['split'] == split]
            summary[arm]['splits'][split] = {'successes': sum(r['accepted_success'] for r in part), 'episodes': len(part)}
    samples = np.random.RandomState(42).randint(0, len(task_names), size=(10000, len(task_names)))
    for arm in summary:
        rates = task_values[arm][samples].mean(axis=1)
        summary[arm]['task_cluster_95pct_ci'] = np.percentile(rates, [2.5, 97.5]).tolist()
        summary[arm]['strict_task_cluster_95pct_ci'] = np.percentile(strict_values[arm][samples].mean(axis=1), [2.5, 97.5]).tolist()
    diff = (task_values['umbra'] - task_values['playwright'])[samples].mean(axis=1)
    summary['paired_difference'] = {
        'umbra_minus_playwright': float((task_values['umbra'] - task_values['playwright']).mean()),
        'task_cluster_95pct_ci': np.percentile(diff, [2.5, 97.5]).tolist(),
        'method': '10000 paired bootstrap samples of task types, RandomState(42); seeds within a task stay grouped',
    }
    strict_diff = (strict_values['umbra'] - strict_values['playwright'])[samples].mean(axis=1)
    summary['paired_difference']['strict_umbra_minus_playwright'] = float((strict_values['umbra'] - strict_values['playwright']).mean())
    summary['paired_difference']['strict_task_cluster_95pct_ci'] = np.percentile(strict_diff, [2.5, 97.5]).tolist()
    for name, value in [('manifest', manifest), ('results', rows), ('summary', summary)]:
        run.write(output / f'{name}.json', value)
    # Retain per-file digests for raw evidence in the original run directory.
    receipts = {}
    for path in root.rglob('*'):
        if path.is_file() and 'chrome' not in path.relative_to(root).parts:
            receipts[str(path.relative_to(root))] = run.digest(path)
    run.write(output / 'evidence-hashes.json', receipts)
    lines = [
        ('# MiniWoB++ serialized clipboard diagnostic' if manifest.get('purpose') else
         '# MiniWoB++ external evaluation'), '',
        ('Status: COMPLETE.' if all('run' in r for r in rows) else
         f'Status: IN PROGRESS — {sum("run" in r for r in rows)}/{len(rows)} episodes finished. Pending cases are not successes.'), '',
        'Pinned upstream tasks and BrowserGym setup/validation; fresh Codex sessions with filtered browser MCP tools.', '',
        '| Browser | BrowserGym successes | Full reward | Episode p50 / p95 | Broker call p50 / p95 |',
        '|---|---:|---:|---:|---:|',
    ]
    for arm in ['umbra', 'playwright']:
        s = summary[arm]
        episode, tool = s['episode_seconds'], s['tool_seconds']
        ci = s['task_cluster_95pct_ci']
        interval = '' if manifest.get('purpose') else f'; task CI {ci[0]:.1%}–{ci[1]:.1%}'
        lines.append(f'| {arm} | {s["successes"]}/{s["episodes"]} ({s["rate"]:.1%}{interval}) | {s["strict_successes"]}/{s["episodes"]} | '
                     + (f'{episode["p50"]:.1f} / {episode["p95"]:.1f} s' if episode else 'unavailable') + ' | '
                     + (f'{tool["p50"]*1000:.1f} / {tool["p95"]*1000:.1f} ms' if tool else 'unavailable') + ' |')
    d = summary['paired_difference']
    if not manifest.get('purpose'):
        lines += ['', f'Paired success difference (Umbra minus Playwright): {d["umbra_minus_playwright"]*100:.1f} percentage points, '
              f'95% task-cluster interval {d["task_cluster_95pct_ci"][0]*100:.1f} to {d["task_cluster_95pct_ci"][1]*100:.1f}.', '',
              f'Full-reward paired difference: {d["strict_umbra_minus_playwright"]*100:.1f} percentage points, '
              f'95% task-cluster interval {d["strict_task_cluster_95pct_ci"][0]*100:.1f} to {d["strict_task_cluster_95pct_ci"][1]*100:.1f}.', '']
    intervals_include_zero = all(lo <= 0 <= hi for lo, hi in
                                 [d['task_cluster_95pct_ci'], d['strict_task_cluster_95pct_ci']])
    decision = ('Both primary score intervals include zero: these outcomes do not establish superiority. '
                if intervals_include_zero else 'Inspect the reported uncertainty before interpreting the observed difference. ')
    decision += 'The asymmetric action budget prevents attributing the observed gap solely to browser quality.'
    lines += ['', decision if not manifest.get('purpose') else
              'Post-hoc supplementary diagnostic only: the two clipboard task types ran one episode at a time. This does not replace the primary 125-task campaign or isolate the cause of its different outcomes. Ten successes per arm across two task types do not imply certain reliability; the degenerate bootstrap intervals in summary.json are not meaningful uncertainty estimates.', '',
              '## Frozen setup', '',
              f'- Umbra revision: `{manifest["umbra_revision"]}`; binary SHA-256 `{manifest["binary_sha256"]}`.',
              f'- MiniWoB++ revision: `{manifest["miniwob_revision"]}`.',
              f'- BrowserGym revision: `{manifest["browsergym_revision"]}`.',
              f'- Model: `{manifest["model"]}`, `{manifest["reasoning_effort"]}` effort; `{manifest["agent"]}`.',
              f'- Task types: {len(task_names)}; {manifest["repeats"]} of the standard five repetitions; two arms.',
              ('- The clipboard subset was selected after inspecting the primary campaign. The original five seeds were reused; original full-campaign scores remain primary.' if manifest.get('purpose') else
               '- Upstream task order and RandomState(42) seed schedule preserved. No task excluded after seeing results.'),
              '- Ten interaction units as implemented by the frozen broker, forty observations, 120 seconds per episode including setup and final response. A native press-array counts as one unit; fill_form counts each field separately. This is not ten unbatched BrowserGym actions.',
              f'- Loopback HTTP only; disposable Umbra profiles and Chromium contexts. {manifest.get("concurrency", 4)} concurrent episodes.',
              '- No arbitrary JavaScript, filesystem tools, browser navigation, other MCP servers or account access in either arm.',
              '- BrowserGym success is its unchanged `RAW_REWARD_GLOBAL > 0` check on a terminated episode. Full reward requires exactly 1.0.',
              '- Attempts with missing/malformed traces, forbidden filesystem/command tools or another MCP server are invalidated by transcript audit. Started actions are audited even if they never completed.', '',
              '## Coverage and failures', '',
              '| Browser | Finished | Train success | Test success | Cleanup failures | Trace violations |',
              '|---|---:|---:|---:|---:|---:|']
    for arm in ['umbra', 'playwright']:
        s = summary[arm]
        train, test = s['splits']['train'], s['splits']['test']
        lines.append(f'| {arm} | {s["finished"]}/{s["episodes"]} | {train["successes"]}/{train["episodes"]} | {test["successes"]}/{test["episodes"]} | {s["cleanup_failures"]} | {s["trace_violations"]} |')
    lines += ['', '| Failure classification | Umbra | Playwright |', '|---|---:|---:|']
    failures = sorted(set(summary['umbra']['failures']) | set(summary['playwright']['failures']))
    for reason in failures:
        lines.append(f'| {reason} | {summary["umbra"]["failures"].get(reason, 0)} | {summary["playwright"]["failures"].get(reason, 0)} |')
    lines += ['', '## Unbatched key-budget sensitivity', '',
              'Post-hoc conservative diagnostic, not a replacement score: expand each key in a native press-array into its own interaction unit and exclude credit from outcomes reached after ten such units. Other interaction commands count once; fill_form counts fields. This does not rerun the agents with an enforced normalized budget, and counts individual character keys separately even where another typing API could express a string in one action.', '',
              '| Browser | Positive reward within ten expanded units | Full reward within ten expanded units |',
              '|---|---:|---:|']
    for arm in ['umbra', 'playwright']:
        s = summary[arm]
        lines.append(f'| {arm} | {s["unbatched_budget_positive"]}/{s["episodes"]} | {s["unbatched_budget_strict"]}/{s["episodes"]} |')
    lines += ['', ('The original full-campaign score remains primary. This supplementary diagnostic retains the same asymmetric interaction budget.' if manifest.get('purpose') else
                  'The primary score remains the frozen protocol score. Its batching asymmetry prevents attributing a score difference solely to browser quality. A prospective comparison with normalized actions is required for that claim.')]
    diagnostic = root / 'supplementary-diagnostic.json'
    if diagnostic.exists():
        supplement = json.loads(diagnostic.read_text())
        lines += ['', '## Serialized clipboard diagnostic', '',
                  f'A separate post-hoc run repeated both clipboard task types with the original five seeds, one episode at a time. Umbra earned full reward on {supplement["umbra_full_reward"]}/10 cases; Playwright earned {supplement["playwright_full_reward"]}/10. These twenty episodes do not replace any primary result. Strategy and machine load also vary, so this does not identify clipboard contention as the sole cause of primary failures.', '',
                  f'[Diagnostic report]({supplement["report"]}).']
    lines += ['', '## Per-task results', '', '| Task | Split | Umbra | Playwright |', '|---|---|---:|---:|']
    for name in task_names:
        group = [r for r in rows if r['task'] == name]
        counts = [f'{sum(r["accepted_success"] for r in group if r["arm"] == arm)}/{sum(r["arm"] == arm for r in group)}' for arm in ['umbra', 'playwright']]
        lines.append(f'| {name} | {group[0]["split"]} | {counts[0]} | {counts[1]} |')
    lines += ['', '## Interpretation limits', '',
              'This is a comparison on standard external tasks with their original grader. It uses an adapted MCP action/observation protocol, so these scores are not an official BrowserGym or MiniWoB leaderboard submission. Tool capabilities, browser engines and screenshots differ; the Playwright baseline uses Chromium. The 120-second cap is stricter than BrowserGym’s page timer. Scores support claims about this frozen agent/tool setup only.', '',
              'The bootstrap groups all repetitions of each task together. Episode-level independence is not assumed. These intervals describe uncertainty over task types, not performance on every real website. Tasks are public and may be present in model training data.', '',
              ('Native copy/paste uses the Mac’s shared clipboard, which browser profiles do not isolate. These diagnostic episodes ran serially, but the clipboard remained shared with the user. Agent strategy and machine load also differed; serial success does not prove clipboard contention caused the primary failures.' if manifest.get('purpose') else
               'Native copy/paste uses the Mac’s shared clipboard, which browser profiles do not isolate. Clipboard tasks ran concurrently; their outcomes cannot establish isolated clipboard reliability. They remain in the primary frozen denominator.'), '',
              'Wall times include model inference and run under shared machine load. Broker call times include MCP handling, action waits, two upstream validation calls and image hashing; they are not pure engine latency. The Python Playwright control connection uses the upstream 100 ms slow-motion setting; the Umbra control connection has no equivalent delay. Broker durations therefore do not support a browser speedup claim. Installation is outside the timing. Codex reports token usage but no dollar cost for this subscription run.', '',
              'The disposable environment uses Playwright Python 1.63.0 with browsergym-core 0.14.3, overriding that package’s legacy Playwright 1.44 pin. The upstream task code is imported from the pinned source checkout and calibrated with positive, negative and unfinished controls; the Gym runtime is not used. This environment is an adapter execution, not an unmodified BrowserGym installation.', '',
              '### Primary sources', '',
              '- [MiniWoB++](https://github.com/Farama-Foundation/miniwob-plusplus): external HTML tasks.',
              '- [BrowserGym MiniWoB adapter](https://github.com/ServiceNow/BrowserGym/blob/' + manifest['browsergym_revision'] + '/browsergym/miniwob/src/browsergym/miniwob/base.py): unchanged setup and reward validation.',
              '- [BrowserGym benchmark configuration](https://github.com/ServiceNow/BrowserGym/blob/' + manifest['browsergym_revision'] + '/browsergym/experiments/src/browsergym/experiments/benchmark/configs.py): 125 tasks, five repeats and ten actions.',
              '- [Playwright MCP](https://github.com/microsoft/playwright-mcp): comparator.',
              '- [WebdriverIO benchmark](https://github.com/webdriverio/benchmark): Online-Mind2Web comparison; not executed because it requires live websites, gated tasks and paid judging.', '',
              f'Raw run directory: `{root}`. `evidence-hashes.json` identifies the exact raw files. `results.json` retains every episode; `summary.json` holds aggregate measurements.', '']
    (output / 'RESULTS.md').write_text('\n'.join(lines))
    print(json.dumps({k: {key: value for key, value in val.items() if key in ['successes', 'episodes', 'finished', 'failures', 'cleanup_failures']} for k, val in summary.items()}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    report(args.root, args.out)
