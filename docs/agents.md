# Using Umbra from an agent

An agent can use Umbra in three ways. They all drive the same live tabs, so you can mix them:

- **Agent skill** (Claude Code and Codex). [`skill/SKILL.md`](../skill/SKILL.md) teaches the loop, targets, waiting, sign-in, handoff and the rules. The agent then works through its shell tool.
- **MCP.** `umbra mcp` exposes every command as a tool. See [mcp.md](mcp.md).
- **Plain instructions.** For any agent with a shell, paste the block below into `AGENTS.md`, `CLAUDE.md` or the system prompt.

## Install the skill

```sh
mkdir -p ~/.claude/skills ~/.codex/skills
ln -s "$PWD/skill" ~/.claude/skills/umbra    # Claude Code
ln -s "$PWD/skill" ~/.codex/skills/umbra     # Codex
```

Claude Code and Codex pick it up in new sessions and use it whenever a task needs a browser: a page behind a login, JavaScript-rendered content, a form, a download that needs a session, or a screenshot.

## Instructions for any agent

```markdown
## Web browsing: use umbra

`umbra` is a headless WebKit browser on this Mac. It's invisible, signed in to the user's
sites, and keeps tabs alive between commands. Use it instead of Safari, AppleScript or
Computer Use for anything on the web.

- Loop: `umbra open <url>`, then `umbra snapshot --interactive` (an outline with refs like
  e3), then act: `umbra click e4`, `umbra fill e3 "text"`, `umbra select e5 "Label"`,
  `umbra press Enter`. Snapshot again after anything that changes the page; refs reset
  after a navigation.
- Targets: a ref from the latest snapshot, `text=Visible text`, or a CSS selector.
- Wait for conditions, never sleep: `umbra wait --text Saved` (or --selector, --url, --gone, --idle).
- Read cheaply: `umbra text`, `umbra markdown --main`, `umbra get <target>`. Use
  `umbra screenshot shot.png` only when layout matters.
- Signed out (exit 3, or a login page where content should be): tell the user which site,
  then run `umbra sign-in <site>`; it returns once they've signed in. Never type their passwords.
- 2FA, CAPTCHA or consent: `umbra handoff "What the user should do" --until "<JS condition>"`.
- If the request asks for an action (refund it, submit the form), do it and report what
  happened. If a consequential action (purchase, post, message, submission, refund,
  deletion) is your own idea, ask first.
- Add --json for structured output. `umbra help <command>` lists parameters.
```

The commands in that block were checked against the Northwind Ops test app in [`bench/site`](../bench/README.md) while this page was written. The `sign-in` and `handoff` windows weren't opened.

## Working well

- **Snapshot small.** `snapshot --interactive` keeps only elements with refs, plus headings. `--scope main` (or any ref, `text=` or selector) narrows it to the part that matters. That keeps the context window for the task.
- **Read before you act.** `text` and `markdown --main` are much cheaper than a screenshot, and they include content that isn't interactive, such as prices, statuses and table cells.
- **Trust the waits that are built in.** `open` waits for the page to load. `click` waits until its target is visible, enabled and not covered, and waits for any page it opens. Use `wait` only for content that arrives later, such as `wait --text "Settings saved"` after a save. `--text` matches case-sensitively.
- **One tab per task.** `open <url> --tab NAME` opens a named tab (created if new), and `--tab NAME` on any later command addresses it. Pop-ups (`target=_blank`, `window.open`) become tabs of their own, and the click that opened one reports its name. Close a tab when its task is done (`tab close NAME`): each holds a web process, and one nothing uses closes by itself only after 15 minutes.
- **Make pages lighter.** `block images trackers` speeds up heavy pages. `reload` applies it to the current page.
- **Parallel agents need their own profiles.** The current tab and the cookies belong to the profile, so two agents on one profile change things under each other. Give each its own `--profile`, or at least its own `--tab`. Cookies on `127.0.0.1` are shared across ports.
- **Dialogs don't block.** Alerts, confirms and prompts are accepted by default, and the reply of the click that raised one reports it. Run `dialog dismiss` first to decline the next one.
- **Debug with the page's own signals.** `console --level error` and `network --failed` show what went wrong on the page, not just that something did.

## When the user is needed

Signing in happens once per site, in a window, by the user: `umbra sign-in <SiteName>` for a site in [sites.json](sites.md) or `umbra sign-in https://site/login` for any other. The agent's part is to notice it's signed out (exit 3 from `read --cookie`, or a login form in a snapshot where content should be), say which site needs it, and run that command: the window opens on the login page and the command returns once the user is signed in. A site that is already signed in returns at once and is never shown.

Mid-task steps that need a person, such as a two-factor prompt, a CAPTCHA or a consent screen, go through `handoff`. It shows the tab in a window with the agent's message and returns when the user closes it or when the `--until` condition holds. Then the tab hides again and the agent carries on in the same tab.

## Give the agent its own profile

A profile is a separate cookie store with its own daemon. An agent on its own profile can only reach the sites you signed it into, and it can't disturb the profile your scheduled jobs use:

```sh
umbra --profile agent sign-in https://canvas.example.edu/login
```

Then tell the agent to pass `--profile agent` on every command, or set `UMBRA_PROFILE=agent` in its environment (or in its MCP config; see [mcp.md](mcp.md#choosing-a-profile)).

## What an agent can and can't do here

- It acts as you. On a signed-in site the agent can read, click and submit as you would, so give it the profiles and sites you'd trust it with.
- It never sees cookie values. `cookies` prints names and expiry dates only, and nothing in Umbra exports a value.
- It can't sign in, pass two-factor prompts or solve CAPTCHAs on its own. Those stay with you, through `sign-in` and `handoff`.
- It can't reach inside closed shadow roots or cross-origin iframes. `snapshot` marks cross-origin iframes so the agent knows they're there.
