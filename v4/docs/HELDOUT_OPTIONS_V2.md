# Held-out answer campaign: refusal, alternatives and handoff — 2026-10-10

## Recorded diagnosis, not a protocol relaxation

Claude's evidence: `.local-qualification/heldout-answer-beam-500k-v1/{audit.json,REFUSED.json}`;
35 source conversations, 20 excluded for overlap with observed BEAM 100K, 15 remaining
independent components, zero inference calls. Keep these files and the v1 protocol intact.
The eligibility criterion remains **>=30 independent groups**; parsing cannot rescue this corpus.

A bounded read of the first JSONL row established the immediate parser failure:
source conversation `1`, `knowledge_update`, probe index `0`, 796 chat IDs (0–795).
The probe has **no `source_chat_ids`**; it has `conversation_references`, a list of
two strings. This is incompatible with the required structured evidence schema.
It does not demonstrate a reference to a nonexistent chat ID or a wrong answer.
The dataset card types `probing_questions` as an opaque string; it does not promise
a uniform nested annotation schema. Natural-language references are not safely
convertible into IDs without a separately validated mapping.

The loader now distinguishes missing fields from dangling IDs and reports source
conversation/category/probe without printing questions, answers or reference text.
The independence preflight precedes parsing gold, including gold in excluded rows.
Acceptance rules, question selection, reader/judge and numerical claim are unchanged.
`beam_annotation_audit` is a separate bounded read-only diagnostic, not an evaluation:
it counts missing/dangling/invalid evidence, hashes questions and records alternate
field types. It never repairs evidence, drops questions or creates a new campaign.
Its full 35-row scan is left to Claude: sandbox swap measurement is unavailable.

Source JSONL SHA-256:
`3f18f51f9a02e3e2104c77826b0915852f48be9517f33c09f297872eef8871f6`.
Parquet SHA-256:
`af05921c979355038e1761b7cde3d2dd713200dd3071b278de0200f6c7f30122`.

## Corpus choices: rows are not independent groups

Metadata/cards consulted on 2026-10-10; no candidate dataset downloaded by Codex.
Sizes in MB/GB are decimal and distinguish transfer from decoded storage.
No external alternative below is **already certified** to contain >=30 fresh roots.
All require a provenance/overlap/root audit before inference, including local older
benchmarks and LoCoMo-derived material, not just BEAM and LongMemEval.

| Corpus | Size and licence reported upstream | Independence and task suitability | Decision / evaluation cost |
|---|---|---|---|
| BEAM 500K | Download 33.96 MB, JSONL ~86 MB; CC-BY-SA-4.0 | Local audit: 15 fresh components, annotation heterogeneity | Refused; spend 0 calls |
| BEAM 1M | 35 rows, 172.6 MB decoded; individual transfer size unverified (all BEAM partitions total 105.5 MB); CC-BY-SA-4.0 | Card does **not** establish whether seeds differ from observed 100K/500K. 35 rows is only an upper bound on roots | No blind download; likely expensive and too little independence headroom. If >=30 fresh roots actually survive: 300–350 q, 1200–1400 calls |
| BEAM 10M | 10 root conversations; 343.8 MB transfer, 975.4 MB decoded; CC-BY-SA-4.0 | Ten plans per root do not supply 100 independent groups | <30 roots; reject, 0 calls |
| LoCoMo current | 10 conversations, `locomo10.json` ~2.68 MB; CC-BY-NC-4.0 | <30; local enriched LoCoMo material was previously used. Earlier release had 50 conversations, but availability, annotation quality and >=30 **unobserved** roots remain unverified | Current release rejected. Historical 50 is conditional research, not a ready held-out corpus; if eligible, 400 q / 1600 calls |
| LongMemEval-M | 500 instances, original file ~2.75 GB; MIT | Same underlying question family as observed S/oracle; longer distractors do not make questions new | Reject as fresh confirmation, 0 calls |
| MSC original | Official `msc_v0.1.tar.gz`; transfer size and dataset-specific licence not verified here | Many dialogue examples, not certified independent users; no ready QA gold. Shared participants/personas/sessions must be grouped; LoCoMo can reuse MSC personas | Possible construction substrate, not executable as answer benchmark. Extra annotation/generation budget required |
| MSC-Self-Instruct | 500 rows, 8.56 MB; Apache-2.0 | Personalized conversation openers, generated during MemGPT evaluation. Rows may share prior dialogues/personas; not 500 certified roots. New QA/reference/rubric construction needed if measuring answer accuracy | Cheap audit candidate, not first confirmation choice. 400 q: 1600 evaluation calls **plus** annotation generation/validation if automated |
| PersonaMem-v1 32k | Questions 1.31 MB + contexts 5.61 MB (~6.92 MB); MIT | Existing MCQ gold; merge persona IDs, shared contexts and source personas. Number of eligible personas is not established by card; 1000 preferences in v2 does not mean 1000 users | Small alternative audit. If >=30 fresh roots: 400 q / 1600 calls under unchanged reader+judge path; label scoring could halve calls only with a new protocol |
| MemoryAgentBench LRU | 110 context rows; one parquet ~49.3 MB, decoded ~99.4 MB; MIT declared by card | Existing questions/answers. Merge by original book/document/scenario, repeated context and shared excerpts; verify upstream source licences. AR reuses LongMemEval, and AR/TTL/CR have only 22/6/8 rows | **Recommended external audit**. 400 q / 1600 calls if >=30 fresh roots and >=400 valid questions. Different task scope from personalized chat |
| LongMemEval-V2 | Repository ~7.12 GB; Apache-2.0 | New multimodal/agent trajectory task; root count/overlap and adapter not established | Too large and different for this immediate campaign; 0 calls |
| Independently constructed synthetic corpus | Proposed <=20 MB; choose original-text licence with Romain (e.g. CC-BY-4.0) | 100 independent randomly seeded worlds ×4 q =400 q; ground truth from deterministic event/state generator, no reused benchmark seeds | Guaranteed construction capacity, subject to audit; 1600 evaluation calls, zero generation calls in deterministic variant. Internal claim only |

Official sources (metadata rather than dataset question inspection):

- [BEAM card](https://huggingface.co/datasets/Mohammadta/BEAM/blob/main/README.md),
  [BEAM 10M metadata commit](https://huggingface.co/datasets/Mohammadta/BEAM-10M/commit/b5c972d2132e4b291049c257ee42cb82929be9c0).
- [LoCoMo release and initial 50](https://github.com/snap-research/locomo),
  [licence](https://github.com/snap-research/locomo/blob/main/LICENSE.txt),
  [file metadata](https://github.com/snap-research/locomo/blob/main/data/locomo10.json).
- [LongMemEval format](https://github.com/xiaowu0162/LongMemEval),
  [M file metadata](https://huggingface.co/datasets/xiaowu0162/longmemeval/blob/c794edff2b491c7be15e04db36c82c696ee3d8cb/longmemeval_m),
  [V2 file inventory](https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/tree/main).
- [MSC project](https://parl.ai/projects/msc/),
  [official archive builder](https://github.com/facebookresearch/ParlAI/blob/main/parlai/tasks/msc/build.py),
  [MSC-Self-Instruct task card](https://huggingface.co/datasets/MemGPT/MSC-Self-Instruct),
  [file sizes/licence](https://huggingface.co/datasets/MemGPT/MSC-Self-Instruct/tree/main).
- [PersonaMem schema](https://huggingface.co/datasets/bowen-upenn/PersonaMem-v1/blob/main/README.md),
  [file inventory](https://huggingface.co/datasets/bowen-upenn/PersonaMem-v1/tree/main).
- [MemoryAgentBench split counts](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench/blob/main/README.md?code=true),
  [LRU artifact](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench/blob/main/data/Long_Range_Understanding-00000-of-00001.parquet),
  [authors' methodology](https://github.com/HUST-AI-HYZ/MemoryAgentBench).

## Recommendation and budget (design only)

1. Request Romain's **download-and-audit-only GO** for the single MemoryAgentBench
   LRU parquet, cap 55 MB, immutable upstream revision and SHA-256 recorded from
   official metadata before transfer. Current artifact SHA-256:
   `5ab175461954db67770d4a4cb69e569b513ebb96aceb9ee79b57f67488bcd539`.
   Convert sequentially with existing Homebrew Python/pyarrow; do not install
   the upstream inference framework. Refuse if any required source/root identity
   is untraceable, if original licence disallows intended use, or if <30 fresh
   components survive. Prefer >=80 roots; >=30 guarantees an estimable clustered
   interval, **not sufficient power or a positive result**.
2. If refused, propose PersonaMem 32k audit under a **separate** <=8 MB download GO.
   Merge shared histories across personas; slice at `end_index_in_shared_context`
   before ingestion to prevent future-state leakage. New format/metric protocol
   must be frozen before retrieval; do not reclassify MCQ as open-ended official scores.
3. Synthetic worlds are a complementary temporal/identity validation and the
   deterministic fallback for >=30 roots, not an external leaderboard substitute.
   Generate facts/events/identities, corrections and oracle state before dialogue
   rendering; use separate development worlds, never query/answer-conditioned
   extraction. Seal fresh test seeds after code/protocol freeze. Balance literal
   and paraphrased evidence, changes, cross-session joins and legitimate abstention
   without optimizing wording for either arm. Human-check a preselected sample
   before inference; corrections/exclusions follow a frozen rule for both arms.
   Bootstrap by independent world (merge any shared generated facts/scenarios);
   inference is conditional on the fixed generator/template bank. Natural-data
   generalization and pretraining contamination are not established.
   Optional question-blind dialogue paraphrasing + independent validation costs
   another 100+100 Codex calls for 100 worlds: **1800 total**, plus at most 200
   explicitly capped failed attempts. Do not let the judge invent ground truth;
   retain deterministic oracle and human checks against annotation/model bias.
   This optional generation requires explicit approval of its quota budget.

Common budget estimate, derived solely from the observed ten-question pilot:
400 q ×4 calls = **1600** (800 Luna low readers +800 Astra high judges),
~**14.49 million tokens**, ~**3.13 h active CLI time**, plus sequential encoding and
quota pauses. 300–500 q gives 1200–2000 calls, ~10.87–18.12 M tokens,
~2.35–3.92 h CLI time. These are extrapolations, not quota or latency guarantees;
longer answers/rubrics can exceed them. Unknown usage counts conservatively;
no retries or resume except explicit validated quota-stop resume, no paid credits
purchase or API fallback. Zero API spend is not zero ChatGPT quota consumption.
[Official Codex usage guidance](https://learn.chatgpt.com/docs/pricing) explains why
model, context and reasoning change consumption; tokens do not identify quota %.

Freeze the new corpus-specific protocol only **after** blind structural audit and
before any scored retrieval/answer: exact corpus/version/hashes, source grouping,
hash-based balanced selection, question count, all valid-gold rules, production
encoding profile and baseline inputs, reader/judge identities, prompts and caps.
Keep the intended paired binary-accuracy claim: lower CI95 bound for C4−BM25 >0,
10,000 whole-component bootstrap replicates, >=30 roots, complete denominator,
unavailable CI => no claim. Report every category and failure, not only wins.
A different corpus does not inherit BEAM's ten-category selection constraints.
GPT-6-astra high is not the published GPT-4o snapshot; a win means only a win
against reproduced BM25 under this protocol. Full-context is absent under the
chosen Codex-only limits; no comparison to competitors' published conditions and
no official top-1 claim. Do not launch an old BEAM plan with substituted data.

## Relay fix and commands for Claude (no inference)

The packaged relay now creates `Server(init.serverInfo.name,
version=init.serverInfo.version, instructions=init.instructions)`.
This preserves upstream identity instead of publishing SDK 1.26.0. Three local
tests cover release/prerelease/arbitrary build identities through actual SDK
initialization options. The real relay→HTTP test also asserts the dynamically
imported package version during handshake. It is a **pending 4.0.4 fix**; no version
bump, artifacts, publication, pinned-relay edits or production change here.
Claude should apply the same one-line change in the next pinned-relay candidate,
qualify it, and deploy only with the applicable GO.

From the implementation clone after bundle import:

```bash
PYTHONPATH=v4/src:v4 PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python -m pytest -q -m 'not slow' v4/tests -p no:cacheprovider --basetemp=.local-qualification/heldout-options-claude-tests
ruff check --no-cache --config pyproject.toml src tests scripts
ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
PYTHONPATH=v4 /Users/romain/Memory-os-ai/.venv/bin/python -m bench.e2e.beam_annotation_audit --data .local-qualification/datasets/beam-500k-v1/beam_500k.jsonl --expected-sha256 3f18f51f9a02e3e2104c77826b0915852f48be9517f33c09f297872eef8871f6 --output .local-qualification/beam-500k-annotation-diagnostic-v2
```

The last command enforces nice>=15, swap<=8.5 GB, free disk>=5 GB at each row,
100 rows/16 MB per row/500,000 nodes budgets and a final dataset fingerprint.
No network or LLM. Use a fresh output if diagnostic evidence already exists;
never erase the original refusal. Source tests use disposable hash stores only.

Before a future campaign, account for source attestation: changing `ops/relay.py`
changes the previously qualified whole-`v4/src` fingerprint. The existing 4.0.3
copy proof cannot attest this new tree. Use an immutable already qualified 4.0.3
source/profile or requalify the future 4.0.4 candidate on a copy; never weaken
`engine_matches_copy`. No campaign command is supplied until corpus eligibility
and its new frozen protocol exist. Download commands are intentionally pending GO.
