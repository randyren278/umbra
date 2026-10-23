# Commands

Commands that work on a page take `--tab NAME` (default: the current tab). Daemon commands take `--timeout SECONDS`, `--agent`, `--task`, `--request` and an absolute Unix-second `--deadline`; `--json` and `--profile NAME` select output and identity. Options can go before or after the command name: `umbra --tab work snapshot` works too. `umbra help <command>` has the details, and `umbra schema` is the generated parameter reference. [Runtime guide](runtime/README.md) covers ownership, bounded concurrency, observations and recovery.

### Session

| Command | What it does |
|---|---|
| `open <url>` | Load a URL (a host name or file path works too) and wait for it. `--new` opens a new tab; `--until JS` waits for a condition across redirects. Alias `goto`. |
| `back`, `forward`, `reload` | Move through the tab's history, or reload. |
| `tabs`, `tab select <name>`, `tab new`, `tab close` | List tabs, or switch, open and close them. Pop-ups become tabs. |
| `wait` | Wait for `--selector`, `--text`, `--url`, `--js`, `--gone`, `--idle` (complete document and unchanged resource-entry count for 500 ms), or `--ms`. |
| `eval <script>` | Run JavaScript in the page as the body of an async function and print what it returns. |
| `block <types>` | Stop loading images, fonts, media, stylesheets, scripts or trackers. `--none` clears. |
| `viewport [w h]` | Set the viewport. `--mobile` sends iPhone Safari's user agent; `--scale` sets the pixel ratio. |
| `handoff "<message>"` | Show the tab to the person for a step only they can do, then hide it again. |
| `status`, `stop` | The daemon's profile, uptime and tabs; stop it. |

### See

| Command | What it does |
|---|---|
| `snapshot` | One line per element with role, name, state, value and ref. `--interactive`, `--scope`, `--max` (outline characters) and `--depth` bound output. |
| `observe` | Full observation or ordered line replacement patch with `--since BASELINE`; retains at most eight document-local baselines. |
| `extract <target>` | Typed table/form/list data and source refs; `--kind`, `--limit`, `--text-max`, `--schema`. Check `truncated`. |
| `watch <target>` | Event-driven DOM condition: `--state`, `--equals`, `--duration` (0–25 seconds); returns `matched=false` on expiry. |
| `capabilities` | Supported observation/input paths and explicit limitations. |
| `text` | The visible text, as the browser lays it out. The cheapest way to read. |
| `markdown` | The page as markdown with tables and absolute links. `--main` drops navigation and page chrome. |
| `html`, `links` | The live DOM; every link as text and URL, with `--filter`. |
| `get <target>` | One element's text or value, or an attribute with `--attr`. |
| `info` | URL, title, HTTP status, viewport, scroll, ready state, visibility, and counts of links, forms, images and iframes. |

### Act

Native pointer and keyboard paths produce trusted input. Actions wait for actionability where supported; direct value setters, select events and HTML5 drag/drop have explicit untrusted fallbacks. Inspect the returned receipt and application state.

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
| `record start`, `record stop`, `record status` | Explicit bounded PNG-sequence recording; start requires a new `--path` directory. |
| `processes` | Native physical footprint and cumulative CPU by unique owned or attributed PID; tab mapping and explicit coverage gaps. Samples may be cached for two seconds; CPU is not per-tab utilization. |
| `inspector [--stop]` | Token-protected loopback dashboard, on-demand frames and human takeover. |
| `diagnose --path <file>` | Metadata-only diagnostic bundle, at most 512 KiB, mode 0600. |

### Observe

| Command | What it does |
|---|---|
| `console` | What the page logged, uncaught errors included. `--level`, `--clear`. |
| `network` | Documents, fetch and XHR calls, and every loaded resource, with status, timing and size. `--failed`, `--filter`, `--clear`. |
| `downloads` | Files the page downloaded, saved under `~/Downloads/Umbra/`. |
| `cookies [domain]` | Cookie names, domains and expiry dates. Never values. |

### Runtime and workflows

| Command | What it does |
|---|---|
| `runtime [--reclaim]` | Scheduler/queue/admission/memory-pressure counters and ownership. Memory covers the daemon only. |
| `tasks` | Pending jobs and last 128 completion records, without command arguments. |
| `cancel <id>` | Interrupt a runtime request with matching agent/task; already-dispatched JavaScript can continue, and side effects cannot be undone. |
| `lease [--seconds n] [--release]` | Acquire/renew/release a tab's cooperating agent/task ownership. Requires `--agent`. |
| `batch <steps>` | Execute a JSON array of 1–64 steps with optional before/after conditions; stop on failure, no automatic retry. |
| `workflow <file> --checkpoint <path>` | Parameterized JSON steps and atomic checkpoints; `--vars`, `--resume`, explicit `--resolve verified` or `retry` for uncertain steps. |
| `resources <preset>` | `lean`, `balanced` or `visual`; tab rules persist until changed, including after task completion. `--reload --until JS` restores previous rules on readiness failure; interruption can leave applied rules in place. |
| `http <url>` | Bounded public UTF-8 HTTP without a WebKit tab, JavaScript or profile cookies; `--max` is a byte budget. |

`batch` and `workflow` carry agent/task/deadline at the coordinator level; put each target `tab` in the step's `args`. See [recovery and safety limits](runtime/README.md#batch-known-steps-and-checkpoint-a-workflow).

### Frames and coordinates

| Command | What it does |
|---|---|
| `frames` | List registered main/cross-origin frame document IDs; refresh after navigation. |
| `frame <id> <operation> --args '<json>'` | Explicit frame-local snapshot/observe/extract/get/watch/eval/click/fill/check/uncheck/select; arguments at most 64 KiB. |
| `pointer <action> --x n --y n` | Trusted left-button click/move/down/up at CSS viewport coordinates; no inferred canvas semantics or application-effect verification. |

Native frame input refuses unavailable, hidden, covered, transformed, zoomed or offscreen geometry. Closed shadow roots remain unsupported. [Frame examples](runtime/README.md#reach-frames-and-canvas-carefully).

### One-shot

These keep their original contract for scheduled scripts: one process, a result on stdout and an exit code. When a daemon is running they go through it; otherwise they run on their own.

| Command | What it does |
|---|---|
| `read <url> < script.js` | Load a page in a scratch tab, run the script (or print the HTML when it's empty) and close the tab. `--cookie NAME` exits 3 when signed out; `--until`, `--wait`. |
| `fetch <url>` | GET a URL with the browser's session, from a page on the same site (`--from`, default `/robots.txt`). |
| `download <url> <file>` | Download with the session, replacing `<file>` once complete. |
| `keep-signed-in [site…]` | Visit the sites marked `keepSignedIn` and report whether each is still signed in. Naming sites marks them and installs the launch agent that visits every 10 minutes; `--off` unmarks them. Exits 3 when any is signed out, otherwise 4 when any couldn't be checked. See [sites.md](sites.md#keepsignedin). |

[docs/recipes.md](recipes.md) has worked examples for both styles.


## Exit codes

Results go to stdout and messages to stderr. Over MCP, a failure comes back as a tool error with the message and, for codes 1 to 4, a hint about what to do next.

| Exit | Meaning | What to do |
|---|---|---|
| `0` | Done | |
| `1` | Umbra itself failed | Read stderr; the next command starts the daemon again |
| `2` | Deadline, cancellation, or a script error | Inspect actual state before retrying an action; use a condition or fix the script |
| `3` | Signed out (`read --cookie`, `keep-signed-in`) | Run `umbra sign-in <site>` |
| `4` | Page/download failure, output bound or uncertain workflow step | Read the message; verify actual state before resolving or repeating a step |
| `5` | HTTP error status (`fetch`, `download`, `http`) | Check the URL and your access |
| `64` | Usage error | Fix the command; `umbra help <command>` |
| `78` | `sites.json` can't be parsed | Fix the file; the message names the problem |
