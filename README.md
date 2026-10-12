<!-- mcp-name: io.github.romainsantoli-web/memory-os-ai -->
# Memory OS AI

**A local project memory shared between your assistants: dated decisions, next actions and verifiable history,
under your control.**

Status: **ALPHA**. **Source candidate: 4.0.6** (2026-10-12), not published or deployed.
**Current published release: 4.0.5** (PyPI, 2026-10-11). macOS Apple Silicon first.
The new candidate must pass artifact qualification and a clean-install trial before publication.

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

For the **4.0.6 candidate**, use the exact locally qualified wheel in a durable environment.
The clean-install trial is still pending; the following candidate commands require this wheel,
not the current PyPI package:

```bash
python3.11 -m venv .venv-memory-os
.venv-memory-os/bin/python -m pip install '/ABS/PATH/memory_os_ai-4.0.6-py3-none-any.whl[ops,brief]'
.venv-memory-os/bin/memory-os-admin quickstart --clients codex,cursor  # preview: no writes or downloads
# After reviewing the preview, with separate consent for downloads:
.venv-memory-os/bin/memory-os-admin quickstart --clients codex,cursor --apply --download-approved
```

Optional `--demo` explicitly saves/searches a fact in a unique demo project. This verifies the
packaged relay; it does not measure a real handoff between assistants. See the packaged client matrix
in `docs/CLIENTS.md`. Keep the environment for the service; the canonical project registry remains inactive.

**Known fresh-install defect (2026-10-12):** published 4.0.4/4.0.5 allow an incompatible MCP 2.x
resolution, which can prevent the server from starting. Already qualified/pinned installations are
unaffected. Unconstrained fresh installation of these releases is not a qualified onboarding path.
The 4.0.6 candidate fixes dependency resolution, but its replacement clean-install trial is still pending.

For an **existing qualified 4.0.5 installation**, keep its qualified environment.
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
pinned resources and needs `--apply --download-approved` for missing files. The candidate implements
`memory-os-admin quickstart --clients claude-code,codex` as a zero-write preview; explicit `--apply`
configures a new macOS installation, with separate download and demo consent. Existing different services,
non-empty stores and unsafe configs are refused. Client configuration undo is semantic and preserves the
store/service/token/resources. The exact 4.0.6 artifacts and complete onboarding still require qualification
and a clean-install trial before release; they are absent from published 4.0.5.

For **new 4.0.6 quickstart installs**, SQLite with work state/tasks is the default;
`--backend jsonl` is the explicit alternative. Existing installations are never migrated implicitly.
The fresh SQLite default directory is `~/.local/share/memory-os/store`; JSONL retains its historical directory.
The candidate pins the full active runtime closure for macOS arm64 CPython 3.11:
61 core packages plus 4 with brief, including MCP 1.26.0, pydantic 2.12.5, the qualified
transport stack, torch 2.10.0 and the numerical/tokenizer dependencies. Versioned constraints
and the METADATA provenance ledger are embedded; optional cloud/document/dev extras are outside this lock.
Quickstart refuses a different installed profile before activation. A matching version list is an attestation,
not a guarantee of identical computation on every machine. Use the returned `ops_env` for administration
in a fresh shell; quickstart does not change your shell defaults or schedule backup jobs.

Provisioning/quickstart keeps a conservative **5 GB free-disk default**. An explicit operator override
`MEMORY_V4_MIN_FREE_BYTES` must be integer bytes ≥ 3,000,000,000; invalid/lower values are refused.
Preview retains the provisioning-peak + 1 GB margin; the 8.5 GB swap cap is unchanged.
This override is **not a user recommendation**, does not alter historical campaign guards,
and is not persisted in service or client configurations.

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
- End-to-end answer pilot: 10 observed questions, C4 0.40 vs BM25 0.20, Codex reader/judge;
  **descriptive only**, different judge from published papers, not a held-out comparison or ranking.
- **No productivity gain measured** to date. L11, the handoff study between assistants, was not run.
  The LongMemEval-S and BEAM results above are descriptive, not official leaderboard scores.

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
