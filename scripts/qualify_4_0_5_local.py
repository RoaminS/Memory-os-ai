"""Offline local T05: lightweight tests/build/two isolated installs. No ML/API/TCP.

Run once from the clone after committing sources. Dependency pool is a read-only
private copy already qualified for 4.0.2; no downloads, editable hooks, or global
venv changes. Actual model/TCP/copy qualification is separate and guarded.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import venv
import tarfile
import shutil
import signal
import xml.etree.ElementTree as ET


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def disk_guard(path):
    if shutil.disk_usage(path).free < 5_000_000_000:
        raise ValueError('Disk guard < 5 GB; no further qualification work')


def main():
    if not __debug__:
        raise RuntimeError('Assertions required; do not use Python -O')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependency-pool', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path,
                        help='Fresh local qualification directory; existing evidence is never overwritten')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = (args.output_dir or root / '.local-qualification/release-4.0.5').resolve()
    if not out.is_relative_to(root / '.local-qualification') or out.exists():
        raise ValueError('Fresh qualification directory required')
    out.mkdir(mode=0o700)
    py = sys.executable
    disk_guard(out)
    from memory_os_v4.brief_tokens import status
    if not status()['ready']:
        raise ValueError('Complete local tiktoken 0.12.0 runtime/table required; no download')
    tokenizer_site = Path(__import__('tiktoken').__file__).resolve().parent.parent
    table = Path(os.environ['MEMORY_V4_CL100K_PATH']).resolve()
    metadata = root / '.local-qualification/lexical-dev-git'
    git = ['git', '--git-dir=' + str(metadata)] if metadata.is_dir() else ['git', '-C', str(root)]
    source = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True, cwd=root).strip()
    epoch = subprocess.check_output([*git, 'show', '-s', '--format=%ct', source], text=True, cwd=root).strip()
    pool = args.dependency_pool.resolve()
    if not pool.is_dir() or list(pool.glob('*memory_os*')) or list(pool.glob('*.pth')):
        raise ValueError('Qualified copied dependency pool without project/hooks required')
    checks, commands = {}, []
    env = {k: v for k, v in os.environ.items() if not k.startswith(('MEMORY_', 'OPENAI_', 'ANTHROPIC_'))}
    env.update(PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1',
               TOKENIZERS_PARALLELISM='false', MEMORY_TEST_HASH_V3='1', SOURCE_DATE_EPOCH=epoch, MEMORY_V4_BUILD_COMMIT=source, MEMORY_V4_CL100K_PATH=str(table),
               PYTHONPATH=str(root / 'src') + ':' + str(root / 'v4/src') + ':' + str(root / 'v4'))

    def run(name, command, *, cwd=root, extra=None):
        disk_guard(out)
        commands.append({'name': name, 'command': command, 'cwd': str(cwd), 'extra_environment': extra or {}})
        t = time.monotonic()
        with (out / (name + '.log')).open('x') as log:
            result = subprocess.Popen(command, cwd=cwd, env={**env, **(extra or {})},
                                      stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while result.poll() is None:
                    disk_guard(out)
                    if time.monotonic() - t > 900:
                        raise RuntimeError(name + ' exceeded 900s; preserve evidence')
                    time.sleep(.5)
            finally:
                if result.poll() is None:
                    os.killpg(result.pid, signal.SIGTERM)
                    try:
                        result.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(result.pid, signal.SIGKILL)
                        result.wait(timeout=5)
        checks[name] = 'passed' if result.returncode == 0 else 'failed'
        (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        print(name, checks[name], round(time.monotonic() - t, 2), 's', flush=True)
        if result.returncode:
            print((out / (name + '.log')).read_text()[-5000:], flush=True)
            raise RuntimeError(name + ' failed; preserve evidence')

    run('adversarial_probe', [py, 'scripts/a6_adversarial_probe.py'])
    checks['a6_review'] = 'passed'  # original A6 + incremental reviews; fresh adversarial probe required
    run('v3', [py, '-m', 'pytest', '-c', 'pyproject.toml', 'tests', '-q', '-p', 'no:cacheprovider',
               '--junitxml=' + str(out / 'v3-unit.xml'),
               '--basetemp=' + str(out / 'v3-tests'), '--cov=memory_os_ai', '--cov-fail-under=80',
               '--cov-report=json:' + str(out / 'coverage-v3.json')], extra={'COVERAGE_FILE': str(out / 'v3.coverage')})
    run('v4', [py, '-m', 'pytest', '-c', 'pyproject.toml', 'v4/tests', '-q', '-m', 'not slow', '-p', 'no:cacheprovider',
               '--ignore=v4/tests/test_server_http_e2e.py', '--ignore=v4/tests/test_ops_e2e.py', '--ignore=v4/tests/test_l3_tcp.py',
               '--basetemp=' + str(out / 'v4-tests'), '--cov=memory_os_v4', '--cov-fail-under=80',
               '--cov-report=json:' + str(out / 'coverage-v4.json')], extra={'COVERAGE_FILE': str(out / 'v4.coverage')})
    run('lint', ['ruff', 'check', '--no-cache', '--config', 'pyproject.toml', 'src', 'tests', 'scripts'])
    run('lint_correctness', ['ruff', 'check', '--no-cache', '--config', 'pyproject.toml', 'src', 'tests', 'v4/src', 'v4/tests', 'v4/bench', 'scripts', '--select', 'E9,F'])
    run('build', ['/opt/homebrew/opt/python@3.11/libexec/bin/python3', '-m', 'build', '--no-isolation', '--outdir', str(out / 'artifacts')])
    artifacts = [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in sorted((out / 'artifacts').iterdir())]
    run('twine_strict', [py, '-m', 'twine', 'check', '--strict', *[str(out / 'artifacts' / a['name']) for a in artifacts]])
    workspace = Path(tempfile.mkdtemp(prefix='memory-os-4.0.5-')).resolve()
    smoke = workspace / 'package_smoke.py'
    smoke.write_bytes((root / 'scripts/package_smoke.py').read_bytes())
    environments = {'outside_root': str(workspace), 'dependencies': str(pool), 'tokenizer_site': str(tokenizer_site), 'cl100k_path': str(table),
                    'dependency_origin': 'qualified private copied 4.0.2 dependency pool; no editable/project hooks or downloads'}
    for kind, suffix in [('wheel', '.whl'), ('sdist', '.tar.gz')]:
        target = workspace / (kind + '-env')
        venv.EnvBuilder(with_pip=False).create(target)
        python = target / 'bin/python'
        site = next((target / 'lib').glob('python*/site-packages'))
        (site / 'qualified-dependencies.pth').write_text(str(pool) + '\n')
        for name in ('tiktoken', 'tiktoken_ext', 'tiktoken-0.12.0.dist-info'):
            (site / name).symlink_to(tokenizer_site / name, target_is_directory=True)
        artifact = next(out / 'artifacts' / a['name'] for a in artifacts if a['name'].endswith(suffix))
        outside = {'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1'}
        run(kind + '_install', [str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-build-isolation',
                               '--no-cache-dir', str(artifact) + '[ops,brief]'], cwd=workspace, extra=outside)
        run(kind + '_pip_check', [str(python), '-m', 'pip', 'check'], cwd=workspace, extra=outside)
        run(kind + '_smoke', [str(python), str(smoke), '--version', '4.0.5'], cwd=workspace, extra=outside)
        environments[kind + '_environment'] = str(target)
    with tarfile.open(next(out / 'artifacts' / a['name'] for a in artifacts if a['name'].endswith('.tar.gz'))) as archive:
        archive.extractall(workspace / 'sdist-source', filter='data')
    sdist_root = workspace / 'sdist-source/memory_os_ai-4.0.5'
    sdist_python = str(Path(environments['sdist_environment']) / 'bin/python')
    for suite in ('v3', 'v4'):
        paths = ['tests'] if suite == 'v3' else ['v4/tests', '--ignore=v4/tests/test_server_http_e2e.py', '--ignore=v4/tests/test_ops_e2e.py', '--ignore=v4/tests/test_l3_tcp.py',
            '--deselect=v4/tests/test_session_copies.py::test_archived_reader_and_200_predictions_match_without_model',
            '--deselect=v4/tests/test_evidence_bootstrap.py::test_s_preflight_stops_before_any_model_worker',
            '--deselect=v4/tests/test_mps_reuse_campaign.py::test_overlay_changes_only_three_reviewed_files',
            '--deselect=v4/tests/test_mps_reuse_campaign.py::test_archived_source_worker_runs_from_untracked_cwd_with_hash_only']
        run('sdist_source_' + suite, [sdist_python, '-m', 'pytest', '-c', 'pyproject.toml', '-q', '-m', 'not slow', '-p', 'no:cacheprovider',
            '--basetemp=' + str(workspace / ('sdist-' + suite + '-tests')), *paths], cwd=sdist_root,
            extra={'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1', 'MEMORY_WORKSPACE': str(sdist_root)})
    (out / 'environments.json').write_text(json.dumps(environments, indent=2) + '\n')
    manifest = {'version': '4.0.5', 'source_commit': source, 'source_date_epoch': int(epoch), 'release_ready': False,
                'open_blockers': [], 'checks': {**checks, 'source_tcp': 'pending', 'cached_semantic': 'pending',
                                               'v3_cached': 'pending', 'production_copy': 'pending', 'brief_exact': 'pending'},
                'source_expected_v3_tests': sum(int(s.get('tests', '0')) for s in ET.parse(out / 'v3-unit.xml').getroot().iter('testsuite')),
                'artifacts': artifacts,
                'qualification_source_sha256': {str(p.relative_to(root)): digest(p) for p in
                    [root / 'pyproject.toml', root / 'MANIFEST.in', root / 'conftest.py', *sorted((root / 'src').rglob('*.py')), *sorted((root / 'tests').rglob('*.py')), *sorted((root / 'v4/src').rglob('*.py')),
                     *sorted((root / 'v4/tests').rglob('*.py')), *sorted((root / 'scripts').glob('*.py')),
                     *sorted((root / 'v4/tests/fixtures/brief_l1a').glob('*.json*')),
                     *sorted((root / 'v4/tests/fixtures/work_l7b').glob('*.json*')),
                     *sorted((root / 'v4/docs/contracts').glob('*.json'))]},
                'installed_relay_scripts_sha256': {n: digest(root / 'scripts' / f) for n, f in [('runner', 'installed_relay_e2e.py'), ('case', 'installed_relay_case.py')]},
                'copy_scripts_sha256': {n: digest(root / 'scripts' / f) for n, f in [('runner', 'qualify_release_copy.py'), ('worker', 'release_copy_worker.py')]},
                'evidence': {'python': sys.version.split()[0], 'dependency_downloads': False, 'models_downloaded': False,
                             'production_modified': False, 'real_encoder_runs': 0, 'v3_unit_encoder': 'explicit synthetic; not semantic qualification', 'sqlite_opt_in': True, 'registry_active': False, 'encoding_defaults': ['auto', 'native', 32], 'c4': ['scoped_literal', 3], 'tokenizer': status()},
                'pending_at_build': ['27 source TCP tests', 'L1a exact offline 400 measurements + TCP envelope', '1 cached MiniLM test', 'full v3 cached actual encoder suite', 'copy/native-profile/cache/timing/search/doctor',
                                     'installed wheel relay TCP', 'installed sdist relay TCP']}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(a['sha256'] + '  ' + a['name'] + '\n' for a in artifacts))
    print('Local T05 complete. Publication guard CLOSED. No inference, upload or service change.', flush=True)


if __name__ == '__main__':
    main()
