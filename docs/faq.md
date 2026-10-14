# FAQ

**How is this different from Playwright MCP?**
Playwright MCP drives Playwright's own browser builds (Chromium by default) and also reads pages as accessibility snapshots with refs. It's cross-platform and a good choice for testing web apps. Umbra is macOS only and uses the WebKit already in the system, with Safari's user agent. Its tabs are invisible but report themselves visible, its input is native and trusted, and it keeps a persistent signed-in store with a sign-in window and handoff for the human steps. The same tabs are reachable from a shell and from MCP at once.

**How is this different from Browser Use?**
Browser Use is a Python framework that puts an LLM in charge of a Chromium browser through its own agent loop. Umbra is only the browser. There's no model inside it; your agent, whether that's Claude Code, Cursor or a script, decides what to do and calls Umbra's commands or tools.

**Why not script Safari with AppleScript?**
`do JavaScript` needs Safari running, "Allow JavaScript from Apple Events" turned on, and a tab in your browser. That tab takes focus and catches your clicks, its events are synthetic, and the script breaks when Safari shows a dialog or someone closes the window. Umbra runs the same engine with none of that. It began as the replacement for exactly that setup.

**Can I still use it without an agent?**
Yes. Scheduled scripts use the one-shot `read`, `fetch` and `download` commands, and the session commands work just as well from a shell script. [docs/recipes.md](recipes.md) has examples in Python, Node and shell.
