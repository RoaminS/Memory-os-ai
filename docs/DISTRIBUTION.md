# Distribution qualification

Applies from 4.1.2. Earlier versions of this page described a GitHub CI matrix and a PyPI workflow; neither runs
any more.

## What GitHub CI runs

`.github/workflows/ci.yml` runs two jobs on every push and pull request, with tools and actions pinned:

- `lint`: `ruff check src tests v4/src v4/tests v4/bench scripts --select E9,F`, then `ruff check src tests scripts`;
- `secrets-scan`: trufflehog with `--only-verified`. An empty result does not prove that no secret exists; on a push,
  only the pushed commits are scanned.

GitHub CI runs no test suite, builds nothing and publishes nothing. GitHub-hosted x86_64 runners are not a retained
platform cell; per-cell CI jobs are planned for 4.1.3.

## How a release is qualified

Qualification runs locally, per retained [platform cell](../README.md#platform-cells), on the exact commit and the exact
artifacts that are published:

- a sealed build of the wheel and the sdist from a clean Git checkout, `twine check --strict`, and the wheel rebuilt
  identical from the sdist;
- the v4 test suite and the installation, service, client and uninstall gates of each cell, with throwaway home
  directories: under `sandbox-exec` on macOS (deprecated by Apple; its limits are declared in the release notes), in a
  virtual machine for Ubuntu 24.04 arm64;
- since 4.1.2, gate P10-a: an MCP client (the official Python SDK) launches the registry command of `server.json`
  through `uvx`, offline, confined, against a throwaway service on macOS arm64 with Python 3.11;
- a P9 manifest that records every gate, its artifacts and their SHA-256 values. It is not signed.

## How a release is published

Publication is manual, by the maintainer, after the gates: the exact qualified wheel and sdist are uploaded to PyPI,
then the qualified commit is pushed without force, tagged, and a GitHub Release carries the wheel, the sdist, the
bootstrap with its release pin filled, the P9 manifest and `SHA256SUMS`. A change of a single byte after a gate
invalidates its evidence: the candidate is rebuilt and fully requalified.

## License provenance

The initial commit `a3a23e4` carried a GPLv3 `LICENSE`, while packaging and README
advertised LGPLv3. On 2026-10-09 the maintainer explicitly confirmed
`LGPL-3.0-or-later`, matching the announced package license. `LICENSE` now contains
the official GNU LGPLv3 text downloaded from
<https://www.gnu.org/licenses/lgpl-3.0.txt>. The former GPLv3 text is preserved in
`LICENSE.GPL-3.0`, the required companion. Package metadata includes both files
and the SPDX expression. Copyright/attribution remain unchanged.
