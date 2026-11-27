# Contributing to Memory OS AI

Use Python 3.11 or 3.12. Install from the repository root:

```bash
python -m pip install -e ".[dev]"
```

The production engine is `v4/src/memory_os_v4` (v4). The installed commands
`memory-os-ai` and `memory-os-v4` start it. Since 4.1.3 the legacy v3 package is no
longer shipped: its sources stay, not installed, under `legacy/v3/` until 4.2.0
(see [legacy/README.md](legacy/README.md)).

```bash
ruff check tests v4/src v4/tests v4/bench scripts --select E9,F
ruff check tests scripts
python -m pytest tests
python -m pytest v4/tests -m "not slow" --cov=memory_os_v4 --cov-report=term-missing
python -m build
python -m twine check dist/*
```

GitHub CI runs the two `ruff` commands above and a scan for verified secrets on every
push; since 4.1.3 a second workflow qualifies the two Ubuntu 24.04 cells (build, gates
P1 and P3–P7, container). The macOS gates run locally. Both are described in
[distribution qualification](docs/DISTRIBUTION.md). Semantic tests
require cached public model weights; deterministic tests use fake/hash embeddings.
Evaluation claims must follow the [versioned protocol](v4/bench/protocols/v1.md).

Open a pull request describing the concrete problem, resulting behavior and
validation. Preserve existing research ledgers and third-party datasets. Do not
commit secrets or user memory data.

Contributions are under `LGPL-3.0-or-later`, as confirmed by the maintainer.
[LICENSE](LICENSE) contains LGPLv3; [LICENSE.GPL-3.0](LICENSE.GPL-3.0) contains the
required GPLv3 companion. Preserve copyright and attribution notices.
