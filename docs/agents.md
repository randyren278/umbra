# Using Umbra from an agent

Coding agents such as Claude Code can use Umbra through their shell tool. An agent runs a command, reads stdout, and branches on the exit code. It sees the web the way you do, signed in, while your screen and your Safari stay yours.

This page covers what works in 0.1.0: the one-shot `read`, `fetch` and `download` commands. Live tabs, snapshots, clicks and an MCP server are planned but not built yet (see [What's next](../README.md#whats-next)).

## Instructions to give your agent

Paste this into the `AGENTS.md`, `CLAUDE.md` or system prompt your agent reads, and adjust the sites:

```markdown
## Signed-in web pages: use umbra

`umbra` is a headless WebKit browser with its own signed-in cookie store. Use it instead of
Safari, AppleScript or curl for pages that need a sign-in. It never opens a window.

- Run JavaScript in a page and print the result: `umbra read <url> <<< 'return document.title'`.
  The script is the body of an async function. Return a string, or an object or array to get JSON.
- Get a page's HTML: `umbra read <url> < /dev/null`. Always give `read` a script or
  `< /dev/null` on stdin.
- Call the site's API with the session: `umbra fetch <url>` (same origin as the site).
- Save a file: `umbra download <url> <path>`.
- If the page redirects or renders late, add `--until '<JavaScript expression>'` to `read`.
- Exit 3 means signed out. Stop and ask the user to run `umbra sign-in <site>`. Never try to
  sign in yourself, and never fall back to Safari.
- Exit 2 is a timeout or a script error (stderr says which), 4 is a page that failed to load,
  and 5 is an HTTP error (the body is still printed).
```

Every command in that block was run against a live site and a local server before this page was written.

### Why "always give `read` a script"

`read` takes its script from stdin whenever stdin isn't a terminal. If an agent's shell leaves stdin open as a pipe that never closes, `read` waits for the script that never comes. A here-string (`<<<`), a file (`< script.js`) or `< /dev/null` always ends.

## When the agent hits exit 3

Signing in happens in a window, with your password and your second factor, so it stays your job. The agent's part is to notice exit 3 and say which site needs it:

```console
$ umbra read --cookie li_at https://www.linkedin.com/feed/ <<< 'return 1'
signed out of www.linkedin.com
```

You then run `umbra sign-in LinkedIn`, sign in, close the window, and tell the agent to carry on. Sites marked `keepSession` in [sites.json](sites.md#keepsession) stay signed in across runs, so this should be rare.

Add `--cookie NAME` to a `read` whenever the site has a clear session cookie. Without it, a signed-out page loads normally and the agent may happily read the login page.

## Give the agent its own profile

A profile is a separate cookie store. Giving an agent its own means it can only reach the sites you signed it into, and its experiments can't disturb the store your scheduled jobs use:

```sh
UMBRA_PROFILE=agent umbra sign-in https://canvas.example.edu/login
```

Then have the agent's shell export `UMBRA_PROFILE=agent`, or tell the agent to prefix its commands with it. Runs on different profiles also don't wait on each other's lock.

## What an agent can and can't do here

- It acts as you. Any site you're signed into, the agent can read, and its JavaScript runs with the page's full power. Umbra doesn't stop a script from pressing a button. Give agents the profiles and sites you'd trust them with.
- It never sees cookie values. `umbra cookies` prints names and expiry dates only, and nothing in Umbra exports a value.
- It can't sign in, pass two-factor prompts or solve CAPTCHAs. Those stay with you.
- Runs on one profile take turns. An agent that fires several commands at once on the same profile gets them one after another.
