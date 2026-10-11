"""S4 scratch <=16 MiB/case; remove only after all test phases pass."""
from pathlib import Path
import shutil

import pytest

CAP = 16 * 1024 ** 2


@pytest.fixture(autouse=True)
def bounded_s4_scratch(tmp_path, request):
    request.node.s4_scratch = tmp_path
    yield
    size = sum(p.stat().st_size for p in tmp_path.rglob('*') if p.is_file() and not p.is_symlink())
    request.node.user_properties.append(('s4_scratch_bytes', str(size)))
    if size > CAP:
        pytest.fail(f'S4 scratch cap exceeded: {size}>{CAP}; evidence kept')


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if 's4' not in Path(str(item.fspath)).parts:
        return
    setattr(item, 's4_' + report.when, report)
    if report.when == 'teardown' and report.passed and getattr(item, 's4_call', None) and item.s4_call.passed:
        path = getattr(item, 's4_scratch', None)
        if path is not None:
            shutil.rmtree(path)  # Owned pytest temp only; failed/skipped cases are preserved.
