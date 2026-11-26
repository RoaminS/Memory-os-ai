# Legacy — not shipped, reference only

Nothing in this directory is part of the `memory-os-ai` package: it is excluded from the sdist and the wheel and
is not covered by the release gates. The supported product is Memory OS v4 (`v4/src/memory_os_v4/`).

## `v3/` — the v3 package (removed from the wheel in 4.1.3)

Until 4.1.2 the wheel still shipped the unqualified v3 package `memory_os_ai` and its `memory-os-ai-v3` command.
Since 4.1.3 they are no longer installed. The sources and their tests are kept here, unchanged, until 4.2.0.

- `v3/memory_os_ai/`: the former `src/memory_os_ai/` package.
- `v3/tests/`: the tests that import it (formerly under `tests/`).

`memory_migrate_v3` (reading a v3 store into v4) does **not** depend on this package and stays supported.
`quickstart` and `uninstall` still recognise old v3 client entries, by string only, so that they can be found and
removed.

## v1 files — the original Mistral-7B prototype

The original Memory OS AI implementation, which used a local **Mistral-7B GGUF** model via `llama-cpp-python`.

| File | Description |
|------|-------------|
| `memory_os_ai.py` | CLI app — FAISS + SentenceTransformers + Mistral-7B (4 GB GGUF) |
| `memory_os_ai_gui.py` | PySide2 desktop GUI wrapping `memory_os_ai.py` |
| `convert_to_sentence_models.py` | Utility to convert embeddings to SentenceTransformer format |

Their dependencies (`llama-cpp-python`, `PySide2`, `textract`) are not installed by any extra.
