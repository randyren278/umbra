---
name: umbra
description: Use when a task needs a web browser - reading or acting on a page, a site behind a login (Canvas, LinkedIn, Instagram, any account), JavaScript-rendered content, filling forms, clicking through a flow, screenshots, downloads that need a session, or scraping your own data - instead of Safari, AppleScript, Playwright, Chrome or Computer Use.
---

# Umbra: the browser for agents

`umbra` is a headless WebKit browser on this Mac. It's invisible while it works (no window, no Dock icon, never takes focus), signed in through its own cookie store, and keeps tabs alive between commands. Never drive Safari with AppleScript, never borrow Safari's cookies for new work, and never take over the screen with Computer Use for web tasks.

## The loop

```sh
umbra open example.com/login          # loads, waits, prints "Title · URL · HTTP 200"
umbra snapshot --interactive          # outline with refs:  - textbox "Email" [ref=e3]
umbra fill e3 me@example.com          # trusted typing (React-safe)
umbra click "text=Sign in"            # waits until clickable; follows the navigation
umbra snapshot --interactive          # refs change after navigation: snapshot again
```

Targets are a ref from the latest snapshot (`e12`), `text=Visible text`, or a CSS selector. Wait for conditions instead of sleeping: `umbra wait --text Saved`, `--selector`, `--url`, `--gone`, `--idle`, `--js`.

## Quick reference

| Need | Command |
|---|---|
| Read the page | `snapshot --max N --depth N`, `observe --since BASELINE`, `text`, `markdown --main`, `links`, `get <t>`, `info` |
| Structured records | `extract <target> --kind table\|form\|list --limit N --text-max N`; check `truncated` |
| DOM event wait | `watch <target> --state text --equals Saved --duration 10` (matched=false on expiry) |
| Act | `click`, `fill`, `type`, `press Enter`, `select`, `check`, `hover`, `scroll`, `drag`, `upload <t> <file>`, `dialog accept` |
| See it | `screenshot [path] [--full] [--target t]`, `pdf <path>` |
| Debug | `console --level error`, `network --failed` |
| Script | `eval 'return …'` (page world, async) |
| Tabs | `tabs`, `tab new\|select\|close <name>`, `--tab NAME` on any command |
| Resources | `resources lean\|balanced\|visual [--reload --until JS]`; cookie-free `http <url> --max BYTES` |
| Coordinate work | `frames`, `frame <id> <operation> --args JSON`, `pointer click --x N --y N`; canvas semantics unavailable |
| Shared runtime | `lease --agent A --task T --tab NAME`, `tasks`, `cancel ID --agent A --task T`, `runtime` |
| Recovery | `batch JSON`, `workflow FILE --checkpoint FILE [--resume]`; no automatic uncertain-step retry |
| Inspect explicitly | `inspector [--stop]`, `record start\|stop\|status`, `diagnose --path FILE` |
| Files | `downloads` (page downloads land in ~/Downloads/Umbra/), `download <url> <file>` |
| One-shot in scripts | `fetch <api-url>`, `read <url> < script.js` (exit codes, no session needed) |

Every command has `umbra help <command>`. `umbra schema` is the parameter source; page commands accept `--tab`, all daemon commands accept agent/task/request/deadline metadata. `deadline` is absolute Unix seconds. [Runtime guide](../docs/runtime/README.md) and [persistent SDKs](../sdk/README.md) cover the details. Add `--json` for structured output. `umbra mcp` serves the same commands as MCP tools.

## Parallel work and recovery

Use a named tab for each task. With cooperating clients on one profile, acquire a lease and pass matching `--agent` and `--task` on every tab command. Different signed-in identities need separate profiles. Leases are daemon-local coordination labels, not authentication; same-user processes can supply them.

Give cancellable work a unique `--request`. `tasks` lists actual runtime IDs; `cancel ID` requires matching ownership. A timeout, cancelled call or lost socket does not prove an action did not happen. Check real state before repeating a submission; already-dispatched JavaScript can continue after cancellation and produce later side effects, while earlier actions cannot be rolled back.

For deltas, keep the returned `baseline`, apply `patch.start/delete/lines` to the previous snapshot lines and use identical scope/budgets. Keep reconstructed refs/url/title/truncated/depthLimited/doc with each token: absent delta metadata inherits from the requested baseline, while emitted values replace it, including false. Full/reset results replace the state; changed describes outline changes only. Navigation or eviction returns full content and metadata. Budgets count characters, not model tokens. `watch` needs observed DOM/input events; use `wait --js` for silent property changes.

Put each target tab in a batch/workflow step’s `args`; coordinators do not take top-level CLI `--tab`. Checkpoint `pending` means outcome uncertain. Resume refuses non-repeatable pending steps until explicitly resolved; use verified postconditions or an authorized retry. SDK repair callbacks observe and propose only; they never auto-execute or prove earlier dispatch did not occur.

Resource presets replace the tab’s blocked-resource rules until changed; task/lease completion does not restore them. With `--reload --until`, readiness failure restores prior rules, while cancellation can leave applied rules in place.

Use `frames` then an explicit frame ID for cross-origin DOM. Handles change on navigation; native frame input refuses unsupported geometry. Closed shadow roots and semantic canvas extraction are unsupported. Coordinates alone do not prove application success.

## Sign-in and humans

- If the request gives you credentials for the site, sign in with them.
- Otherwise exit code 3, or a login page where you expected content, means signed out. Tell the user which site needs them, then run `umbra sign-in <SiteName>` (sites live in `~/.config/umbra/sites.json`) or `umbra sign-in https://site/login`. Umbra's window opens on that login page for them and the command returns once they're signed in (exit 0), or exits 3 if they close it. Never ask them to send you their password. For a site that isn't listed and that the user will need again, `umbra sign-in https://site/login --save Name` adds it to the list as it signs them in (the Umbra app's Add Account does the same).
- A site that signs the user out again soon after they sign in (Canvas does when left idle) can be kept signed in: suggest `umbra keep-signed-in <SiteName>`, which installs a launch agent that visits it every 10 minutes.
- 2FA, CAPTCHA or consent screens mid-flow: `umbra handoff "Approve the Duo push" --until "location.pathname.startsWith('/app')"`. This shows the tab to the user, then hides it again.

## Rules

- If the request asks for an action (refund it, submit the form, post this), do it, then report what happened.
- If an action with consequences (purchases, posts, messages, submissions, refunds, deletions) is your own idea and not in the request, ask first.
- Never print or save cookie values or tokens. `umbra cookies` shows names only.
- Respect the site's pace. Use one tab per task, and don't hammer accounts (LinkedIn and Instagram reads are rate-limited in FIRST).
- Close your tab when the task is done: `umbra tab close NAME`. Every open tab holds a web process (hundreds of MB for a busy site). A tab nothing has used for 15 minutes closes by itself; after that, `open --tab NAME <url>` opens it again.
- For experiments and tests, use `--profile scratch-<name>` so the real signed-in store is untouched. Run `umbra --profile scratch-<name> stop` when done.

## Common mistakes

- Acting on refs from an old snapshot after the page changed. Snapshot again.
- `sleep 3` instead of `umbra wait …`.
- Using `read` without stdin in a script. Give it a script or `< /dev/null`.
- Expecting `read` to fail on a 404 page. It returns the page; `open` reports the HTTP status.
