# slicer

<img src="assets/slicer-512.png" alt="slicer" width="128" />

A local roadmap and slice manager. Record ideas, specify bounded work, pick what is
ready, and track it through implementation and review. slicer stores, prioritizes,
validates, and renders the work; you supply the planning and implementation.

**[Get started](docs/getting-started.md)** — take one small task from an idea to a
checked roadmap, without an AI agent.

- [How slicer works](docs/how-it-works.md) — concepts, data flow, and lifecycle
- [Why slicer](docs/why-slicer.md) — the workflow problems it solves
- [Agent reference](docs/agents.md) — optional AI workflows, prompts, and JSON contracts

Stdlib Python 3.11+, no dependencies. Runs locally without an AI service or API key.
The canonical state is git-tracked JSON under `.slicer/`; the markdown and HTML in
`.slicer/render/` are generated views. Manage state through the commands or TUI.
**slicer never commits, pushes, or tags.** Coordination stays in git.

## Install

Into its own virtual environment from PyPI. The distribution is `squalor-slicer`
because the `slicer` name on PyPI is taken; the command it installs is still `slicer`.

```sh
python3 -m venv ~/.slicer-venv
~/.slicer-venv/bin/pip install squalor-slicer
ln -s ~/.slicer-venv/bin/slicer ~/.local/bin/slicer   # or anywhere on your PATH
slicer --help
```

`slicer --about` prints the version, description, source URL, and issues URL.
[Other installation options](docs/commands.md#installation-options) include pipx,
uv, and unreleased main. The [contributor install](AGENTS.md#install) tracks a checkout.

## Documentation

- [Getting started](docs/getting-started.md) — a human first-use walkthrough
- [Why slicer](docs/why-slicer.md) — the workflow problems it solves
- [Command reference](docs/commands.md) — commands, flags, lifecycle, CI, and recovery
- [TUI manual](docs/tui.md) — interactive browsing, editing, and the roadmap wizard
- [Outline format](docs/import.md) — import a plan in bulk
- [Configuration](docs/configuration.md) — project settings and defaults
- [Legacy format](docs/migrate-format.md) — migrate an existing slice tree
- [ARCHITECTURE.md](ARCHITECTURE.md) — layering and invariants
- [AGENTS.md](AGENTS.md) — contributing, tests, and house style
- [CHANGELOG.md](CHANGELOG.md) — changes by release

Run `slicer --help` for the command list or `slicer ai instructions` for agent onboarding.
Every command except `tui`, `ui`, `merge-index` and `merge-relay-roles` takes `--json`;
see the [agent reference](docs/agents.md) for payloads and exit codes.
To try slicer, follow [Getting started](docs/getting-started.md); to understand it first,
read [How slicer works](docs/how-it-works.md).

## Tests

```sh
python3 -m unittest discover -s tests -t tests
```

No install step or dependencies. See [AGENTS.md](AGENTS.md) for fixtures and checks.

## Status

slicer manages its own roadmap. Run `slicer status` for progress, `slicer goals` for
its direction, and `slicer --version` for your installed version. The
[changelog](CHANGELOG.md) records releases.

Apache-2.0. Stdlib Python, no dependencies, and none planned.
