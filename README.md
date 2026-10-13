<p align="center">
  <img src="assets/banner.svg" width="100%" alt="umbra: the headless browser built for agents. Native WebKit, invisible, signed in. Its commands are read, fetch, download and sign-in.">
</p>

<p align="center">
  <strong>Sign in once. Your agents and scripts take it from there.</strong><br>
  Umbra runs the WebKit built into macOS with no window and a cookie store of its own. Agents and scripts call it from the shell to read signed-in pages, call a site's own API and download files, and Safari never notices.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#commands">Commands</a> ·
  <a href="#use-it-from-an-agent">Agents</a> ·
  <a href="docs/recipes.md">Recipes</a> ·
  <a href="#security-and-privacy">Security</a> ·
  <a href="#limitations">Limitations</a>
</p>

## Why it exists

On a Mac, scripts that need a signed-in website usually borrow Safari through AppleScript. Umbra replaced exactly that setup. A job-search tool and a Canvas course sync used to drive Safari. Their tabs opened in the middle of whatever else was on screen, links clicked elsewhere landed in the automation's window, and the runs often errored.

Umbra gives those jobs a browser of their own. Pages load off screen in WebKit, the engine Safari is built on. There's no window, no Dock icon, and nothing takes focus. You sign in once in a small window, and later runs stay signed in. Safari's tabs, history and cookies are never touched.

Coding agents hit the same wall. They need the web as you see it, signed in, without taking over your screen or your browser. Umbra gives them that through a few shell commands with plain output and exit codes they can act on.

## See it run

This is real output from a fresh profile against example.com:

```console
$ umbra read https://example.com <<< 'return document.title'
Example Domain

$ umbra read https://example.com <<'JS'
const link = document.querySelector('a')
return JSON.stringify({ text: link.textContent, href: link.href })
JS
{"text":"Learn more","href":"https://iana.org/help/example-domains"}

$ umbra read --cookie session_id https://example.com <<< 'return 1'; echo "exit $?"
signed out of example.com
exit 3

$ umbra fetch https://example.com/missing > /dev/null; echo "exit $?"
HTTP 404
exit 5
```

`read`, `fetch` and `download` each run as a short-lived process that prints its result on stdout and reports problems through the exit code. A run against a local server takes about 0.4 s from launch to exit. A Canvas module listing that makes several API calls inside one script takes about 3.7 s.

It runs real jobs every day:

- **FIRST**, a job-search tool, reads Instagram story links and LinkedIn employer apply links.
- **A Canvas course sync** reads the Canvas REST API and downloads course files.
- **A lecture transcript tool** reads Kaltura player pages.
- **Claude Code skills** in a course workspace check Canvas through it and ask the user to run `umbra sign-in` when a session lapses.

## Install

You need macOS 14 Sonoma or later and the Xcode Command Line Tools (`xcode-select --install`).

```sh
git clone https://github.com/randyren278/macOS-headless-browser.git
cd macOS-headless-browser
make install
```

`make install` builds `Umbra.app`, copies it to `~/Applications` and links `~/.local/bin/umbra` to the binary inside it. Nothing else on the system changes. If `~/.local/bin` isn't on your `PATH`, add it, then check that `umbra --version` prints `umbra 0.1.0`. If something seems off, `scripts/doctor.sh` checks the macOS version, the Swift compiler, the link, the binary and your sites file.

To install somewhere else, set the directories: `make install APPDIR=~/Apps BINDIR=~/bin`.

`make uninstall` removes the app and the link. Pass the same `APPDIR` and `BINDIR` if you set them. Your sign-ins stay in `~/Library/WebKit/com.randyren.umbra/`; delete that folder to sign out everywhere.

Prebuilt zips are signed ad hoc, not notarized by Apple, so macOS blocks a downloaded copy until you clear its quarantine flag with `xattr -dr com.apple.quarantine Umbra.app`. Building from source avoids that.

## Quick start

**1. List your sites** in `~/.config/umbra/sites.json`. You can start from [examples/sites.example.json](examples/sites.example.json):

```json
[
  {
    "name": "Canvas",
    "domain": "canvas.example.edu",
    "login": "https://canvas.example.edu/login",
    "probe": "/api/v1/users/self",
    "keepSession": true
  },
  {
    "name": "LinkedIn",
    "domain": "linkedin.com",
    "login": "https://www.linkedin.com/login",
    "cookie": "li_at"
  }
]
```

Umbra treats a site as signed in while its `cookie` is set, or while its same-origin `probe` path answers with a 2xx status. `keepSession` keeps session-only cookies between runs; read [the trade-off](#security-and-privacy) before you turn it on. Every field is covered in [docs/sites.md](docs/sites.md).

**2. Sign in once.**

```sh
umbra sign-in           # every site in the file
umbra sign-in Canvas    # one site by name; a URL opens a one-off tab
```

One window opens with a tab per site. Sign in as you would in Safari. Each tab gets a green check as soon as Umbra sees the session, then the window moves on to the next site. Close it when every tab is checked.

**3. Call it from your scripts.**

```sh
# Run JavaScript in the signed-in page. Exit 3 means the session is gone.
umbra read --cookie li_at https://www.linkedin.com/jobs/ < jobs.js

# Call the site's own API with the browser's cookies.
umbra fetch "https://canvas.example.edu/api/v1/courses?per_page=100"

# Save a file that needs the session.
umbra download "https://canvas.example.edu/files/123/download" ~/Courses/notes.pdf
```

A script is the body of an async function, so you can `await` inside it. Return a string, or return an object or array to get JSON. [docs/recipes.md](docs/recipes.md) has tested callers in Python, shell and Node that handle every exit code.

## Commands

### Pages, APIs and files

| Command | What it does |
|---|---|
| `umbra read <url>` | Loads the page, runs the script on stdin and prints what it returns. Strings print as they are, objects and arrays print as JSON with sorted keys, and `null` or `undefined` prints nothing. With an empty script it prints the page's HTML. |
| `umbra fetch <url>` | Loads a page on the same site, then `fetch()`es the URL from inside it with the session's cookies and prints the body. The page is the URL's own `/robots.txt` unless you pass `--from PAGE`. |
| `umbra download <url> <file>` | Downloads with the session into a hidden file beside `<file>`, then renames it into place once the download is complete. |

Options:

- `--cookie NAME` (read): exit 3 unless a cookie with that name is sent to the page's final host.
- `--until JS` (read): before running the script, wait until this JavaScript expression is true, such as `--until 'document.querySelector(".results")'`. It's re-checked in whichever document is current, so JavaScript redirects and self-submitting forms can land first.
- `--wait S` (read): pause S seconds after the page loads, before `--until` and the script.
- `--from PAGE` (fetch): the page to fetch from, as a path on the same site or a full URL.
- `--timeout S`: give up with exit 2 after S seconds. The default is 90 for `read` and `fetch` and 300 for `download`. The clock starts once the run holds its lock, and `--wait` is added on top. `--until` waits within this limit.

### Sign-in and cookies

| Command | What it does |
|---|---|
| `umbra sign-in [sites]` | Opens the sign-in window for the named sites or URLs. With no arguments it opens every site in `sites.json`, and plain `umbra` does the same. |
| `umbra cookies [domain]` | Lists cookie names, domains and expiry dates. It never prints values. |
| `umbra --version` | Prints the version. |

### Exit codes

Results go to stdout. Messages go to stderr.

| Exit | Meaning | What to do |
|---|---|---|
| `0` | Done | |
| `1` | Umbra itself failed, for example it couldn't open its lock file | Read the message on stderr |
| `2` | Timed out, or the script threw | Retry later, or fix the script |
| `3` | Signed out: the `--cookie` isn't set | Run `umbra sign-in` |
| `4` | The page or download failed to load | Check the network or the site; retry later |
| `5` | HTTP error status. `fetch` still prints the body | Check the URL and your access |
| `64` | Usage error | Fix the command line |
| `78` | `sites.json` can't be parsed | Fix the file; the message names the problem |

## Use it from an agent

Any agent with a shell tool can use Umbra today. It runs a command, reads stdout, and branches on the exit code. Exit 3 is the important one: the session has lapsed, so the agent should stop and ask you to run `umbra sign-in`, because signing in happens in a window, by you.

```sh
umbra read https://canvas.example.edu/ <<< 'return document.title'
umbra fetch "https://canvas.example.edu/api/v1/users/self/todo"
umbra read https://example.com/article < /dev/null    # the page's HTML
```

[docs/agents.md](docs/agents.md) has instructions you can paste into `AGENTS.md` or `CLAUDE.md`, plus advice on giving an agent its own profile.

## Configuration and profiles

| Setting | Default | Purpose |
|---|---|---|
| `~/.config/umbra/sites.json` | none | Sites for `sign-in`, signed-in checks and `keepSession` |
| `UMBRA_SITES=/path/file.json` | | Use a different sites file |
| `UMBRA_PROFILE=name` | the default store | Use a separate cookie store and lock |

A profile is a separate browser identity. It has its own cookies and its own lock, persists under its name, and runs alongside other profiles. Use one for a second account, for experiments, or for jobs that must run in parallel:

```sh
UMBRA_PROFILE=work umbra sign-in https://mail.example.com/
UMBRA_PROFILE=work umbra read https://mail.example.com/ < unread.js
```

## How it works

<p align="center">
  <img src="assets/how-it-works.svg" width="100%" alt="Your script runs umbra and gets back text, JSON or a file plus an exit code. umbra takes the profile's lock and drives WebKit off screen with no window or Dock icon. WebKit presents Safari's user agent, runs your JavaScript, and fetches and downloads using Umbra's own cookie store on disk. umbra sign-in fills that store once, in one window with a check per site. Safari is untouched.">
</p>

Each command starts `Umbra.app` as a background process with no Dock icon and no menu bar. It waits for the profile's lock, then loads the page in a WebKit view that is never placed in a window. When your script returns, any session cookies you asked to keep are saved and the process exits.

The cookie store is a persistent WebKit data store that belongs to Umbra, under `~/Library/WebKit/com.randyren.umbra/`. Safari keeps its own store, so neither can see the other's cookies. Sites receive Safari's user agent, built from the version of Safari installed on the Mac, for example `Version/27.0.1 Safari/605.1.15`.

[docs/how-it-works.md](docs/how-it-works.md) covers the store, the lock, profiles and the life of a run in detail.

## Security and privacy

- **Cookies stay in the store.** Like any browser, Umbra sends a cookie only to the site it belongs to. No command prints or exports a cookie value. `umbra cookies` shows names and expiry dates only, so its output is safe to paste into an issue.
- **Umbra adds no traffic of its own.** It sends no telemetry. Its own requests are the URLs you give it, plus the `--from` page for `fetch`. What those pages load in turn is up to them, as in any browser.
- **Your account protects the store.** As with Safari, anyone who can read your home folder can reuse your sessions.
- **The `keepSession` trade-off.** Sites mark some cookies session-only, meaning they should die when the browser quits. WebKit drops those cookies when an Umbra run ends, which signs you out of sites like Canvas after every run. With `keepSession: true`, Umbra re-saves them with a 30-day expiry at the end of each run. Your sign-in survives, but it also outlives the browser session the site had in mind. Turn it on only for sites that need it, and only on a Mac you trust.
- **Scripts run with the page's power.** Your JavaScript runs inside the signed-in page, the same as the site's own code. Only run scripts you wrote or have read.
- **To sign out,** open the site with `umbra sign-in <url>` and sign out there, or delete `~/Library/WebKit/com.randyren.umbra/` to drop every profile.

## Limitations

- It runs on macOS 14 or later and nowhere else, because it's built on `WKWebView` and `WKWebsiteDataStore(forIdentifier:)`.
- It needs your logged-in macOS session. Runs from Terminal and from LaunchAgents work; FIRST's sync jobs run as LaunchAgents.
- Passkeys and Touch ID don't work in the sign-in window. Use a password with a push approval or a code.
- One run at a time per profile. Other runs wait their turn, and while the sign-in window is open, runs on that profile wait too. Give parallel jobs separate profiles.
- `fetch` is same-origin with its `--from` page. For an API on another origin, point `--from` at a page on that origin, or use `read` and call `fetch()` yourself.
- It reads, fetches and downloads, and that's all. There are no screenshots, PDFs, network interception or click helpers; anything else is up to your JavaScript inside the page.
- It's not a scraper or an anti-bot tool. It's meant for your own accounts at a human pace. It doesn't hide automation, rotate identities or solve CAPTCHAs, and site terms still apply.
- Prebuilt zips are unsigned: they carry only an ad hoc signature and aren't notarized.

## What's next

Umbra is growing from one-shot commands into a browser an agent can drive step by step. The plan includes a per-profile daemon with live tabs, accessibility-style snapshots with element references, native clicks and typing, screenshots and PDFs, markdown extraction, console and network logs, handing a tab to you for two-factor prompts, and an MCP server. None of that ships in 0.1.0, and this README documents only what does.

## FAQ

**How is this different from Playwright or Puppeteer?**
They download their own browser builds (Chromium, plus Firefox and a patched WebKit for Playwright) and drive them over a debugging protocol from Node or Python. In return you get cross-platform support, parallel contexts, screenshots and network interception, and they're the right tools for testing web apps. Umbra makes the opposite trade. It's a small native app, with a binary under 1 MB, that uses the WebKit already in macOS and presents Safari's user agent. It needs no runtime and no browser download. It keeps one persistent store that you sign into by hand, which suits personal jobs on your own Mac.

**Why not script Safari with AppleScript?**
`do JavaScript` needs Safari running, "Allow JavaScript from Apple Events" turned on, and a tab to work in. That tab lives in your browser, where it takes focus and catches your clicks, and the script breaks if Safari shows a dialog or someone closes the window. Umbra runs the same engine with none of that.

**Why not curl with cookies copied from the browser?**
Cookies expire and rotate, HttpOnly session cookies are awkward to export, and many pages build their content with JavaScript. Umbra keeps the session inside a browser engine that accepts cookie updates as sites send them, and it runs the page's own JavaScript before yours.

**Can I run several jobs at once?**
Yes, with one profile per job; each profile signs in separately. Runs on the same profile take turns, which keeps one store from being written by two processes at once.

## Development

```sh
make build     # build/Umbra.app, signed ad hoc as com.randyren.umbra
make test      # tests/run.sh against the build, never the installed app
make icon      # regenerate the artwork in assets/ from assets/build.py
make release   # dist/Umbra-<version>.zip and its .sha256
make help      # list targets and overrides
```

The source is in [Sources/](Sources/). The tests serve pages from `127.0.0.1` and use throwaway profiles, so they never touch your signed-in store. When you report a bug, include `umbra --version`, your macOS version, the exit code and stderr, and `umbra cookies <domain>` if sign-in is involved. Never include cookie values. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## License

[MIT](LICENSE) © 2026 Randy Ren
