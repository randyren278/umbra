#!/usr/bin/env python3
"""Report a frozen baseline/candidate Umbra campaign on upstream MiniWoB tasks."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

import numpy as np

import report
import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    root, output = Path(args.root).resolve(), Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['arms'] == ['baseline', 'candidate']
    rows = [report.parse_case(root, case) for case in manifest['cases']]
    for row in rows:
        trace = root / f'{row["task"]}-{row["repeat"]}-{row["arm"]}' / 'transcript.jsonl'
        for line in trace.read_text().splitlines() if trace.exists() else []:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('type') in ['item.started', 'item.completed']:
                item = event.get('item', {})
                kind = item.get('type')
                if kind not in ['agent_message', 'reasoning', 'error', 'mcp_tool_call']:
                    row['trace_violations'].append('unexpected item: ' + str(kind))
                if kind == 'mcp_tool_call' and item.get('tool') not in run.UMBRA_ACT | run.UMBRA_OBSERVE | {'task'}:
                    row['trace_violations'].append('unexpected browser tool: ' + str(item.get('tool')))
        if row['trace_violations']:
            row['accepted_success'] = row['strict_success'] = False
            row['failure'] = 'invalid trace evidence'
    names = sorted({row['task'] for row in rows})
    summary, values = {}, {}
    for arm in manifest['arms']:
        group = [row for row in rows if row['arm'] == arm]
        summary[arm] = {
            'episodes': len(group), 'finished': sum('run' in row for row in group),
            'successes': sum(row['accepted_success'] for row in group),
            'strict_successes': sum(row['strict_success'] for row in group),
            'failures': dict(Counter(row['failure'] for row in group if row['failure'])),
            'cleanup_failures': sum(not row.get('cleanup', {}).get('clean') for row in group if 'run' in row),
            'trace_violations': sum(bool(row['trace_violations']) for row in group),
            'episode_seconds': report.quantiles([row['run']['seconds'] for row in group if 'run' in row]),
            'tool_seconds': report.quantiles([v for row in group for v in row['tool_seconds']]),
            'tokens': {key: sum(row['usage'].get(key, 0) for row in group) for key in
                       ['input_tokens', 'cached_input_tokens', 'output_tokens']},
        }
        values[arm] = {metric: np.array([statistics.mean(row[metric] for row in group if row['task'] == name)
                                        for name in names]) for metric in ['accepted_success', 'strict_success']}
    samples = np.random.RandomState(42).randint(0, len(names), size=(10000, len(names)))
    summary['paired_difference'] = {}
    for metric in ['accepted_success', 'strict_success']:
        diff = values['candidate'][metric] - values['baseline'][metric]
        summary['paired_difference'][metric] = {
            'candidate_minus_baseline': float(diff.mean()),
            'task_cluster_95pct_ci': np.percentile(diff[samples].mean(axis=1), [2.5, 97.5]).tolist(),
        }
    complete = all('run' in row for row in rows)
    valid = complete and all(not summary[arm]['cleanup_failures'] and not summary[arm]['trace_violations'] and
                             not summary[arm]['failures'].get('infrastructure / setup', 0) for arm in manifest['arms'])
    summary['complete'] = complete
    full = len(names) == 125 and manifest['repeats'] == 5 and len(rows) == 1250
    distinct = len(set(manifest['binary_hashes'].values())) == 2
    summary['full_standard_campaign'] = full
    summary['distinct_binaries'] = distinct
    summary['observed_no_success_regression'] = bool(full and distinct and valid and all(
        summary['candidate'][key] >= summary['baseline'][key] for key in ['successes', 'strict_successes']))
    summary['interpretation'] = ('Observed aggregate score comparison only. A nonnegative difference does not prove '
                                 'statistical noninferiority, per-task equivalence or reliability outside this frozen suite.')
    for name, value in [('manifest', manifest), ('results', rows), ('summary', summary)]:
        run.write(output / f'{name}.json', value)
    run.write(output / 'evidence-hashes.json', {str(path.relative_to(root)): run.digest(path)
              for path in root.rglob('*') if path.is_file()})
    lines = ['# Umbra runtime: external MiniWoB comparison', '',
             ('Full standard campaign with distinct frozen binaries.' if full and distinct else
              'PLUMBING DIAGNOSTIC ONLY. This is a selected task subset and/or both arms use the same binary; it cannot establish runtime non-regression.'), '',
             f'Status: {"COMPLETE" if complete else "IN PROGRESS"}. '
             f'{sum("run" in row for row in rows)}/{len(rows)} frozen episodes finished.', '',
             'Unchanged pinned BrowserGym setup/validation, 125 task types in a full run, five seeded repeats per arm. '
             'Both arms use Umbra/WebKit through the same filtered MCP broker. Every frozen episode stays in the denominator.', '',
             '| Arm | Positive reward | Full reward | Finished | Cleanup failures | Invalid traces |',
             '|---|---:|---:|---:|---:|---:|']
    for arm in manifest['arms']:
        item = summary[arm]
        lines.append(f'| {arm} | {item["successes"]}/{item["episodes"]} | {item["strict_successes"]}/{item["episodes"]} | '
                     f'{item["finished"]}/{item["episodes"]} | {item["cleanup_failures"]} | {item["trace_violations"]} |')
    lines += ['', '| Metric | Candidate minus baseline | 95% task-cluster interval |', '|---|---:|---:|']
    for metric, item in summary['paired_difference'].items():
        low, high = item['task_cluster_95pct_ci']
        lines.append(f'| {metric} | {item["candidate_minus_baseline"] * 100:.2f} pp | {low * 100:.2f} to {high * 100:.2f} pp |')
    lines += ['', summary['interpretation'], '',
              'Bootstrap: 10,000 paired task-type samples, RandomState(42); all repetitions stay grouped. '
              'Public tasks may be present in model training. This adapted MCP protocol is not an official BrowserGym leaderboard submission.', '',
              f'Model: `{manifest["model"]}`, `{manifest["reasoning_effort"]}` effort, `{manifest["agent"]}`. '
              f'Task types: {len(names)}; repetitions: {manifest["repeats"]}; episode cap: {manifest["seconds_per_episode"]} seconds. '
              'Ten interaction requests and forty observations per episode; a native press-array counts once. '
              'Wall time includes model inference and shared load. Broker time includes validation and is not pure engine latency.', '',
              'Native copy/paste uses the shared macOS clipboard. Profiles isolate browser data, but do not isolate that clipboard. '
              'No arbitrary scripts, filesystem tools, source, reward or upstream internals are exposed to the model. '
              'The trusted controller alone calls upstream setup and validate. Exact binary, harness, source and raw-file hashes are retained.', '',
              '| Arm | Binary SHA-256 |', '|---|---|']
    for arm in manifest['arms']:
        lines.append(f'| {arm} | `{manifest["binary_hashes"][arm]}` |')
    lines += ['', '| Failure | Baseline | Candidate |', '|---|---:|---:|']
    for failure in sorted(set(summary['baseline']['failures']) | set(summary['candidate']['failures'])):
        lines.append(f'| {failure} | {summary["baseline"]["failures"].get(failure, 0)} | {summary["candidate"]["failures"].get(failure, 0)} |')
    lines += ['', '| Task | Split | Baseline | Candidate |', '|---|---|---:|---:|']
    for name in names:
        group = [row for row in rows if row['task'] == name]
        counts = [f'{sum(row["accepted_success"] for row in group if row["arm"] == arm)}/'
                  f'{sum(row["arm"] == arm for row in group)}' for arm in manifest['arms']]
        lines.append(f'| {name} | {group[0]["split"]} | {counts[0]} | {counts[1]} |')
    lines += ['', f'Raw evidence: `{root}`.', '']
    (output / 'RESULTS.md').write_text('\n'.join(lines))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
