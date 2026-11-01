#!/usr/bin/env python3
"""Visible/headless frame traces on a localhost scrolling fixture, alternating builds.

Shows real windows and takes focus. Uses the suite's throwaway profiles and cleanup.
This measures requestAnimationFrame cadence, not physical trackpad-to-display latency.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys

os.environ['UMBRA_TEST_GUI'] = '1'
os.environ['UMBRA_UNSEEN'] = '0'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
import test_umbra as base
import test_daemon as daemon
from server import Server

PAGE = '''<title>Umbra frame cadence</title>
<style>body{margin:0;background:#fafafa}article{height:16000px;
background:repeating-linear-gradient(#fafafa 0px,#fafafa 99px,#aaa 100px)}
header{position:fixed;top:0;background:white;padding:24px;font:24px system-ui}</style>
<header>Umbra native browsing test</header><article></article><script>
let frames=[], started, previous, warm=0, focusedAtStart; window.done=false;
const focusEvents=[];
for(const name of ['focus','blur','visibilitychange']) addEventListener(name, () => focusEvents.push({event:name,at:performance.now(),focused:document.hasFocus(),visible:document.visibilityState}));
function frame(now) {
  if(warm++ < 30) return requestAnimationFrame(frame);
  if(!started) { started=now; focusedAtStart=document.hasFocus(); }
  if(previous) frames.push(now-previous);
  previous=now; scrollBy(0,3);
  if(now-started < 3000) requestAnimationFrame(frame);
  else {
    window.result={frames,scrollY,visible:document.visibilityState,focused:document.hasFocus(),focusedAtStart,
      focusEvents:focusEvents.filter(e=>e.at>=started&&e.at<=now),width:innerWidth,height:innerHeight};
    window.done=true;
  }
}
window.start=()=>requestAnimationFrame(frame);
addEventListener('pointerdown', window.start, {once:true});
</script>'''


def run(binary, server, mode):
    profile = base.new_profile()
    def command(*args):
        result = base.umbra(*args, profile=profile, binary=str(binary))
        if result.code:
            raise RuntimeError(result.err)
        return result
    try:
        command('open', server.url('/'))
        if mode == 'visible':
            process = base.spawn('handoff', 'Frame cadence measurement', '--until', 'window.done', '--timeout', '20', profile=profile, binary=str(binary))
            [pid] = daemon.daemon_of(profile)
            windows = daemon.wait_for(lambda: daemon.shown([pid]), 5)
            if not windows:
                raise RuntimeError('the measurement window did not appear')
            window = max(windows, key=lambda w: w['visibleArea'])
            x, y, width, height = window['bounds']
            daemon.lister.stdin.write(f'click {pid} {x + width / 2} {y + height / 2}\n')
            daemon.lister.stdin.flush()
            sent = json.loads(daemon.lister.stdout.readline())
            if not sent['trusted'] or not sent['sent']:
                raise RuntimeError('foreground measurement requires native input permission')
            out, err = process.communicate(timeout=25)
            if process.returncode:
                raise RuntimeError(err.decode())
        else:
            command('eval', 'window.start(); return true')
            command('wait', '--js', 'window.done', '--timeout', '15')
        value = json.loads(command('eval', 'return window.result', '--json').text)['value']
        frames = value['frames']
        assert frames and value['scrollY'] > 0 and value['visible'] == 'visible', value
        if mode == 'visible':
            after = command('eval', '''return await new Promise(resolve => {
                const frames=[]; let previous, warm=0;
                const tick = now => {
                    if (warm++ < 10) return requestAnimationFrame(tick);
                    if (previous) frames.push(now-previous);
                    previous=now;
                    if (frames.length < 30) requestAnimationFrame(tick); else resolve(frames);
                }; requestAnimationFrame(tick);
            });''', '--json')
            value['afterHandoffFrames'] = json.loads(after.text)['value']
        return dict(mode=mode, **value, p50=statistics.median(frames), p95=sorted(frames)[int(len(frames) * .95)], maximum=max(frames),
                    foregroundVerified=mode == 'hidden' or value['focusedAtStart'] and value['focused'] and
                        all(e['focused'] and e['visible'] == 'visible' for e in value['focusEvents']))
    finally:
        base.umbra('stop', profile=profile, binary=str(binary))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, default=Path(base.UMBRA))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    binaries = dict(baseline=args.baseline.resolve(), candidate=args.candidate.resolve())
    displays = json.loads(subprocess.check_output(['system_profiler', 'SPDisplaysDataType', '-json']))
    result = dict(environment=dict(system=platform.platform(),
        displays=[dict(name=display['_name'], resolution=display.get('_spdisplays_resolution'))
                  for gpu in displays['SPDisplaysDataType'] for display in gpu.get('spdisplays_ndrvs', [])]),
        binaries={key: dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for key, path in binaries.items()},
        workload=dict(warmupFrames=30, durationMs=3000, scrollPixelsPerFrame=3, timing='requestAnimationFrame timestamps'), samples=[])
    server = Server()
    server.page('/', PAGE)
    try:
        for repeat in range(args.repeats):
            for arm in (['baseline', 'candidate'] if repeat % 2 == 0 else ['candidate', 'baseline']):
                for mode in ['hidden', 'visible']:
                    sample = dict(repeat=repeat, arm=arm, **run(binaries[arm], server, mode))
                    result['samples'].append(sample)
                    print(f"{arm} {mode}: p50={sample['p50']} ms p95={sample['p95']} ms n={len(sample['frames'])}", flush=True)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(result, indent=2) + '\n')
    finally:
        server.close()
    if any(not sample['foregroundVerified'] for sample in result['samples']):
        raise RuntimeError('foreground focus was interrupted; raw samples retained, comparison inconclusive')


if __name__ == '__main__':
    main()
