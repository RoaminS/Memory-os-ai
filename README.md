<!-- mcp-name: io.github.romainsantoli-web/memory-os-ai -->
# Memory OS AI

**4.0.2 ALPHA — published on PyPI on 2026-10-10.**

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

Before `setup`, set `MEMORY_V4_BUILD_COMMIT` to the **40-character source commit from the qualified release
dossier** (`releases/4.0.2/QUALIFICATION.md`). An installed wheel has no Git checkout:
without this operator-provided identity, `/ready` refuses the build and `doctor` reports FAIL.
Download/setup is not proof of the operator's local configuration or data migration.

```bash
pip install 'memory-os-ai[ops]'
memory-os-admin setup --write-launchd ~/memory-os-launchd   # creates your private access token + prints client configs
cp ~/memory-os-launchd/*.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.memory-os-v4.plist
memory-os-admin doctor                                       # checks the server, the security and every AI client
```

`doctor` ends with a line such as `17 checks: 16 pass, 1 warn, 0 fail`. Full guide: [`v4/docs/OPERATIONS.md`](v4/docs/OPERATIONS.md).

## Works with your assistants and editors

All clients connect through `memory-os-relay`, which reads your private token file itself — **no secret is ever
written into a client configuration**. `memory-os-admin setup` prints a configuration snippet for each one, and
`memory-os-admin doctor` checks their configuration syntax; it does not prove a live handshake for every client.

| Client | Configuration | Client evidence / candidate configuration |
|---|---|---|
| Claude Code | `claude mcp add --scope user …` (`~/.claude.json`; project overrides possible) | previous relay run verified; generated configuration only |
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
| **BEAM 100K** annotated-turn Recall@10, 400 questions completed (355 with evidence), Granite, no LLM | C4 0.462 vs BM25 0.445; paired Δ +0.017, IC95 **[-0.004, +0.040] — preregistered criterion not met**. MRR 0.444 vs 0.436; Δ +0.008 [+0.003, +0.015] | 20 conversation clusters, frozen plan `beam-c4-retrieval-v2` (sha256 `5878e5b5…`), 0 failures. This is retrieval only, not answer accuracy or a leaderboard score. See [`BEAM_RESULT_V2.md`](v4/docs/BEAM_RESULT_V2.md) |
| **LongMemEval-S** evidence Recall@10, 500 questions executed (479 with evidence scored; 21 abstention cases excluded from retrieval metrics), ~50 sessions of history each, no LLM, MiniLM — candidate C4 (scoped literal BM25 + 3 protected lexical results + hybrid fusion) | **0.754 [0.721, 0.785] vs BM25 0.712 [0.675, 0.748]; paired Δ +0.042 [+0.022, +0.061]** (466 evidence clusters); MRR 0.626 vs 0.614, Δ +0.012 [+0.008, +0.0164]; 0 failures | Frozen preregistered plan `lexical-c4-s-v3` (sha256 38c1877e…); CI conditional on the fixed distractor corpus; questions overlap the oracle variant used for development, so this is **not** an independent confirmation and **not** an official leaderboard score |
| Development history, LongMemEval oracle (exploratory) | 4.0.1 defaults ≈ BM25 (0.802 vs 0.812); root cause found (filtering after candidate truncation); corrected lexical path = exact BM25 parity; C4 +0.026 recall on dev | `v4/docs/LEXICAL_DEV_V1.md`, failures kept |
| LoCoMo evidence Recall@10 (exploratory) | 0.538 production defaults → 0.815 with offline LLM enrichment and reranking | `v4/RESULTS.md`; offline enrichment is not the production path |
| 4.0.1 release qualification | 478 v3 + 795 v4 tests, two adversarial review rounds, installed-artifact relay proof (round-trip, failure, reconnection, no replay) for wheel and sdist | `releases/4.0.1/QUALIFICATION.md` |

We publish retrieval recall, answer quality and agent-task success **separately**, with sample sizes and
confidence intervals, and always next to simple baselines (BM25, dense-only, files, full context). BEAM had no
exact normalized question/turn overlap with LongMemEval and its preregistered recall criterion failed. The next milestones are the
indexing-cost target, a new held-out temporal test, answer accuracy with a fixed reader model and independent
reproduction — before any leaderboard claim. On BEAM, indexing took 678 s per conversation at the median while
warm search stayed at 41 ms p50; the encoder consumed 99.86% of measured ingestion time.

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
                                     └─ store: JSONL by default; transactional SQLite S2/S3 opt-in (qualified backups not yet supported)
 memory-os-admin: setup · doctor · backup · restore-drill · health · hooks
```

Details: [`v4/docs/ARCHITECTURE.md`](v4/docs/ARCHITECTURE.md), [`v4/docs/HTTP_CLIENT_CONTRACT.md`](v4/docs/HTTP_CLIENT_CONTRACT.md),
[`v4/docs/ADR-001-SQLITE.md`](v4/docs/ADR-001-SQLITE.md), [`v4/docs/READINESS.md`](v4/docs/READINESS.md).

## Honest limits of this ALPHA

- One user, one machine (macOS qualified). Projects organise memory; they are not a security boundary between
  people. No remote access: assistants that run only in the cloud cannot reach a local server yet.
- The JSONL store and request receipts are not atomic across files; transactional SQLite S2 and offline reversible migration S3 ship opt-in,
  with failure-injection tests and embedding caching. SQLite qualified backups/restore drills (S4) remain to do;
  the current ops backup command refuses SQLite.
- C4 is the 4.0.2 default (`MEMORY_V4_BM25_PROFILE=scoped_literal`,
  `MEMORY_V4_LEXICAL_GUARD_K=3`). LongMemEval-S passed its gate but was not independent; the question-disjoint
  BEAM run failed its recall gate. C4 therefore has no general superiority claim. Rollback uses
  `MEMORY_V4_BM25_PROFILE=legacy` and `MEMORY_V4_LEXICAL_GUARD_K=0`.
- Neither run measures official answer accuracy, multilingual quality, agent-task success or leaderboard rank.

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
