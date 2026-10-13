# Umbra over MCP

`umbra mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server on stdio. Its tools are Umbra's commands. Each call goes to the profile's daemon, which it starts on first use, so an MCP client and the `umbra` CLI drive the same live tabs. An agent can open a page through MCP, and you can look at the same tab from a terminal with `umbra snapshot`.

## Set it up

```sh
umbra mcp --print-config
```

This prints the setup for Claude Code, Claude Desktop and Cursor, filled in with the full path of the `umbra` you ran it with. After `make install` that path is `~/Applications/Umbra.app/Contents/MacOS/umbra`.

**Claude Code** (add `--scope user` to use it in every project):

```sh
claude mcp add umbra -- ~/Applications/Umbra.app/Contents/MacOS/umbra mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`) and **Cursor** (`~/.cursor/mcp.json`) take the same block. Use the absolute path that `--print-config` prints, since these files don't expand `~`:

```json
{
  "mcpServers" : {
    "umbra" : {
      "args" : [
        "mcp"
      ],
      "command" : "/Users/you/Applications/Umbra.app/Contents/MacOS/umbra"
    }
  }
}
```

### Choosing a profile

Without `UMBRA_PROFILE`, the server uses your default profile and its sign-ins. To give an agent its own store, run `--print-config` with the profile set. It then adds the variable to every snippet:

```sh
UMBRA_PROFILE=agent umbra mcp --print-config
# Claude Code: claude mcp add umbra -e UMBRA_PROFILE=agent -- …/umbra mcp
# JSON clients: "env" : { "UMBRA_PROFILE" : "agent" }
```

Then sign that profile in to the sites the agent should reach: `UMBRA_PROFILE=agent umbra sign-in https://site.example/login`.

## What the agent gets

- **41 tools**, one per command except `stop`, with the same parameters as the CLI. `umbra schema` prints them as JSON, and `umbra help <command>` describes each one.
- **Instructions** sent on `initialize`. They teach the loop (`open`, `snapshot`, act on refs, snapshot again), waiting instead of sleeping, `handoff` for human-only steps, and `fetch` for signed-in JSON APIs.
- **Text plus structured results.** Every result has a short text line. The other fields arrive as `structuredContent`, the same fields `--json` prints on the CLI.
- **Images.** `screenshot` without a path returns the image itself as MCP image content, alongside a text line such as `88×48 PNG (base64 in data)`.
- **Tool hints.** Tools that only look (`snapshot`, `text`, `markdown`, `links`, `info`, `console`, `network`…) are marked read-only. `screenshot`, `pdf` and `download` are marked destructive because they can replace a file at the path given. Every tool is marked open-world.
- **Errors the model can act on.** A failure comes back as a tool error with Umbra's message. Codes 1 to 4 also get a hint: a signed-out site, for example, says to call `sign-in` with the site's name, which shows the person Umbra's window and returns once they've signed in.
- **Progress for long calls.** If the client sends a progress token, `open`, `wait`, `handoff` and other long calls report progress every 5 seconds, so the client's request timeout doesn't give up on them.

Relative paths in arguments (`screenshot shot.png`, `upload … invoice.pdf`) are resolved against the directory the server was started in, unless the call passes `cwd`.

## Handoff over MCP

When a page needs a person (a two-factor prompt, a CAPTCHA, a consent screen), the agent calls `handoff` with a short message and, ideally, an `until` condition. The tab appears in a window on your screen with the message. The tool call returns when you close the window or the condition holds, and the tab hides again. The default timeout is 15 minutes, and progress notifications keep the call alive meanwhile.

## Troubleshooting

| Symptom | Check |
|---|---|
| The client lists no tools | Run the command from the config by hand: `…/umbra mcp` should wait silently on stdin. Check the path is absolute. |
| Every page shows a sign-in screen | The server is on another profile than the one you signed in. Compare `UMBRA_PROFILE` in the config with your `umbra sign-in` run. |
| Calls hang or fail after a crash | `umbra status` shows the daemon. `umbra stop` (with the same profile) resets it; the next call starts a new one. Its log is `~/Library/Application Support/Umbra/daemon.log` (`daemon-<profile>.log` for a profile). |
| A call times out | Pass a larger `timeout`, or `wait` for a specific selector, text or URL first. |
