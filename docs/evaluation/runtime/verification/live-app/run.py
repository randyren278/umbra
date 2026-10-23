import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen

sys.dont_write_bytecode = True
repo = Path('/Users/randyren/Developer/umbra')
sys.path.insert(0, str(repo / 'tests'))
import test_umbra as base
import test_daemon as daemon
from server import Server

binary = os.environ['UMBRA']
out = Path('/tmp/umbra-closeout-app-smoke-v3')
out.mkdir(exist_ok=True)
profile = base.new_profile()
receipt = {'binarySha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest(), 'profile': profile, 'checks': {}}
site = Server()
site.page('/smoke', '''<!doctype html><title>Umbra release smoke</title>
<label>Name<input id=entry oninput="window.inputTrusted=event.isTrusted"></label>
<button id=submit onclick="window.clickTrusted=event.isTrusted;document.querySelector('#result').textContent=document.querySelector('#entry').value">Save</button>
<p id=result>Waiting</p>''')

def call(*args):
    result = base.umbra(*args, '--json', profile=profile, binary=binary, env={'UMBRA_UNSEEN': '0'})
    if result.code != 0:
        receipt['lastError'] = {'command': args[0], 'exitCode': result.code, 'error': result.err[:1200]}
        raise RuntimeError(f'{args[0]} failed with exit {result.code}')
    return json.loads(result.text)

try:
    nav = base.umbra('open', 'https://example.com', '--tab', 'public', '--timeout', '20', '--json', profile=profile, binary=binary, env={'UMBRA_UNSEEN': '0'})
    receipt['checks']['publicNavigation'] = {'exitCode': nav.code, 'error': nav.err, 'result': json.loads(nav.text) if nav.code == 0 else None}
    public = call('eval', 'return {title:document.title,host:location.hostname,ready:document.readyState,text:document.body?.innerText.slice(0,600)}', '--tab', 'public', '--timeout', '5')['value']
    receipt['checks']['publicHTTPSLoad'] = public
    assert public['title'] == 'Example Domain' and public['host'] == 'example.com', public
    assert 'documentation' in call('snapshot', '--tab', 'public')['snapshot']
    receipt['checks']['publicSnapshotContainsDocumentText'] = True
    call('screenshot', str(out / 'public-page.png'), '--tab', 'public')
    call('open', site.url('/smoke'), '--tab', 'fixture')
    call('fill', '#entry', 'Umbra is ready', '--tab', 'fixture')
    call('click', '#submit', '--tab', 'fixture')
    call('wait', '--js', 'document.querySelector("#result").textContent === "Umbra is ready"', '--tab', 'fixture', '--timeout', '5')
    state = call('eval', 'return {value:document.querySelector("#result").textContent,inputTrusted:window.inputTrusted,clickTrusted:window.clickTrusted}', '--tab', 'fixture')['value']
    assert state == {'value': 'Umbra is ready', 'inputTrusted': True, 'clickTrusted': True}, state
    receipt['checks']['trustedForm'] = state
    inspector = call('inspector')['url']
    endpoint = inspector.split('#')[0]
    try:
        urlopen(endpoint + 'state', timeout=5)
        raise AssertionError('unauthenticated inspector state was exposed')
    except HTTPError as error:
        assert error.code == 403
    receipt['checks']['inspectorUnauthorizedStatus'] = 403
    call('open', inspector, '--tab', 'inspector', '--until', 'document.querySelectorAll("#tabs tr").length >= 4', '--timeout', '10')
    for name, width, height in [('desktop', 1280, 900), ('mobile', 390, 844)]:
        call('viewport', str(width), str(height), '--tab', 'inspector')
        state = call('eval', 'return {title:document.title,rows:document.querySelectorAll("#tabs tr").length,error:document.querySelector("#error").textContent,preview:document.querySelector("#live").checked,pageWidth:document.documentElement.scrollWidth,viewport:innerWidth}', '--tab', 'inspector')['value']
        assert state['title'] == 'Umbra · Runtime' and state['rows'] >= 4 and state['error'] == ''
        assert not state['preview'] and state['pageWidth'] <= state['viewport'] + 1, state
        call('screenshot', str(out / f'inspector-{name}.png'), '--tab', 'inspector')
        receipt['checks'][f'inspector{name.title()}'] = state
    call('click', '#capture', '--tab', 'inspector')
    call('wait', '--js', '!document.querySelector("#frame").hidden && document.querySelector("#frame").naturalWidth > 0', '--tab', 'inspector', '--timeout', '10')
    receipt['checks']['requestedCapture'] = True
    call('sign-in')
    pids = daemon.daemon_pids(profile)
    assert len(pids) == 1
    windows = [w for w in daemon.window_report(pids)['windows'] if w['onscreen'] and w['alpha'] > 0 and w['visibleArea'] > 0]
    assert windows, 'native sign-in window did not appear'
    window = max(windows, key=lambda w: w['visibleArea'])
    capture = subprocess.run(['screencapture', '-x', '-l', str(window['id']), str(out / 'native-window.png')], capture_output=True, timeout=10)
    receipt['checks']['nativeWindow'] = {'visible': True, 'bounds': window['bounds'], 'captureSucceeded': capture.returncode == 0}
    labels = daemon.texts(pids[0])
    receipt['checks']['nativeAccessibilityLabels'] = labels
    assert daemon.quit_app(pids[0])
    time.sleep(.3)
    receipt['checks']['daemonRemainsUsableAfterWindowClose'] = call('runtime')['tabs'] >= 3
    assert receipt['checks']['daemonRemainsUsableAfterWindowClose']
    call('inspector', '--stop')
    receipt['ok'] = True
finally:
    site.close()
    base.cleanup()
    receipt['cleanup'] = {'daemonGone': not daemon.daemon_pids(profile), 'profileFilesGone': not any(p.exists() for p in base.profile_files(profile))}
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'ok': receipt.get('ok', False), 'checks': list(receipt['checks']), 'cleanup': receipt['cleanup']}))
