<!-- mcp-name: io.github.romainsantoli-web/memory-os-ai -->
# Memory OS AI

**A local project memory shared between your assistants: dated decisions, next actions and verifiable history,
under your control.**

Status: **ALPHA**. **Current version: 4.0.5** (PyPI, 11/10/2026). **4.0.6 = candidate** under preparation (simplified
install), not published. macOS Apple Silicon first. Draft integrated after Codex review (2026-10-11); candidate features are not yet published.

## What it does

You work with several AI assistants (Claude Code, Codex, Cursor, Copilot, Grok…). Each one has its own memory, or none.
Memory OS AI is a **local MCP server** that they all share:

- **Resume a project**: on startup, a compact brief (≤ 1,500 tokens) gives the objective, the decisions in force,
  the next action, with open tasks when SQLite/work state is enabled. Compact mode requires the optional offline tokenizer table.
- **Record a decision**: dated, sourced facts; a correction explicitly replaces the old one (history kept).
- **Hand over work**: shared tasks (open / blocked / done) with protection against concurrent writes (CAS),
  so that two assistants do not overwrite each other.

The server needs **no LLM**: your assistant extracts the facts, local search costs no API call.
Note: what your assistant reads in memory then enters **its own** context (cloud if your assistant is in the cloud).

## Install (macOS Apple Silicon, Python ≥ 3.11)

The current published release requires manual provisioning and service setup by an operator. It is not yet a
qualified ten-minute installation for a new user. Install into a durable environment (not a `uvx` daemon):

```bash
uv tool install 'memory-os-ai[ops,brief]==4.0.5'  # requires uv; current published package
memory-os-admin --help
```

Before starting the server: explicitly provision Granite (~220.2 MB, revision
`835ad14087e140460703cf0fae09f97d469d65c2`), tiktoken 0.12.0 and the cl100k table (1.68 MB); create a 0600
token with `memory-os-admin token create`; prepare the qualified release commit declaration required by 4.0.5;
then use `memory-os-admin setup` to generate client snippets/service templates and `memory-os-admin doctor`
to check the running installation. **Setup creates the token if needed and prints snippets; it does not install
or start a service or edit client configs.** Model loading is offline by default; a missing model is a failure,
not a silent download. Runtime dependencies (including torch) are additional to model weights; installation
size/time still needs a clean-machine measurement.

**4.0.6 candidate only, not available in PyPI 4.0.5:** document libraries move to `[documents]` and `[legacy-v3]`;
a verified embedded identity removes the manual commit declaration; `memory-os-admin provision` previews
pinned resources and needs `--apply --download-approved` for missing files. `quickstart` is planned and not
implemented yet. The complete onboarding instructions will be qualified before release.

## Supported assistants

| Assistant | Configured | Connection verified | Real usage observed |
|---|---|---|---|
| Claude Code | yes | yes | yes (brief at startup, saves, assisted capture) |
| Codex (CLI / ChatGPT app) | yes | yes | yes (saves, tasks) |
| Grok CLI | yes | yes (handshake, tools) | not measured |
| Claude Desktop | yes (edit with the app closed) | to verify | not measured |
| VS Code (Copilot agent mode) | yes | to verify (Copilot Chat extension) | not measured |
| Cursor · Antigravity · Zed · Devin Desktop · Windsurf · Gemini CLI | yes | to verify | not measured |

Definitions: *configured* = valid entry detected by `doctor`; *connection verified* = MCP handshake/tool call
observed; *real usage* = memory used in real work. These levels describe the operator deployment reported on 2026-10-11, not every installation.
The candidate source/sdist includes `docs/CLIENTS.md`; packaged documentation is installed under
`share/doc/memory-os-ai`. No unpublished documentation URL is presented as live.

## Security and control

Local server (`127.0.0.1`), private access token (0600) read by the relay: **never written into a client
configuration**. Memories are data, never instructions. Maintenance operations (consolidation) are refused
without explicit authorization. In SQLite mode (opt-in in 4.0.5): encrypted online backups and a restore test.

## Results, with their conditions

- 4.0.5 qualification: more than 1,600 v4 tests plus the v3 suite; outage/reconnection/no-replay TCP tests,
  and qualification on a copy of the operator store. This is engineering evidence, not a productivity score.
- Compact brief: 400 exact measurements, max 1,349 tokens / 3,678 bytes.
- LongMemEval-S retrieval: C4 Recall@10 0.754 vs BM25 0.712, paired delta +0.042 [0.022, 0.061];
  MiniLM/CPU, 466 evidence clusters, observed questions and conditional intervals: **not independent**.
- BEAM 100K retrieval: C4 0.462 vs BM25 0.445, delta +0.017 [-0.004, +0.040], 20 conversations,
  Granite/CPU: the preregistered recall criterion **failed**. These evaluations differ from the operator
  production profile (Granite, auto/MPS, native precision, batch 32). No official leaderboard claim.
- **No productivity gain measured** to date (handoff study between assistants not run).

## Limits (ALPHA)

macOS Apple Silicon first; Linux and Windows not qualified. A single machine (no multi-device sync), a single user.
Alternatives: Mem0 (OSS and self-hostable), Zep (managed memory) / Graphiti (open-source temporal graph), Letta (agents with memory), Supermemory
(MCP memory, managed service), native memories of the assistants (some with import/export). Different products; we
claim no superiority: our angle is a local memory shared between existing assistants, with explicit
mutation contracts.

## Older test suites

The 4.0.4 source test suite could overwrite real client MCP configs. HOME/config isolation was fixed and
qualified in 4.0.5. Do not run the 4.0.4 suite against your real home.

## Licence

LGPL-3.0-or-later. Third-party model: IBM Granite embedding (see its licence).
