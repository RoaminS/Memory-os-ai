# Distribution qualification

Applies from 4.1.3; 4.1.2 had only the `ci.yml` workflow below, and the Linux cells workflow is new in 4.1.3.
Earlier versions of this page described a GitHub CI matrix and a PyPI workflow; neither runs any more.

## What GitHub CI runs

`.github/workflows/ci.yml` runs two jobs on every push and pull request, with tools and actions pinned:

- `lint`: `ruff check tests v4/src v4/tests v4/bench scripts --select E9,F`, then `ruff check tests scripts`;
- `secrets-scan`: trufflehog with `--only-verified`. An empty result does not prove that no secret exists; on a push,
  only the pushed commits are scanned.

`ci.yml` runs no test suite, builds nothing and publishes nothing.

Since 4.1.3, `.github/workflows/linux-cells.yml` qualifies the two Ubuntu 24.04 cells on GitHub-hosted runners
(maintainer decision D6), with actions pinned by commit SHA and tools by exact version:

- `build`: the wheel and the sdist of the pushed commit, with their `SHA256SUMS`, kept as a workflow artifact;
- `resolve-lock-x86_64`: the frozen 4.1.1 lock recipe replayed for x86_64, its outputs kept as a workflow artifact;
  the embedded lock was generated offline from those of run 45879240439 (`scripts/generate_cell_locks_411.py`);
- `qualify-arm64` (`ubuntu-24.04-arm`) and `qualify-x86_64` (`ubuntu-24.04`, on manual request): gates P1 and P3–P7
  of the cell (`tools/qualify413/linux_cell.py`); each report records the SHA-256 values of the artifacts it tested,
  for the P9 manifest;
- `container-arm64`: the minimal image built from that wheel and tried (readiness, non-root user, token required,
  foreign `Host` refused, persistence after a restart, exit code 0). It is never pushed to any registry.

This workflow publishes nothing either.

## How a release is qualified

Qualification runs per retained [platform cell](../README.md#platform-cells), on the exact commit and the exact
artifacts that are published: locally for the macOS cells, on GitHub Actions for the two Ubuntu cells since 4.1.3:

- a sealed build of the wheel and the sdist from a clean Git checkout, `twine check --strict`, and the wheel rebuilt
  identical from the sdist;
- the v4 test suite and the installation, service, client and uninstall gates of each cell, with throwaway home
  directories: under `sandbox-exec` on macOS (deprecated by Apple; its limits are declared in the release notes); on
  disposable GitHub-hosted runners for the Ubuntu cells since 4.1.3 (in a virtual machine for Ubuntu 24.04 arm64 up to
  4.1.2);
- since 4.1.2, gate P10-a: an MCP client (the official Python SDK) launches the registry command of `server.json`,
  `uvx --from memory-os-ai[ops,brief]==4.1.3 memory-os-relay memory-os-ai` (its runtime arguments plus the
  package identifier the consumer appends, here for 4.1.3), offline, confined, against
  a throwaway service on macOS arm64 with Python 3.11; since 4.1.3 it also requires the compact brief through the
  relay, without `MEMORY_V4_CL100K_PATH`;
- a P9 manifest that records the per-cell gates, their artifacts and their SHA-256 values. It is not signed. Gates
  P10-a and P10-c are not in it: since 4.1.2 they are recorded in a separate proof complement of the Release, with
  its own checksum file.

## How a release is published

Publication is manual, by the maintainer, after the gates: the exact qualified wheel and sdist are uploaded to PyPI,
then the qualified commit is pushed without force, tagged, and a GitHub Release carries the release assets. A change
of a single byte after a gate invalidates its evidence: the candidate is rebuilt and fully requalified.

The release assets of version `X.Y.Z` (since 4.1.3; up to 4.1.2 the same list without `install.sh`):

| Asset | What it is |
|---|---|
| `memory_os_ai-X.Y.Z-py3-none-any.whl`, `memory_os_ai-X.Y.Z.tar.gz` | the qualified wheel and sdist, same bytes as PyPI |
| `memory_os_bootstrap.py` | the bootstrap with its release pin filled (state `release`, version `X.Y.Z`, the wheel's name, SHA-256 and size) |
| `memory-os-ai-X.Y.Z.p9-manifest.json` | the unsigned P9 manifest, naming the wheel's SHA-256 |
| `install.sh` | the one-command install, pinned to its release by `RELEASE_TAG="vX.Y.Z"` |
| `SHA256SUMS` | one `<sha256>  <name>` line per asset above, sorted by name, never a line for itself |

`install.sh` downloads `memory_os_bootstrap.py` and `SHA256SUMS` from the release its tag names and requires exactly
one `SHA256SUMS` line for the bootstrap. `install.sh` itself is in `SHA256SUMS`, which the manual route can check; the
one command cannot check the script it runs.

`scripts/release_assets_413.py <directory>` writes that `SHA256SUMS` at publication, once the other five assets are in
one directory. It refuses, writing nothing, when an asset is missing, duplicated or extra (the proof complement has
its own checksum file), when the wheel and the sdist do not name the same version, when `install.sh` does not
carry exactly `RELEASE_TAG="v<that version>"`, when the bootstrap pin is not the release pin of that wheel, when the
P9 manifest does not name the wheel's SHA-256, or when a `SHA256SUMS` is already there. The freeze test
`v4/tests/test_release_assets_413.py` requires `RELEASE_TAG` to equal `v` plus the version of both `pyproject.toml`
files, and the one-command URLs of the README and of the release notes to use that tag.

Since 4.1.2, gate P10-c follows the PyPI publication and comes before any entry in the MCP registry: the P10-a recipe
is replayed with the wheel resolved from PyPI (fresh cache, dependencies constrained by the cell lock), and the wheel
`uv` downloaded must have the SHA-256 of the qualified one. Its report is a complement linked to the P9 manifest by
its digest; the P9 manifest is never rewritten. A P10-c failure blocks the registry entry, not the published
version.

## License provenance

The initial commit `a3a23e4` carried a GPLv3 `LICENSE`, while packaging and README
advertised LGPLv3. On 2026-10-09 the maintainer explicitly confirmed
`LGPL-3.0-or-later`, matching the announced package license. `LICENSE` now contains
the official GNU LGPLv3 text downloaded from
<https://www.gnu.org/licenses/lgpl-3.0.txt>. The former GPLv3 text is preserved in
`LICENSE.GPL-3.0`, the required companion. Package metadata includes both files
and the SPDX expression. Copyright/attribution remain unchanged.
