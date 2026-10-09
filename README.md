<!-- mcp-name: io.github.romainsantoli-web/memory-os-ai -->
# Memory OS AI

> **One local memory for all your AI assistants.** Decisions, facts and lessons saved by Claude are recalled by
> Codex, Copilot, Cursor, Gemini or Grok — on your machine, under your control, with evidence for every claim.

[![PyPI](https://img.shields.io/pypi/v/memory-os-ai)](https://pypi.org/project/memory-os-ai/)
[![Python](https://img.shields.io/badge/python-3.11+-blue)]()
[![MCP](https://img.shields.io/badge/MCP-2025--11--25-purple)]()
[![License](https://img.shields.io/badge/license-LGPL--3.0--or--later-orange)]()

Every AI assistant forgets between sessions, and nothing it learns reaches the next assistant you open. Memory OS AI
is a local [MCP](https://modelcontextprotocol.io) server that gives all of them the **same persistent memory**:
typed, dated, sourced and isolated per project. It needs **no LLM on the server side** — the assistant you already
use extracts the facts — so recall costs no API call and works offline.

## Quick start (macOS)

```bash
pip install 'memory-os-ai[ops]'
memory-os-admin setup --write-launchd ~/memory-os-launchd   # creates your private access token + prints client configs
cp ~/memory-os-launchd/*.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.memory-os-v4.plist
memory-os-admin doctor                                       # checks the server, the security and every AI client
```

`doctor` ends with a line such as `17 checks: 16 pass, 1 warn, 0 fail`. Full guide: [`v4/docs/OPERATIONS.md`](v4/docs/OPERATIONS.md).

## Works with your assistants and editors

All clients connect through `memory-os-relay`, which reads your private token file itself — **no secret is ever
written into a client configuration**. `memory-os-admin setup` prints the exact snippet for each one, and
`memory-os-admin doctor` tells you which ones are connected.

| Client | Configuration | Status in 4.0.1 |
|---|---|---|
| Claude Code | `~/.mcp.json` | verified end to end |
| Claude Desktop | `claude_desktop_config.json` (edit with the app closed) | verified |
| OpenAI Codex (CLI / app) | `codex mcp add …` | verified end to end |
| VS Code / GitHub Copilot | `Code/User/mcp.json` | configuration generated and checked |
| Google Antigravity | `~/.gemini/config/mcp_config.json` | configuration generated and checked |
| Grok CLI (xAI) | `grok mcp add …` | verified (`grok mcp doctor`: handshake, 16 tools) |
| Cursor · Gemini CLI · Windsurf | `~/.cursor/mcp.json` · `~/.gemini/settings.json` · `~/.codeium/windsurf/mcp_config.json` | configuration generated |

Shared agent rules live in [`AGENTS.md`](AGENTS.md): start with `memory_session_brief`, search before assuming,
save atomic dated facts with `memory_chat_save`. The server also sends these rules to every client at connection.
Claude Code gets two optional hooks: the project brief injected at session start (framed as data, never as
instructions) and a reminder to save after long sessions.

## What makes it different

- **One memory, every assistant.** The point is continuity *between* AI vendors; built-in memories (ChatGPT,
  Claude, Gemini, Copilot) stay locked to one of them.
- **Local and private by default.** One server per user on `127.0.0.1`, mandatory bearer token (0600 file),
  Host/Origin validation against DNS-rebinding, no telemetry. Memories are treated as data, never as instructions.
- **No LLM on the server.** Extraction is done by the client assistant at save time; search is hybrid dense + BM25
  with multi-query fusion. A warm search takes about 15 ms in the evaluation harness; cold start (server and
  model load, ~3 s) and ingestion are paid once, not per query.
- **Facts that change correctly.** Bi-temporal facts (`event_time`, `valid_from`/`valid_until`): a fact is only
  replaced explicitly or on a declared unique property — never because two sentences look alike — and history is
  kept. `memory_timeline` answers "how did X evolve", `at_time` answers "what was true on …".
- **Operated like production, even in ALPHA.** Single writer with an inter-process lock, idempotent requests with
  durable receipts, encrypted verified backups (AES-256-GCM) with restore drills, a daily health report, runbooks
  for eight incident types, and a `/ready` endpoint that reports the attested build commit.

## Evidence, not adjectives

Every number below comes from a versioned run you can reproduce; none is a vendor claim.

| What | Result | Protocol |
|---|---|---|
| End-to-end evidence recall, LongMemEval **oracle** (500 questions, no LLM, MiniLM) | Memory OS 0.802 [0.771, 0.832] vs BM25 0.812 [0.781, 0.841]: **no significant difference yet**; vs dense-only +0.098, vs plain files +0.061 (95 % CI, grouped bootstrap) | `v4/bench/e2e`, frozen manifest, failures kept |
| LoCoMo evidence Recall@10 (exploratory) | 0.538 production defaults → 0.815 with offline LLM enrichment and reranking | `v4/RESULTS.md`; offline enrichment is not the production path |
| 4.0.1 release qualification | 478 v3 + 795 v4 tests, two adversarial review rounds, installed-artifact relay proof (round-trip, failure, reconnection, no replay) for wheel and sdist | `releases/4.0.1/QUALIFICATION.md` |

We publish retrieval recall, answer quality and agent-task success **separately**, with sample sizes and
confidence intervals, and always next to simple baselines (BM25, dense-only, files, full context). Beating BM25
significantly on a held-out partition is the next milestone; leaderboard submissions come after independent
reproduction.

## Architecture

```
 Claude · Codex · Copilot · Cursor · Antigravity · Gemini · Grok · Windsurf
        │ stdio (MCP)            one relay per client process, reads the 0600 token file
        ▼
 memory-os-relay ──HTTP + Bearer──▶ memory-os-v4 server (127.0.0.1:8765, launchd)
                                     ├─ MCP tools: brief, search, timeline, chat_save, add_facts, blocks, forget…
                                     ├─ engine: typed bi-temporal items, project scopes, explicit fact updates
                                     ├─ retrieval: dense (granite-97m multilingual) + BM25 + entities, RRF fusion
                                     ├─ single writer lock · request receipts · /health · /ready
                                     └─ store: append-only JSONL today → transactional SQLite (ADR-001, in progress)
 memory-os-admin: setup · doctor · backup · restore-drill · health · hooks
```

Details: [`v4/docs/ARCHITECTURE.md`](v4/docs/ARCHITECTURE.md), [`v4/docs/HTTP_CLIENT_CONTRACT.md`](v4/docs/HTTP_CLIENT_CONTRACT.md),
[`v4/docs/ADR-001-SQLITE.md`](v4/docs/ADR-001-SQLITE.md), [`v4/docs/READINESS.md`](v4/docs/READINESS.md).

## Honest limits of this ALPHA

- One user, one machine (macOS qualified). Projects organise memory; they are not a security boundary between
  people. No remote access: assistants that run only in the cloud cannot reach a local server yet.
- The JSONL store and request receipts are not atomic across files; industrial durability guarantees arrive with
  the transactional SQLite store (ADR-001 stages S2–S5).
- Retrieval currently matches BM25 rather than beating it on LongMemEval oracle; answer-accuracy and agent-task
  benchmarks are being built.

## Development

```bash
pip install -e '.[dev,ops]'
pytest v4/tests          # v4 engine, MCP server, ops layer
pytest tests             # v3 engine
```
The v4 engine lives in `v4/src/memory_os_v4`; the evaluation harness in `v4/bench`. Contributions: see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Legacy v3 document engine

`memory-os-ai-v3` still ships the previous FAISS document server (21 tools: document ingestion for PDF/DOCX/
images/audio, chat extraction, cross-project linking, cloud overflow). Existing v3 memories can be imported into v4
with the `memory_migrate_v3` tool. v3 documentation: [`legacy/`](legacy/) and the 3.x release notes.

## License

LGPL-3.0-or-later. Built and reviewed jointly by Claude (Anthropic) and Codex (OpenAI) with [@romainsantoli-web](https://github.com/romainsantoli-web).
