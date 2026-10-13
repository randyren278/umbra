---
name: umbra
description: Use when a task needs a web browser - reading or acting on a page, a site behind a login (Canvas, LinkedIn, Instagram, any account), JavaScript-rendered content, filling forms, clicking through a flow, screenshots, downloads that need a session, or scraping your own data - instead of Safari, AppleScript, Playwright, Chrome or Computer Use.
---

# Umbra: the browser for agents

`umbra` is a headless WebKit browser on this Mac. It's invisible (no window, no Dock icon, never takes focus), signed in through its own cookie store, and keeps tabs alive between commands. Never drive Safari with AppleScript, never borrow Safari's cookies for new work, and never take over the screen with Computer Use for web tasks.

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
| Read the page | `snapshot`, `text`, `markdown --main`, `links`, `get <t>`, `info` |
| Act | `click`, `fill`, `type`, `press Enter`, `select`, `check`, `hover`, `scroll`, `drag`, `upload <t> <file>`, `dialog accept` |
| See it | `screenshot [path] [--full] [--target t]`, `pdf <path>` |
| Debug | `console --level error`, `network --failed` |
| Script | `eval 'return …'` (page world, async) |
| Tabs | `tabs`, `tab new|select|close <name>`, `--tab NAME` on any command |
| Speed | `block images trackers`, `viewport 390 844 --mobile` |
| Files | `downloads` (page downloads land in ~/Downloads/Umbra/), `download <url> <file>` |
| One-shot in scripts | `fetch <api-url>`, `read <url> < script.js` (exit codes, no session needed) |

Every command has `umbra help <command>`. Add `--json` for structured output. `umbra mcp` serves the same commands as MCP tools.

## Sign-in and humans

- If the request gives you credentials for the site, sign in with them.
- Otherwise exit code 3, or a login page where you expected content, means signed out. Ask the user to run `umbra sign-in <SiteName>` (sites live in `~/.config/umbra/sites.json`) or `umbra sign-in https://site/login` and sign in themselves. Wait for them; don't ask them to send you their password.
- 2FA, CAPTCHA or consent screens mid-flow: `umbra handoff "Approve the Duo push" --until "location.pathname.startsWith('/app')"`. This shows the tab to the user, then hides it again.

## Rules

- If the request asks for an action (refund it, submit the form, post this), do it, then report what happened.
- If an action with consequences (purchases, posts, messages, submissions, refunds, deletions) is your own idea and not in the request, ask first.
- Never print or save cookie values or tokens. `umbra cookies` shows names only.
- Respect the site's pace. Use one tab per task, and don't hammer accounts (LinkedIn and Instagram reads are rate-limited in FIRST).
- For experiments and tests, use `--profile scratch-<name>` so the real signed-in store is untouched. Run `umbra --profile scratch-<name> stop` when done.

## Common mistakes

- Acting on refs from an old snapshot after the page changed. Snapshot again.
- `sleep 3` instead of `umbra wait …`.
- Using `read` without stdin in a script. Give it a script or `< /dev/null`.
- Expecting `read` to fail on a 404 page. It returns the page; `open` reports the HTTP status.
