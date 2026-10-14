#!/usr/bin/env python3
"""Calibrate the transport adapter against upstream success, failure and unfinished episodes."""
import argparse
import http.server
import json
import os
from pathlib import Path
import secrets
import threading

import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browsergym', required=True)
    parser.add_argument('--miniwob', required=True)
    parser.add_argument('--chromium', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    class Site(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(Path(args.miniwob) / 'miniwob/html'), **kw)
        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}/miniwob/'
    task_cls = run.load_tasks(args.browsergym)['miniwob.click-test-2']
    rows = []
    try:
        for arm in ['umbra', 'playwright']:
            profile = 'extcheck-' + secrets.token_hex(4)
            env = {**os.environ, 'UMBRA_PROFILE': profile, 'UMBRA_IDLE': '60'}
            page = context = pw = None
            try:
                if arm == 'umbra':
                    page = run.UmbraPage(env)
                    page.goto(base + 'click-test-2.html')
                    page.call('viewport', width=332, height=214)
                else:
                    from playwright.sync_api import sync_playwright
                    pw = sync_playwright().start()
                    context = pw.chromium.launch(headless=True, executable_path=args.chromium)
                    page = context.new_page(viewport={'width': 332, 'height': 214})
                for target, expected, label in [(None, 0, 'unfinished'),
                                                 ('text=ONE', 1, 'correct'),
                                                 ('text=TWO', 0, 'incorrect')]:
                    task = task_cls(seed=7, base_url=base)
                    goal, _ = task.setup(page)
                    if target:
                        if arm == 'umbra':
                            page.call('click', target=target)
                        else:
                            page.click(target)
                    reward, done, _, info = task.validate(page, [])
                    assert reward == expected, (arm, label, reward, info)
                    assert done == bool(target), (arm, label, done)
                    rows.append({'arm': arm, 'case': label, 'goal': goal,
                                 'reward': reward, 'done': done, 'info': info})
                print(arm, 'unfinished / correct / incorrect: PASS', flush=True)
            finally:
                if arm == 'umbra':
                    if page:
                        page.close()
                    assert run.cleanup(profile, env)
                else:
                    if context:
                        context.close()
                    if pw:
                        pw.stop()
        run.write(args.out, {'checks': rows, 'source': task_cls.__module__, 'passed': True})
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
