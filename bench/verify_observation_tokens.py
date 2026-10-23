#!/usr/bin/env python3
"""Audit retained MCP token evidence, including deliberate quality failures."""
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
import observation_tokens as study
import tiktoken


def main():
    folder = study.PROTOCOL.parent
    path = folder / 'results.json'
    data = json.loads(path.read_text())
    protocol = json.loads(study.PROTOCOL.read_text())
    assert not data['errors'] and not data['pilot'] and data['eligibleForHeadline']
    for key, source in [('protocolSha256', study.PROTOCOL), ('fixtureSha256', study.PAGE),
                        ('harnessSha256', Path(study.__file__)), ('runtimeHarnessSha256', Path(study.runtime.__file__))]:
        assert data[key] == study.runtime.digest(source), (key, 'source drift')
    encoding = tiktoken.get_encoding(protocol['tokenizer']['encoding'])
    expected_runs = [(repeat, arm) for repeat, order in enumerate(protocol['orders']) for arm in order]
    assert [(r['repeat'], r['arm']) for r in data['runs']] == expected_runs
    states = [(fixture, step, label) for fixture in protocol['fixtures'] for step, label in enumerate(protocol['states'])]
    totals, full_totals, fixture_totals = defaultdict(list), defaultdict(list), defaultdict(lambda: defaultdict(list))
    reference_states = [r['expected'] for r in data['runs'][0]['states']]
    negative_checks = set()
    for run in data['runs']:
        assert not run['errors'] and all(v for k, v in run['cleanup'].items() if k != 'profile')
        assert [(r['fixture'], r['step'], r['label']) for r in run['states']] == states
        assert [r['expected'] for r in run['states']] == reference_states
        previous = None
        fixture_counts = defaultdict(int)
        for row in run['states']:
            for payload, counts in [('response', 'counts'), ('fullResponse', 'fullCounts')]:
                raw = json.dumps(row[payload], sort_keys=True, separators=(',', ':'))
                assert row[counts] == {'tokens': len(encoding.encode(raw)), 'bytes': len(raw.encode())}
            if run['arm'] == 'umbra':
                observation, supplement = row['response']
                full_observation, full_supplement = row['fullResponse']
                assert supplement == full_supplement
                metadata = observation['structuredContent']
                keys = ['refs', 'url', 'title', 'truncated', 'depthLimited', 'doc']
                if metadata['mode'] == 'delta':
                    assert previous
                    lines = previous['text'].split('\n')
                    patch = metadata['patch']
                    lines[patch['start']:patch['start'] + patch['delete']] = patch['lines']
                    text = '\n'.join(lines)
                    inherited = {**previous['metadata'], **{k: metadata[k] for k in keys if k in metadata}}
                else:
                    text = study.native_text(observation)
                    inherited = {k: metadata[k] for k in keys}
                assert text == study.native_text(full_observation)
                assert inherited == {k: full_observation['structuredContent'][k] for k in keys}
                assert not inherited['truncated'] and not inherited['depthLimited']
                if row['step'] in [0, len(protocol['states']) - 1]:
                    assert metadata['mode'] == 'full'
                    if previous:
                        assert metadata['reset'] and inherited['doc'] != previous['metadata']['doc']
                previous = {'text': text, 'metadata': inherited}
                text += '\n' + study.native_text(supplement)
            else:
                text = study.native_text(row['response'])
            assert text == row['reconstructedText']
            assert not row['semanticMissing'] and not study.check_semantics(text, row['expected'])
            without_refs = re.sub(r'\[ref=[^\]]+\]', '', text)
            assert 'actionable refs' in study.check_semantics(without_refs, row['expected'])
            negative_checks.add('missing actionable refs rejected')
            required = row['expected']['visible'][0]
            assert study.check_semantics(text.replace(required, ''), row['expected'])
            negative_checks.add('missing task information rejected')
            if row['expected']['checked']:
                assert 'checkbox state' in study.check_semantics(text.replace('[checked]', ''), row['expected'])
                negative_checks.add('missing checked state rejected')
            fixture_counts[row['fixture']] += row['counts']['tokens']
        totals[run['arm']].append(sum(s['counts']['tokens'] for s in run['states']))
        full_totals[run['arm']].append(sum(s['fullCounts']['tokens'] for s in run['states']))
        for fixture, tokens in fixture_counts.items():
            fixture_totals[fixture][run['arm']].append(tokens)
    for arm in totals:
        assert data['summary'][arm] == {'tokens': totals[arm], 'fullTokens': full_totals[arm], 'statesPerRepeat': len(states)}
    reductions = {arm: 1 - max(totals['umbra']) / min(totals[arm]) for arm in ['chromium', 'webkit']}
    assert all(value >= protocol['headlineMinimumReduction'] for value in reductions.values())
    receipt = {'verifiedUTC': datetime.now(timezone.utc).isoformat(), 'resultsSha256': study.runtime.digest(path),
               'validatorSha256': study.runtime.digest(__file__), 'observations': len(states) * len(data['runs']),
               'allResponseTokenCountsRecomputed': True, 'allRequiredInformationPresent': True,
               'allCrossEngineDOMStatesMatch': True, 'allDeltaTextAndMetadataMatchFullResults': True,
               'allOwnedProfilesCleaned': True, 'negativeChecks': sorted(negative_checks),
               'totals': dict(totals), 'fullTotals': dict(full_totals), 'perFixtureTotals': dict(fixture_totals),
               'worstCaseReduction': reductions, 'errors': []}
    study.runtime.write(folder / 'verification.json', receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
