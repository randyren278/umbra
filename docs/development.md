# Testing

```sh
make test       # builds if needed, then runs every suite against the build, never the installed app
```

`tests/run.sh` runs the smoke test and three suites, all against local servers on `127.0.0.1` with throwaway profiles:

- `test_umbra.py`: 77 tests of the one-shot commands and every exit code.
- `test_daemon.py`: 102 tests of the daemon's lifecycle, the session commands, and one-shots served through a daemon.
- `mcp_conformance.py`: 32 tests of the MCP server's protocol, tools and errors.

An invisibility guard runs after every daemon command in the suite. It fails the test if any Umbra window is visible on screen or the daemon has become the frontmost app.

[`bench/`](../bench/README.md) adds the proving-ground app behind the [agent eval](evaluation/README.md) and the [benchmarks](evaluation/latency/README.md).


## Development

```sh
make build     # build/Umbra.app (BUILD=dir builds elsewhere), signed ad hoc as com.randyren.umbra
make icon      # regenerate the logo, icon, banner and how-it-works diagram (assets/build.py)
make release   # dist/Umbra-<version>.zip and its .sha256
make help      # list targets and overrides
python3 assets/charts.py   # redraw the eval, speed and sign-in pictures from docs/evaluation data
```

The loop, WebKit, handoff and profile diagrams are hand-drawn SVGs in the same style; edit them directly.

[AGENTS.md](../AGENTS.md) explains the architecture and the safety rules for working on Umbra, including never touching the default profile. [CHANGELOG.md](../CHANGELOG.md) has release notes.
