# Commands

Commands that work on a page take `--tab NAME` (default: the current tab). Every command takes `--timeout SECONDS`, `--json` and `--profile NAME`. Options can go before or after the command name: `umbra --tab work snapshot` works too. `umbra help <command>` has the details.

### Session

| Command | What it does |
|---|---|
| `open <url>` | Load a URL (a host name or file path works too) and wait for it. `--new` opens a new tab; `--until JS` waits for a condition across redirects. Alias `goto`. |
| `back`, `forward`, `reload` | Move through the tab's history, or reload. |
| `tabs`, `tab select <name>`, `tab new`, `tab close` | List tabs, or switch, open and close them. Pop-ups become tabs. |
| `wait` | Wait for `--selector`, `--text`, `--url`, `--js`, `--gone`, `--idle` (network quiet for 500 ms), or `--ms`. |
| `eval <script>` | Run JavaScript in the page as the body of an async function and print what it returns. |
| `block <types>` | Stop loading images, fonts, media, stylesheets, scripts or trackers. `--none` clears. |
| `viewport [w h]` | Set the viewport. `--mobile` sends iPhone Safari's user agent; `--scale` sets the pixel ratio. |
| `handoff "<message>"` | Show the tab to the person for a step only they can do, then hide it again. |
| `status`, `stop` | The daemon's profile, uptime and tabs; stop it. |

### See

| Command | What it does |
|---|---|
| `snapshot` | One line per element with role, name, state, value and ref. `--interactive` keeps only elements with refs plus headings; `--scope` narrows it to part of the page. |
| `text` | The visible text, as the browser lays it out. The cheapest way to read. |
| `markdown` | The page as markdown with tables and absolute links. `--main` drops navigation and page chrome. |
| `html`, `links` | The live DOM; every link as text and URL, with `--filter`. |
| `get <target>` | One element's text or value, or an attribute with `--attr`. |
| `info` | URL, title, HTTP status, viewport, scroll, ready state, visibility, and counts of links, forms, images and iframes. |

### Act

All of these use trusted native input and wait until the target is visible, enabled and not covered.

| Command | What it does |
|---|---|
| `click <target>` | Click (`--button`, `--count`, `--modifiers`). Waits for any navigation it starts and reports the new page. |
| `fill <target> <value>` | Replace a field's contents, typed as a keyboard would. Works on React-controlled inputs and contenteditables. |
| `type <text>` | Type without clearing first, into `--target` or the focused element. |
| `press <keys…>` | Keys and chords in order: `Enter`, `Tab`, `Meta+a`, `Shift+Tab`… |
| `select <target> <values…>` | Choose options in a `<select>` by value or label. |
| `check`, `uncheck <target>` | Set a checkbox, radio or switch, then verify it. |
| `hover`, `focus <target>` | Move the mouse over an element; move keyboard focus to it. |
| `scroll` | Native wheel scrolling: `--dy`, `--dx`, `--to top` or `--to bottom`, or `--target` into view. |
| `drag <from> <to>` | Drag one element onto another. |
| `upload <target> <files…>` | Answer a file chooser, including one behind a styled button. |
| `dialog accept`, `dialog dismiss` | Dialogs never block: each alert, confirm or prompt is accepted by default, and the reply of the action that raised it reports it. `dialog dismiss` changes how the next one is answered (`--text` answers a prompt); `dialog` alone lists the ones seen. |

### Capture

| Command | What it does |
|---|---|
| `screenshot [path]` | The viewport, the whole page with `--full`, or one element with `--target`. PNG or JPEG; base64 when no path is given. |
| `pdf <path>` | The whole page as one tall PDF page, with selectable text and working links. |

### Observe

| Command | What it does |
|---|---|
| `console` | What the page logged, uncaught errors included. `--level`, `--clear`. |
| `network` | Documents, fetch and XHR calls, and every loaded resource, with status, timing and size. `--failed`, `--filter`, `--clear`. |
| `downloads` | Files the page downloaded, saved under `~/Downloads/Umbra/`. |
| `cookies [domain]` | Cookie names, domains and expiry dates. Never values. |

### One-shot

These keep their original contract for scheduled scripts: one process, a result on stdout and an exit code. When a daemon is running they go through it; otherwise they run on their own.

| Command | What it does |
|---|---|
| `read <url> < script.js` | Load a page in a scratch tab, run the script (or print the HTML when it's empty) and close the tab. `--cookie NAME` exits 3 when signed out; `--until`, `--wait`. |
| `fetch <url>` | GET a URL with the browser's session, from a page on the same site (`--from`, default `/robots.txt`). |
| `download <url> <file>` | Download with the session, replacing `<file>` once complete. |

[docs/recipes.md](recipes.md) has worked examples for both styles.


## Exit codes

Results go to stdout and messages to stderr. Over MCP, a failure comes back as a tool error with the message and, for codes 1 to 4, a hint about what to do next.

| Exit | Meaning | What to do |
|---|---|---|
| `0` | Done | |
| `1` | Umbra itself failed | Read stderr; the next command starts the daemon again |
| `2` | Timed out, or a script threw | Wait for a specific condition, raise `--timeout`, or fix the script |
| `3` | Signed out (`read --cookie`) | Run `umbra sign-in <site>` |
| `4` | The page or download failed to load | Check the URL and that the site is up |
| `5` | HTTP error status (`fetch`, `download`) | Check the URL and your access |
| `64` | Usage error | Fix the command; `umbra help <command>` |
| `78` | `sites.json` can't be parsed | Fix the file; the message names the problem |
