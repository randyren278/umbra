#!/usr/bin/env python3
"""Count real MCP observation results on frozen localhost page states.

Uses the runtime harness's isolated Umbra lifecycle. No model calls, real sites,
installed Umbra or owner profiles. See the adjacent protocol for claim limits.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime
sys.path.insert(0, str(runtime.ROOT / 'tests'))
from server import Server

ROOT = runtime.ROOT
PROTOCOL = ROOT / 'docs/evaluation/observation-tokens/protocol.json'
PAGE = Path(__file__).with_name('observation_tokens.html')


class PlaywrightMCP(runtime.ObservationMCP):
    def __init__(self, cli, engine, out):
        out.mkdir(parents=True, exist_ok=True)
        self.log = (out / 'mcp.log').open('wb')
        self.process = subprocess.Popen([
            'node', str(cli), '--browser', 'chrome' if engine == 'chromium' else 'webkit',
            '--headless', '--isolated', '--viewport-size', '1280x900',
            '--snapshot-mode', 'full', '--output-dir', str(out),
            *(['--executable-path', executable(cli)] if engine == 'chromium' else []),
        ], env=os.environ.copy(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.inbox = queue.Queue()
        self.serial = 0
        def read():
            try:
                for line in self.process.stdout:
                    self.inbox.put(json.loads(line))
            except Exception as exc:
                self.inbox.put(exc)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                   'clientInfo': {'name': 'observation-token-study', 'version': '1'}})
            self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        except Exception:
            self.close()
            raise

    def tool(self, name, **args):
        return self.rpc('tools/call', {'name': name, 'arguments': args})['result']


def executable(cli):
    code = 'process.stdout.write(require("playwright-core").chromium.executablePath())'
    return subprocess.check_output(['node', '-e', code], cwd=cli.parent, text=True)


def count(encoding, value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'))
    return {'tokens': len(encoding.encode(raw)), 'bytes': len(raw.encode())}


def native_text(result):
    return '\n'.join(part['text'] for part in result['content'] if part['type'] == 'text')


def evaluated(result):
    match = re.search(r'### Result\n(.*?)\n###', native_text(result), re.S)
    if not match:
        raise RuntimeError('missing Playwright evaluation result: ' + native_text(result)[:600])
    return json.loads(match[1])


def check_semantics(text, expected):
    # All task prose, table cells, labels, editable values and dynamic outcomes
    # must be available to the consumer, even when inherited from a baseline.
    normalized = re.sub(r'\s+', ' ', text)
    missing = [s for s in expected['visible'] if re.sub(r'\s+', ' ', s) not in normalized]
    if expected['entry'] and expected['entry'] not in text:
        missing.append('current textbox value')
    if expected['checked'] != ('[checked]' in text):
        missing.append('checkbox state')
    if expected['expanded'] != ('[expanded]' in text):
        missing.append('menu expanded state')
    if not re.search(r'\[ref=[^\]]+\]', text):
        missing.append('actionable refs')
    return missing


def run_arm(arm, repeat, protocol, site, args, encoding, out):
    work = out / f'{repeat}-{arm}'
    work.mkdir(parents=True)
    session = client = mcp = None
    record = {'arm': arm, 'repeat': repeat, 'states': [], 'errors': []}
    try:
        if arm == 'umbra':
            session = runtime.Session(args.umbra, work / 'browser')
            client = runtime.Client(session)
            mcp = runtime.ObservationMCP(session)
        else:
            mcp = PlaywrightMCP(args.playwright_mcp, arm, work)
        previous = None
        for fixture in protocol['fixtures']:
            url = site.url('/' + fixture)
            if arm == 'umbra':
                client.call('open', url=url)
                client.call('viewport', width=1280, height=900)
            else:
                mcp.tool('browser_navigate', url=url)
                version = evaluated(mcp.tool('browser_evaluate', function='() => navigator.userAgent'))
            for step, label in enumerate(protocol['states']):
                reset = step == len(protocol['states']) - 1
                if reset:
                    if arm == 'umbra':
                        client.call('open', url=url + '?reset=1')
                    else:
                        mcp.tool('browser_navigate', url=url + '?reset=1')
                script = f'return window.advance({0 if reset else step})'
                if arm == 'umbra':
                    expected = client.call('eval', script=script)['value']
                    full = mcp.observe({})['result']
                    observed = mcp.observe({'since': previous['baseline']})['result'] if previous else full
                    payload = observed['structuredContent']
                    if payload['mode'] == 'delta':
                        lines = previous['text'].split('\n')
                        patch = payload['patch']
                        lines[patch['start']:patch['start'] + patch['delete']] = patch['lines']
                        reconstructed = '\n'.join(lines)
                        metadata = {**previous['metadata'], **{k: payload[k] for k in previous['metadata'] if k in payload}}
                    else:
                        reconstructed = native_text(observed)
                        metadata = {k: payload[k] for k in ['refs', 'url', 'title', 'truncated', 'depthLimited', 'doc']}
                    assert reconstructed == native_text(full), 'delta reconstruction differs from full snapshot'
                    assert metadata == {k: full['structuredContent'][k] for k in metadata}, 'delta metadata mismatch'
                    assert not metadata['truncated'] and not metadata['depthLimited'], 'snapshot truncated'
                    if step == 0 or reset:
                        assert payload['mode'] == 'full', 'navigation must include full observation'
                    previous = {'baseline': payload['baseline'], 'text': reconstructed, 'metadata': metadata}
                    # Snapshot paragraphs can be shortened independently of the
                    # overall budget. Charge a complete text read on every state.
                    supplement = mcp.rpc('tools/call', {'name': 'text', 'arguments': {}})['result']
                    text = reconstructed + '\n' + native_text(supplement)
                    observed = [observed, supplement]
                    full = [full, supplement]
                    full_count = count(encoding, full)
                    version = client.call('eval', script='return navigator.userAgent')['value']
                else:
                    setup = mcp.tool('browser_evaluate', function='() => {' + script + '}')
                    expected = evaluated(setup)
                    observed = mcp.tool('browser_snapshot')
                    text = native_text(observed)
                    full_count = count(encoding, observed)
                    full = observed
                missing = check_semantics(text, expected)
                assert not missing, f'{fixture}/{label}: missing information {missing}'
                row = {'fixture': fixture, 'step': step, 'label': label, 'expected': expected,
                       'response': observed, 'fullResponse': full, 'counts': count(encoding, observed), 'fullCounts': full_count,
                       'semanticMissing': missing, 'reconstructedText': text, 'userAgent': version}
                record['states'].append(row)
                runtime.write(work / 'results.json', record)
    except Exception as exc:
        record['errors'].append(str(exc))
    finally:
        if mcp:
            mcp.close()
        if client:
            client.close()
        if session:
            session.close()
            record['cleanup'] = session.cleanup
        else:
            record['cleanup'] = {'mcpExited': mcp.process.poll() is not None if mcp else True}
        runtime.write(work / 'results.json', record)
    return record


def main():
    import tiktoken
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--umbra', type=Path, default=ROOT / 'build/Umbra.app/Contents/MacOS/umbra')
    parser.add_argument('--playwright-mcp', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pilot', action='store_true')
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    protocol = json.loads(PROTOCOL.read_text())
    assert importlib.metadata.version('tiktoken') == protocol['tokenizer']['version']
    encoding = tiktoken.get_encoding(protocol['tokenizer']['encoding'])
    site = Server()
    for fixture in protocol['fixtures']:
        site.page('/' + fixture, PAGE.read_text().replace('__FIXTURE__', fixture))
    result = {'protocolSha256': runtime.digest(PROTOCOL), 'fixtureSha256': runtime.digest(PAGE),
              'harnessSha256': runtime.digest(__file__), 'binarySha256': runtime.digest(args.umbra),
              'runtimeHarnessSha256': runtime.digest(Path(runtime.__file__)),
              'recordedUTC': datetime.now(timezone.utc).isoformat(),
              'environment': {'macOS': subprocess.check_output(['sw_vers', '-productVersion'], text=True).strip(),
                              'node': subprocess.check_output(['node', '--version'], text=True).strip(),
                              'playwrightMCP': json.loads((args.playwright_mcp.parent / 'package.json').read_text())['version'],
                              'playwrightCore': json.loads((args.playwright_mcp.parent.parent.parent / 'playwright-core/package.json').read_text())['version'],
                              'browsers': json.loads((args.playwright_mcp.parent.parent.parent / 'playwright-core/browsers.json').read_text())['browsers'],
                              'playwrightCoreBundleSha256': runtime.digest(args.playwright_mcp.parent.parent.parent / 'playwright-core/lib/coreBundle.js')},
              'tokenizer': protocol['tokenizer'], 'pilot': args.pilot, 'runs': [], 'errors': []}
    try:
        orders = protocol['orders'][:1] if args.pilot else protocol['orders']
        for repeat, order in enumerate(orders):
            for arm in order:
                run = run_arm(arm, repeat, protocol, site, args, encoding, args.out)
                result['runs'].append(run)
                result['errors'] += run['errors']
                print(json.dumps({'arm': arm, 'repeat': repeat, 'states': len(run['states']), 'errors': run['errors']}), flush=True)
                runtime.write(args.out / 'results.json', result)
                if run['errors']:
                    raise RuntimeError('arm failed; retain attempt and repair before measurement')
        expected_states = len(protocol['fixtures']) * len(protocol['states'])
        assert all(len(run['states']) == expected_states for run in result['runs'])
        for index in range(expected_states):
            reference = result['runs'][0]['states'][index]['expected']
            assert all(run['states'][index]['expected'] == reference for run in result['runs']), 'browser state mismatch'
        summaries = {}
        for arm in ['umbra', 'chromium', 'webkit']:
            runs = [r for r in result['runs'] if r['arm'] == arm]
            summaries[arm] = {'tokens': [sum(s['counts']['tokens'] for s in r['states']) for r in runs],
                              'fullTokens': [sum(s['fullCounts']['tokens'] for s in r['states']) for r in runs],
                              'statesPerRepeat': expected_states}
        result['summary'] = summaries
        result['eligibleForHeadline'] = not args.pilot and all(
            min(summaries[other]['tokens']) >= max(summaries['umbra']['tokens']) / (1 - protocol['headlineMinimumReduction'])
            for other in ['chromium', 'webkit'])
        assert result['binarySha256'] == runtime.digest(args.umbra), 'binary changed during run'
        assert result['protocolSha256'] == runtime.digest(PROTOCOL), 'protocol changed during run'
        assert result['fixtureSha256'] == runtime.digest(PAGE), 'fixture changed during run'
        assert all(all(value for key, value in run['cleanup'].items() if key != 'profile') for run in result['runs']), 'cleanup failed'
    except Exception as exc:
        result['errors'].append(str(exc))
    finally:
        site.close()
        runtime.write(args.out / 'results.json', result)
    print(json.dumps({'errors': result['errors'], 'summary': result.get('summary'),
                      'eligibleForHeadline': result.get('eligibleForHeadline', False)}), flush=True)
    return bool(result['errors'])


if __name__ == '__main__':
    sys.exit(main())
