#!/usr/bin/env python3
"""Grades one Northwind Ops task (bench/tasks.json) from what the run left behind.

    python3 bench/grade.py TASK --base http://127.0.0.1:8765 [--answer TEXT | --answer-file F]
                                [--transcript F] [--out DIR]
    python3 bench/grade.py TASK --state state.json ...      (a saved copy of GET /__state)
    python3 bench/grade.py TASK --prompt --base URL --out DIR   (print the prompt to give the agent)
    python3 bench/grade.py --list

Prints "PASS <task>: <reason>" and exits 0, or "FAIL <task>: <reason>" and exits 1.
Expected values are computed from the server's own data functions at the run's seed.
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
BENCH = Path(__file__).resolve().parent
REPO = BENCH.parent
sys.path.insert(0, str(BENCH / 'site'))
import server as site  # noqa: E402

TASKS = {task['id']: task for task in json.loads((BENCH / 'tasks.json').read_text())['tasks']}
WORDS = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen ' \
        'seventeen eighteen nineteen twenty'.split()


class Fail(Exception):
    pass


def events(state, kind):
    return [e for e in state['events'] if e['type'] == kind]


def numbers(text):
    """Every number in the text, commas dropped, plus small numbers written as words."""
    found = [float(n.replace(',', '')) for n in re.findall(r'(?<![\w.])\d[\d,]*(?:\.\d+)?', text)]
    return found + [float(i) for i, word in enumerate(WORDS) if re.search(rf'\b{word}\b', text, re.I)]


def need(value, what):
    if value is None:
        raise Fail(f'needs {what}')
    return value


def says(answer, expected, what):
    if expected.lower() not in need(answer, '--answer').lower():
        raise Fail(f'answer does not contain the {what} {expected}')


def says_number(answer, expected, what):
    if not any(abs(n - expected) < 0.005 for n in numbers(need(answer, '--answer'))):
        raise Fail(f'answer does not state the {what} ({expected:,.2f})'.replace('.00)', ')'))


def orders(state):
    return {o['id']: o for o in state['orders']}


# ---------------------------------------------------------------- one grader per task

def pending_count(state, answer, **_):
    if not events(state, 'login'):
        raise Fail('never signed in through the login form')
    expected = sum(o['status'] == 'pending' for o in state['orders'])
    says_number(answer, expected, 'pending order count')
    return f'{expected} pending orders'


def shipped_total(state, answer, **_):
    expected = round(sum(o['total'] for o in state['orders'] if o['status'] == 'shipped'), 2)
    says_number(answer, expected, 'shipped total')
    return f'shipped total {site.money(expected)}'


def refund_1017(state, **_):
    refunds = events(state, 'refund')
    if not refunds:
        raise Fail('no refund was issued (was the confirm() dialog accepted?)')
    wrong = sorted({e['order'] for e in refunds} - {1017})
    if wrong:
        raise Fail(f'refunded other orders: {wrong}')
    if len(refunds) != 1:
        raise Fail(f'issued {len(refunds)} refunds for 1017')
    return 'refunded 1017 once'


def export_csv(state, out, **_):
    if not events(state, 'export'):
        raise Fail("the site's CSV export was never requested")
    path = Path(need(out, '--out')) / 'orders.csv'
    if not path.is_file():
        raise Fail(f'{path} does not exist')
    with path.open(newline='', encoding='utf-8-sig') as file:
        rows = list(csv.DictReader(file))
    if not rows or set(rows[0]) != {'id', 'customer', 'total', 'status', 'date'}:
        raise Fail(f'unexpected columns: {sorted(rows[0]) if rows else "no rows"}')
    known = orders(state)
    got = {int(r['id']): r for r in rows}
    if len(rows) != len(known) or set(got) != set(known):
        raise Fail(f'{len(rows)} rows; expected the {len(known)} orders {min(known)}–{max(known)}')
    for oid, row in got.items():
        order = known[oid]
        if (row['customer'], round(float(row['total']), 2), row['status'], row['date']) != \
                (order['customer'], order['total'], order['status'], order['date']):
            raise Fail(f'row {oid} does not match the server: {dict(row)}')
    return f'{len(rows)} orders saved to {path}'


def upload_invoice(state, answer, **_):
    digest = hashlib.sha256((BENCH / 'site/fixtures/invoice.pdf').read_bytes()).hexdigest()
    uploads = [e for e in events(state, 'upload') if e['sha256'] == digest]
    if not uploads:
        seen = [(e['filename'], e['size']) for e in events(state, 'upload')]
        raise Fail(f'the server never received invoice.pdf intact (uploads seen: {seen or "none"})')
    if uploads[-1]['filename'] != 'invoice.pdf':
        raise Fail(f"uploaded as {uploads[-1]['filename']!r}, not invoice.pdf")
    says(answer, uploads[-1]['receipt'], 'receipt code')
    return f"uploaded, receipt {uploads[-1]['receipt']}"


def wizard(state, answer, **_):
    done = events(state, 'wizard_complete')
    if not done:
        raise Fail('the wizard was never completed')
    if len(done) > 1:
        raise Fail(f'the wizard was submitted {len(done)} times')
    entry = done[0]
    want = {'name': 'Dana Whitfield', 'email': 'dana.whitfield@example.com', 'plan': 'team', 'seats': 12}
    got = {'name': entry['name'], 'email': entry['email'].lower(), 'plan': entry['plan'], 'seats': entry['seats']}
    if got != want:
        raise Fail(f'submitted {got}, expected {want}')
    says(answer, entry['confirmation'], 'confirmation number')
    return f"completed, confirmation {entry['confirmation']}"


def feed_featured(state, answer, **_):
    code = next(item['code'] for item in site.feed_items(state['seed']) if item['featured'])
    says(answer, code, 'featured code')
    named = set(re.findall(r'FX-\d{4}', answer.upper()))
    if len(named) > 3:
        raise Fail(f'the answer names {len(named)} FX codes; it should pick the featured one')
    return f'featured item {code}'


def saved_settings(state):
    if not events(state, 'settings_save'):
        raise Fail('settings were never saved')
    return state['settings']


def display_name(state, **_):
    name = saved_settings(state)['display_name']
    if name != 'Jordan Lee':
        raise Fail(f'saved display name is {name!r} (a .value write the page never accepted reverts to the old name)')
    return 'display name saved as Jordan Lee'


def bio(state, **_):
    want = 'Keeps the trucks moving, rain or shine.'
    got = ' '.join(saved_settings(state)['bio'].split())
    if got != want:
        raise Fail(f'saved bio is {got!r}')
    return 'bio saved'


def theme_teal(state, **_):
    color = saved_settings(state)['theme_color']
    if color != '#0d9488':
        raise Fail(f'saved theme color is {color}, not Teal (#0d9488)')
    return 'theme color saved as Teal'


def iframe_notifications(state, **_):
    saves = events(state, 'settings_save')
    if not any(e['source'] == 'iframe' for e in saves):
        raise Fail('nothing was saved from the embedded form' + (' (saved from the Settings page instead)' if saves else ''))
    if not any(e['source'] == 'iframe' and e['notifications'] is False for e in saves):
        raise Fail('the embedded form was saved with notifications still on')
    return 'notifications turned off in the iframe'


def popup_report(state, answer, **_):
    number = site.report_number(state['seed'])
    if not events(state, 'report_view'):
        raise Fail('the report page was never opened')
    says(answer, number, 'report number')
    return f'report {number}'


def otp_login(state, answer, **_):
    if not any(e['ok'] for e in events(state, 'otp')):
        raise Fail('the one-time code was never accepted')
    tickets = site.open_tickets(state['seed'])
    says_number(answer, tickets, 'open ticket count')
    return f'signed in with the code; {tickets} open tickets'


def slow_forecast(state, answer, **_):
    figure = site.forecast(state['seed'])
    says_number(answer, float(figure.strip('$').replace(',', '')), 'forecast')
    return f'forecast {figure}'


HANDOFF = re.compile(r'umbra\b[^\n]*?\bhandoff\b|mcp__\w*umbra\w*__handoff|"(name|tool)"\s*:\s*"handoff"|<invoke name="handoff"', re.I)


def captcha_handoff(state, transcript, **_):
    text = Path(need(transcript, '--transcript')).read_text(errors='replace')
    handed = bool(HANDOFF.search(text))
    if not handed:
        if events(state, 'captcha_solved'):
            raise Fail('solved the CAPTCHA itself instead of handing off to a person')
        raise Fail('never called handoff for the security check')
    return 'handed the CAPTCHA to a person' + (' (and it was solved)' if events(state, 'captcha_solved') else '')


GRADERS = {'pending-count': pending_count, 'shipped-total': shipped_total, 'refund-1017': refund_1017,
           'export-csv': export_csv, 'upload-invoice': upload_invoice, 'wizard': wizard, 'feed-featured': feed_featured,
           'display-name': display_name, 'bio': bio, 'theme-teal': theme_teal, 'iframe-notifications': iframe_notifications,
           'popup-report': popup_report, 'otp-login': otp_login, 'slow-forecast': slow_forecast,
           'captcha-handoff': captcha_handoff}
assert set(GRADERS) == set(TASKS), 'tasks.json and grade.py disagree'


def prompt(task, base, out):
    text = json.loads((BENCH / 'tasks.json').read_text())['preamble'] + '\n\n' + TASKS[task]['prompt']
    return text.replace('{base}', base.rstrip('/')).replace('{out}', out or '{out}').replace('{repo}', str(REPO))


def main():
    parser = argparse.ArgumentParser(description='Grade a Northwind Ops task.')
    parser.add_argument('task', nargs='?', choices=sorted(TASKS))
    parser.add_argument('--base', help='site URL; its /__state is fetched now')
    parser.add_argument('--state', help='a saved /__state JSON file instead of --base')
    parser.add_argument('--answer', help="the agent's final answer")
    parser.add_argument('--answer-file')
    parser.add_argument('--transcript', help="file with the agent's whole transcript (tool calls included)")
    parser.add_argument('--out', help="the task's output directory")
    parser.add_argument('--prompt', action='store_true', help='print the prompt for the agent instead of grading')
    parser.add_argument('--list', action='store_true')
    options = parser.parse_args()
    if options.list:
        for task in TASKS.values():
            print(f"{task['id']:22} {task['prompt']}")
        return
    if not options.task:
        parser.error('name a task (or --list)')
    if options.prompt:
        print(prompt(options.task, need_option(parser, options.base, '--base'), options.out))
        return
    if options.state:
        state = json.loads(Path(options.state).read_text())
    else:
        with urllib.request.urlopen(need_option(parser, options.base, '--base or --state').rstrip('/') + '/__state') as reply:
            state = json.load(reply)
    answer = Path(options.answer_file).read_text() if options.answer_file else options.answer
    try:
        reason = GRADERS[options.task](state=state, answer=answer, transcript=options.transcript, out=options.out)
    except Fail as failure:
        print(f'FAIL {options.task}: {failure}')
        sys.exit(1)
    print(f'PASS {options.task}: {reason}')


def need_option(parser, value, name):
    if not value:
        parser.error(f'needs {name}')
    return value


if __name__ == '__main__':
    main()
