# Recipes

These are copy-paste callers and patterns for scripts that use Umbra. The callers below were run against a local test server before publishing. Swap in your own URLs.

- [Exit codes in practice](#exit-codes-in-practice)
- [Python](#python) · [Node](#node) · [Shell](#shell)
- [Several API calls in one run](#several-api-calls-in-one-run)
- [Pages that redirect through JavaScript](#pages-that-redirect-through-javascript)
- [Waiting for content the page renders](#waiting-for-content-the-page-renders)
- [Downloading files](#downloading-files)
- [Running on a schedule with launchd](#running-on-a-schedule-with-launchd)
- [Trying scripts on a separate profile](#trying-scripts-on-a-separate-profile)

## Exit codes in practice

| Exit | Meaning | Retry? | Typical handling |
|---|---|---|---|
| `0` | Done | | Use stdout |
| `2` | Timed out, or your script threw | Timeouts, yes | stderr starts with `no answer in` for a timeout and `the script failed:` for a thrown error |
| `3` | Signed out | No | Tell the person to run `umbra sign-in`, then skip this run |
| `4` | The page or download failed to load | Yes, later | Network down, site down, or a bad host name |
| `5` | HTTP error status | Rarely | stderr is `HTTP <status>`. `fetch` still prints the body, which often explains the error |
| `64` | Usage error | No | A bug in the caller |
| `78` | `sites.json` can't be parsed | No | Fix the file |

Umbra writes results to stdout and messages to stderr. Runs on the same profile take turns, and time spent waiting for the lock doesn't count toward `--timeout`. So if your caller enforces its own timeout, make it generous.

## Python

```python
import subprocess


class SignedOut(Exception):
    """Exit 3: the site's session is gone. Run `umbra sign-in` again."""


class UmbraError(Exception):
    def __init__(self, code, message):
        super().__init__(f"umbra exited {code}: {message}")
        self.code = code


def umbra(*args, script=""):
    """Runs one umbra command and returns its stdout. An empty script makes `read` print the page's HTML."""
    result = subprocess.run(["umbra", *args], input=script, capture_output=True, text=True)
    if result.returncode == 3:
        raise SignedOut(result.stderr.strip())
    if result.returncode != 0:
        raise UmbraError(result.returncode, result.stderr.strip())
    return result.stdout
```

Using it:

```python
import json

title = umbra("read", "https://example.com", script="return document.title").strip()

html = umbra("read", "https://example.com")  # the whole page

# Canvas may prefix JSON with "while(1);" when you're signed in with a browser session.
body = umbra("fetch", "https://canvas.example.edu/api/v1/courses?per_page=100")
courses = json.loads(body.removeprefix("while(1);"))

try:
    links = json.loads(umbra("read", "--cookie", "li_at", "https://www.linkedin.com/jobs/",
                             script=open("jobs.js").read()))
except SignedOut:
    print("LinkedIn is signed out: run `umbra sign-in LinkedIn`")
```

## Node

```js
import { spawn } from 'node:child_process'

// Runs one umbra command and resolves with its stdout; rejects with err.exitCode set (3 = signed out).
export function umbra(args, script = '') {
  return new Promise((resolve, reject) => {
    const child = spawn('umbra', args)
    let out = '', err = ''
    child.stdout.setEncoding('utf8').on('data', chunk => { out += chunk })
    child.stderr.setEncoding('utf8').on('data', chunk => { err += chunk })
    child.on('error', reject)
    child.on('close', code => {
      if (code === 0) return resolve(out)
      reject(Object.assign(new Error(`umbra ${args[0]} exited ${code}: ${err.trim()}`), { exitCode: code }))
    })
    child.stdin.end(script)
  })
}
```

Using it (ES module, top-level `await`):

```js
import { readFile } from 'node:fs/promises'

const title = (await umbra(['read', 'https://example.com'], 'return document.title')).trim()

try {
  const script = await readFile('stories.js', 'utf8')
  const stories = JSON.parse(await umbra(['read', '--cookie', 'sessionid', 'https://www.instagram.com/'], script))
} catch (error) {
  if (error.exitCode === 3) console.error('Instagram is signed out: run `umbra sign-in Instagram`')
  else throw error
}
```

## Shell

```zsh
#!/bin/zsh
out=$(umbra read --cookie li_at https://www.linkedin.com/jobs/ < jobs.js)
case $? in
  0) print -r -- "$out" ;;
  3) print -u2 "signed out: run 'umbra sign-in'"; exit 3 ;;
  2|4) print -u2 "timed out or failed to load; try again later"; exit 75 ;;
  *) print -u2 "umbra failed"; exit 1 ;;
esac
```

Umbra's JSON output pipes straight into `jq`:

```sh
umbra read https://canvas.example.edu/ < modules.js | jq -r '.[].name'
```

## Several API calls in one run

Each run pays for a process start and a page load, so do related requests inside one script. Load any page on the site, then call its API with `fetch()`; the browser attaches the session cookies. The Canvas course sync lists every module and its items this way. A full listing takes about 3.7 s.

`modules.js`:

```js
const get = async path => {
  const response = await fetch(path, { credentials: 'include', headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`)
  return JSON.parse((await response.text()).replace(/^while\(1\);/, ''))
}
const modules = await get('/api/v1/courses/123/modules?per_page=100')
const items = await Promise.all(modules.map(m => get(`/api/v1/courses/123/modules/${m.id}/items?per_page=100`)))
return JSON.stringify(modules.map((m, i) => ({ name: m.name, items: items[i].map(item => item.title) })))
```

```sh
umbra read https://canvas.example.edu/ < modules.js
```

If a request fails, the thrown error ends the run with exit 2, and its message lands on stderr after `the script failed:`.

## Pages that redirect through JavaScript

Some pages hand you on with a script redirect or a form that submits itself, as many LTI launches do, including Kaltura's lecture player. Umbra acts on the *first* page load, so use `--until` to describe the page you actually want. It re-checks the expression in whichever document is current until it's true:

```sh
umbra read --until 'location.hostname == "player.example.com"' \
  "https://lms.example.edu/lti/launch/456" < player.js
```

If there's nothing reliable to test for, `--wait 4` pauses a fixed four seconds instead. `--wait` adds to `--timeout`, while `--until` waits within it.

## Waiting for content the page renders

When the page builds its content with JavaScript, wait for an element to exist:

```sh
umbra read --until 'document.querySelector("[data-test=results] li")' https://example.com/search < results.js
```

For more control, such as a custom deadline or error message, wait inside the script instead:

```js
const deadline = Date.now() + 15000
let row
while (!(row = document.querySelector('[data-test="results"] li'))) {
  if (Date.now() > deadline) throw new Error('results never appeared')
  await new Promise(resolve => setTimeout(resolve, 200))
}
return [...document.querySelectorAll('[data-test="results"] li')].map(li => li.textContent.trim()).join('\n')
```

## Downloading files

```sh
umbra download "https://canvas.example.edu/files/789/download" ~/Courses/ELEC-101/week1.pdf
```

The download carries the session, follows redirects (including to a storage host on another domain), and lands in a hidden `.week1.pdf.umbra-part` beside the target. That file is renamed into place only once it's complete, so readers never see half a file and an existing file is replaced in one step. An HTTP error status exits 5 and writes nothing.

## Running on a schedule with launchd

Run Umbra from a LaunchAgent: a job in your own login session. A LaunchDaemon runs outside it. launchd's `PATH` doesn't include `~/.local/bin`, so use absolute paths. Save this as `~/Library/LaunchAgents/com.example.course-sync.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.example.course-sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/you/sync/course_sync.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/Users/you/.local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>StandardErrorPath</key>
  <string>/Users/you/Library/Logs/course-sync.log</string>
</dict>
</plist>
```

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.course-sync.plist
```

Two jobs on the same profile simply take turns. Give a job its own profile only when it must not wait.

## Trying scripts on a separate profile

Write and debug a script against a throwaway profile, so mistakes can't disturb the store your scheduled jobs use:

```sh
export UMBRA_PROFILE=scratch
umbra sign-in https://canvas.example.edu/login
umbra read https://canvas.example.edu/ < modules.js
```

A profile is a separate store under `~/Library/WebKit/com.randyren.umbra/WebsiteDataStore/`. To find its folder so you can delete it, see [how-it-works.md](how-it-works.md#profiles).
