"""H2: lexical versus semantic retrieval parity on BEAM 100K (frozen manifest h2-lexical-parity-v1).

Contract: docs/LEXICAL_PROFILE_4_1_0.md section 10. Retrospective measure, no tuning, no independence
claim. ``prepare`` only reads, hashes and counts; ``retrieve`` runs each arm in its own process and scores
ONLY after both arms completed. Loader, capture, budget packing, scoring and source attestation are those
of the BEAM v2 campaign; the two arms are the lexical and the default (semantic) engine profile on fresh
sqlite_s1 stores instead of memory versus BM25.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.abc
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import time

from .beam import DATA_SHA, REVISION, conversations
from .client import offline_env, project_for
from .data import sha256
from .guard import Limits, ResourceStop, ensure_priority, sample
from .leaderboard import AddSearchAdapter
from .pipeline import extract, pack
from .run import code_record, kill_group, model_record, verify_source, write_json
from .stats import score

RUN_ID = 'h2-lexical-parity-v1'
VERSION = 'beam-h2-lexical-parity-v1'
ARMS = ('lexical', 'semantic')  # execution order; each arm is one sequential worker process
SEED = 20261010
REPLICATES = 2000
CI_INDEXES = (49, 1949)  # percentile 95 % of 2,000 sorted draws, as bench.e2e.beam_campaign.summarize
TOP_K = 10
CONTEXT_BYTES = 12000
CHUNK_CHARS = 1024
INGEST_BYTES = 16_000_000
POINT_MARGIN = Fraction(2, 100)  # lexical Recall@10 >= semantic Recall@10 - 0.02
CI_MARGIN = Fraction(3, 100)  # lower bound of the paired CI95 >= -0.03
NAMESPACE = 'beam-c4-v1-capture'  # same capture namespace as v2: identical quotes, ids and tie-breaks
GRANITE = 'ibm-granite/granite-embedding-97m-multilingual-r2'
GRANITE_REVISION = '835ad14087e140460703cf0fae09f97d469d65c2'
GRANITE_CACHE = Path('/Users/romain/.cache/huggingface/hub') / (
    'models--ibm-granite--granite-embedding-97m-multilingual-r2')
ML_MODULES = ('torch', 'sentence_transformers', 'faiss', 'transformers')
EXTRA_FILES = ('v4/bench/evaluation_manifest.py', 'v4/bench/__init__.py', 'scripts/run_beam_h2.sh')
ARCHIVED = ('v4/src', 'v4/bench/e2e', *EXTRA_FILES)
STORE_PLACEHOLDER = '<fresh private sqlite_s1 store per arm and per conversation>'
ENCODER_CALL_CEILING = 100_000
BUDGETS = {'retrieval_seconds': 28800, 'conversation_seconds': 3600, 'swap_bytes': 8_500_000_000,
           'free_bytes': 5_000_000_000, 'nice': 15, 'concurrency': 1, 'downloads': 0, 'llm_calls': 0,
           'encoder_calls': ENCODER_CALL_CEILING}
PRIOR_EXPOSURE = [
    'BEAM 100K was already observed before the H2 threshold was set: C4 Recall@10 0.462 versus same-arm BM25 '
    '0.445 (v4/docs/BEAM_RESULT_V2.md, plan beam-c4-retrieval-v2).',
    'The arms move from private JSONL stores behind MCP stdio (v2 campaign) to fresh sqlite_s1 stores driven '
    'in process; the measure holds on this frozen corpus only and is not an independent test.',
    'A rerun of the same C4 arm on MPS over BEAM 100K was observed as well (mps-beam-reuse-v1, Recall@10 '
    '0.4625, v4/bench/results/mps_beam_reuse_v1_summary.json).',
]
ARM_ENV = {
    'lexical': {},
    # Default device "auto": no silent CPU fallback of single operators once MPS is selected, and the
    # allocator watermarks of the existing production-profile harness (bench.e2e.client).
    'semantic': {'PYTORCH_ENABLE_MPS_FALLBACK': '0', 'PYTORCH_MPS_HIGH_WATERMARK_RATIO': '0.4',
                 'PYTORCH_MPS_LOW_WATERMARK_RATIO': '0.3'},
}


@dataclass(frozen=True)
class Spec:
    """Corpus and model identities frozen by section 10; tests substitute a synthetic one."""
    dataset_sha256: str = DATA_SHA
    revision: str = REVISION
    conversations: int = 20
    messages: int = 5732
    questions: int = 400
    scored: int = 355
    minimum_clusters: int = 20
    model_cache: str | None = str(GRANITE_CACHE)
    synthetic: bool = False  # True can never be reported as H2


SPEC = Spec()


def frozen_values() -> dict:
    return {'run_id': RUN_ID, 'version': VERSION, 'arm_order': list(ARMS), 'seed': SEED,
            'bootstrap': {'replicates': REPLICATES, 'unit': 'whole conversation; question-weighted within '
                          'sampled conversations', 'confidence': 0.95, 'ci_indexes': list(CI_INDEXES),
                          'method': 'percentile'},
            'metric': 'Recall@10 of annotated turns after packing to the shared context budget',
            'statistic': 'paired delta lexical minus semantic over scored questions',
            'top_k': TOP_K, 'context_bytes': CONTEXT_BYTES, 'chunk_chars': CHUNK_CHARS,
            'ingest_bytes': INGEST_BYTES, 'capture_namespace': NAMESPACE,
            'threshold': {'point_margin': str(POINT_MARGIN), 'ci_lower_margin': str(CI_MARGIN),
                          'rule': 'lexical Recall@10 >= semantic Recall@10 - 2/100 AND lower bound of the '
                                  'paired CI95 >= -3/100; a missing interval never passes'},
            'decision': {'met': 'threshold met with every question executed in both arms and 0 failure: the '
                                'lexical profile may become the recommended default',
                         'not_met': 'semantic stays recommended, lexical is offered as the light profile and '
                                    'the gap is published',
                         'always': 'no claim of superior recall'},
            'search': {'tool': 'memory_search', 'kinds': ['fact'], 'queries': None, 'rerank': False,
                       'at_time': None},
            'capture': 'memory_chat_save of observable chat content/role/session/id and explicit '
                       'time_anchor only; lossless 1,024-character quotes; no metadata or gold enrichment',
            'scoring': 'after BOTH arms completed, by the supervisor; workers never read annotations into '
                       'their output and no partial score is ever computed',
            'missing_evidence': 'questions without annotated evidence are executed in both arms and '
                                'excluded from the recall denominator only',
            'model': {'id': GRANITE, 'revision': GRANITE_REVISION}, 'arm_env': ARM_ENV, 'budgets': BUDGETS,
            'retrospective': {'independence_claim': False, 'tuning_allowed': False,
                              'prior_exposure': PRIOR_EXPOSURE}}


def arm_configs() -> dict:
    """Full effective engine configuration of each arm; they differ by the dense channel only."""
    from memory_os_v4.config import Config
    clone = Path(__file__).resolve().parents[3]
    if not Path(sys.modules[Config.__module__].__file__).resolve().is_relative_to(clone / 'v4/src'):
        raise ValueError('Engine source must be this clone, never an installed package')
    lexical = asdict(Config.lexical(data_dir=STORE_PLACEHOLDER, use_recency=False))
    semantic = asdict(Config(data_dir=STORE_PLACEHOLDER, store_backend='sqlite_s1', use_recency=False))
    Config(**lexical).validate_lexical_policy()
    Config(**semantic).validate_lexical_policy()
    if {key for key in lexical if lexical[key] != semantic[key]} != {'retrieval_profile', 'use_dense'}:
        raise ValueError('The arms must differ by the retrieval profile and its dense channel only')
    if semantic['embed_model'] != GRANITE or semantic['retrieval_profile'] != 'semantic':
        raise ValueError('The semantic arm is the default profile of this commit (Granite, C4)')
    for config in (lexical, semantic):
        if (config['store_backend'], config['use_recency'], config['bm25_profile'],
                config['lexical_guard_k']) != ('sqlite_s1', False, 'scoped_literal', 3):
            raise ValueError('Both arms: sqlite_s1, recency disabled, C4 lexical defaults')
    return {'lexical': lexical, 'semantic': semantic}


class Host:
    """Everything that touches Git, the interpreter or processes; replaced by fakes in tests only."""

    clone = Path(__file__).resolve().parents[3]

    def attest(self, revision: str) -> dict:
        code = code_record()
        verify_source(self.clone, revision, code)
        extras = {}
        for name in EXTRA_FILES:
            shown = subprocess.run(['git', '-C', str(self.clone), 'show', f'{revision}:{name}'],
                                   capture_output=True, timeout=10, cwd=self.clone)
            extras[name] = sha256(self.clone / name)
            if shown.returncode or hashlib.sha256(shown.stdout).hexdigest() != extras[name]:
                raise ValueError(f'Declared source commit does not match the working tree: {name}')
        return {'code_files': code, 'extra_files': extras}

    def archive(self, revision: str) -> bytes:
        done = subprocess.run(['git', '-C', str(self.clone), 'archive', '--format=tar', revision, *ARCHIVED],
                              capture_output=True, timeout=30, cwd=self.clone)
        if done.returncode:
            raise ValueError('Cannot archive the declared source commit')
        return done.stdout

    def environment(self) -> str:
        packages = sorted({f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions()},
                          key=str.casefold)
        return '\n'.join([f'# python=={platform.python_version()}', *packages]) + '\n'

    def hardware(self) -> dict:
        memory = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        return {'os': platform.platform(), 'cpu': platform.processor() or platform.machine(),
                'accelerator': 'mps when the default device "auto" resolves to it, otherwise none; the '
                               'resolved value is recorded in result.json',
                'device': 'lexical: no encoder; semantic: Config.device="auto", resolved by the engine',
                'python': sys.version, 'ram_gib': round(memory / 2 ** 30, 2)}

    def guard(self, directory: Path) -> dict:
        ensure_priority(Limits())
        return sample(directory)

    def launch(self, plan: dict, directory: Path, arm: str, manifest_sha256: str, deadline: float) -> None:
        """One worker in its own session; resources sampled every 0.5 s, as in the v2 campaign."""
        output = directory / 'retrieval' / arm
        env = offline_env()
        env.update(plan['arm_env'][arm])
        env['PYTHONPATH'] = os.pathsep.join([str(self.clone / 'v4'), str(self.clone / 'v4/src')])
        command = [sys.executable, '-B', '-m', 'bench.e2e.beam_h2', '_worker', '--plan-dir', str(directory),
                   '--arm', arm, '--manifest-sha256', manifest_sha256,
                   *(['--synthetic'] if plan['spec']['synthetic'] else [])]
        progress_time, last = time.monotonic(), None
        with (output / 'worker.log').open('x') as log:
            child = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=output,
                                     start_new_session=True)
            try:
                while child.poll() is None:
                    sample(directory)
                    progress = (output / 'progress').read_text() if (output / 'progress').exists() else None
                    if progress != last:
                        progress_time, last = time.monotonic(), progress
                    if (time.monotonic() > deadline or
                            time.monotonic() - progress_time > plan['budgets']['conversation_seconds']):
                        raise ResourceStop('Frozen campaign/conversation budget exceeded')
                    time.sleep(.5)
                if child.returncode:
                    raise RuntimeError(f'Worker {arm} failed; partial evidence preserved, no restart')
            finally:
                kill_group(child)


# ------------------------------------------------------------------ prepare
def inventory(data: Path, spec: Spec) -> tuple[dict, list[dict]]:
    """Counts and opaque identifiers only: no retrieval, no engine, no score."""
    if sha256(data) != spec.dataset_sha256:
        raise ValueError('Dataset checksum differs from the frozen corpus')
    provenance = json.loads((data.parent / 'PROVENANCE.json').read_text())
    if (provenance.get('revision') != spec.revision or provenance.get('license') != 'cc-by-sa-4.0'
            or provenance.get('derived_jsonl', {}).get('sha256') != spec.dataset_sha256):
        raise ValueError('BEAM provenance mismatch')
    rows, categories, messages, scored, unscored, clusters = [], Counter(), 0, 0, [], 0
    for conversation in conversations(data):
        messages += len(conversation.turns)
        clusters += any(probe.gold.evidence for probe in conversation.probes)
        for probe in conversation.probes:
            rows.append({'id': probe.id, 'conversation_id': conversation.id})
            categories[probe.gold.category] += 1
            scored += bool(probe.gold.evidence)
            if not probe.gold.evidence:
                unscored.append(probe.id)
    counts = {'conversations': len({row['conversation_id'] for row in rows}), 'messages': messages,
              'questions': len(rows), 'scored': scored, 'clusters': clusters}
    expected = {'conversations': spec.conversations, 'messages': spec.messages, 'questions': spec.questions,
                'scored': spec.scored, 'clusters': spec.conversations}
    if counts != expected or len({row['id'] for row in rows}) != len(rows):
        raise ValueError(f'Corpus counts differ from the frozen protocol: {counts}')
    return {**counts, 'categories': dict(sorted(categories.items())), 'unscored_question_ids': unscored,
            'provenance_sha256': sha256(data.parent / 'PROVENANCE.json'), 'license': 'CC-BY-SA-4.0',
            'revision': spec.revision, 'data_sha256': spec.dataset_sha256}, rows


def encoder_record(spec: Spec) -> dict | None:
    """Hashes of the cached snapshot the default model name resolves to offline; nothing is loaded."""
    if spec.model_cache is None:
        return None
    cache = Path(spec.model_cache)
    if (cache / 'refs/main').read_text().strip() != GRANITE_REVISION:
        raise ValueError('Cached Granite reference is not the frozen revision')
    record = model_record(str(cache / 'snapshots' / GRANITE_REVISION))
    return {'id': GRANITE, 'revision': GRANITE_REVISION, 'snapshot': record['id'],
            'snapshot_files': record['snapshot_files'], 'bytes': record['bytes'],
            'files_sha256': record['revision'], 'downloads': 0}


def record(path: Path) -> dict:
    return {'path': path.name, 'sha256': sha256(path)}


def manifest_document(plan: dict, directory: Path, hardware: dict) -> dict:
    launcher = (f'bash scripts/run_beam_h2.sh retrieve --plan-dir {directory} '
                '--manifest-sha256 <manifest sha256>')
    systems = [{'name': arm, 'revision': plan['source_commit'], 'budget_policy': 'shared',
                'command': f'{launcher}  # worker: python -m bench.e2e.beam_h2 _worker --arm {arm}',
                'config': plan['arms'][arm], 'environment': plan['arm_env'][arm]} for arm in ARMS]
    embedding = {'enabled': True, 'id': GRANITE, 'revision': GRANITE_REVISION,
                 'provider': 'local Hugging Face cache, offline (semantic arm only)',
                 'precision': 'native (Config.embed_dtype); loaded parameter dtype recorded in result.json',
                 'prompt_sha256': hashlib.sha256(b'').hexdigest(), 'prompt': 'none (empty prompt hashed)',
                 'max_seq_length': plan['arms']['semantic']['embed_max_seq_length'],
                 'batch_size': plan['arms']['semantic']['embed_batch_size']}
    budgets = plan['budgets']
    return {
        'protocol_version': '1.0', 'stage': 'final', 'run_id': plan['run_id'],
        'source': {'commit': plan['source_commit'], 'artifact': plan['files']['source_artifact']},
        'environment_lock': plan['files']['environment_lock'], 'seed': plan['seed'], 'hardware': hardware,
        'timing': {'mode': 'live', 'cache': 'warm', 'repetitions': 1, 'warmup_queries': 0,
                   'scope': 'ingest+retrieval+generation',
                   'note': 'no generation stage is run; ingestion and search are timed per arm'},
        'models': {'embedding': embedding,
                   **{role: {'enabled': False} for role in ('reranker', 'planner', 'response', 'judge')}},
        'tasks': [{'kind': 'retrieval', 'metrics': ['recall_at_k'],
                   'scoring_rule': f"{plan['metric']}; {plan['statistic']}; {plan['missing_evidence']}"}],
        'top_k': plan['top_k'],
        'budgets': {'memory_bytes': plan['ingest_bytes'], 'ingest_tokens': plan['ingest_bytes'] // 4,
                    'context_tokens': plan['context_bytes'] // 4, 'query_tokens': 512,
                    'model_calls': budgets['encoder_calls'], 'wall_seconds': budgets['retrieval_seconds'],
                    'api_usd': 0,
                    'note': 'binding limits are UTF-8 bytes (16,000,000 ingested per conversation, 12,000 '
                            'of context); token figures are bytes/4 estimates, not tokenizer budgets; '
                            'model_calls caps encoder calls of the semantic arm, LLM calls are 0'},
        'systems': systems,
        'datasets': [{'name': 'beam-100k', 'revision': plan['audit']['revision'], 'subset': '100K',
                      'license': plan['audit']['license'], 'exclusions': plan['missing_evidence'],
                      'data': {'path': plan['data'], 'sha256': plan['dataset_sha256']},
                      'splits': plan['files']['splits'], 'group_keys': ['conversation_id'],
                      'evaluation_count': plan['audit']['questions']}],
        'selection_rule': 'retrospective_no_selection', 'retrospective': plan['retrospective'],
        'reporting': 'separate_task_families',
        'plan': record(directory / 'plan.json'),
        'h2': {'threshold': plan['threshold'], 'decision': plan['decision'], 'bootstrap': plan['bootstrap'],
               'response_family': 'not evaluated', 'agent_task_family': 'not evaluated'},
    }


def prepare(directory: Path, data: Path, revision: str, *, spec: Spec = SPEC,
            host: Host | None = None) -> dict:
    from bench import evaluation_manifest
    host = host or Host()
    directory, data = directory.resolve(), data.resolve()
    if directory.is_relative_to(host.clone) or directory == data.parent:
        raise ValueError('The plan directory must be outside the Git clone and the data directory')
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError('A new or empty plan directory is required; a prior freeze is never overwritten')
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('An exact source Git commit is required')
    attested = host.attest(revision)
    audit, rows = inventory(data, spec)
    arms = arm_configs()
    encoder = encoder_record(spec)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json(directory / 'splits.json', {'evaluation': rows})
    (directory / 'requirements.lock').write_text(host.environment())
    (directory / 'source.tar').write_bytes(host.archive(revision))
    plan = {**frozen_values(), 'spec': asdict(spec), 'source_commit': revision, **attested,
            'data': str(data), 'dataset_sha256': spec.dataset_sha256, 'audit': audit, 'arms': arms,
            'config_difference': ['retrieval_profile', 'use_dense'], 'encoder_record': encoder,
            'files': {'splits': record(directory / 'splits.json'),
                      'environment_lock': record(directory / 'requirements.lock'),
                      'source_artifact': record(directory / 'source.tar')},
            'no_retuning': True, 'official_submission': False}
    write_json(directory / 'plan.json', plan)
    manifest = manifest_document(plan, directory, host.hardware())
    evaluation_manifest.validate(manifest, directory)
    write_json(directory / 'manifest.json', manifest)
    fingerprint = sha256(directory / 'manifest.json')
    now = datetime.now(timezone.utc).isoformat()
    # Same lock document as `evaluation_manifest.py --freeze`, so `--check-lock` applies unchanged.
    write_json(directory / 'frozen.json', {'protocol_version': '1.0', 'manifest_sha256': fingerprint,
                                           'frozen_at': now})
    write_json(directory / 'receipt.json', {'manifest_sha256': fingerprint, 'source_commit': revision,
                                            'plan_sha256': sha256(directory / 'plan.json'),
                                            'prepared_before_inference': True, 'frozen_at': now})
    return {'manifest_sha256': fingerprint, 'plan': plan}


# ------------------------------------------------------------------- freeze
def frozen(directory: Path, manifest_sha256: str, *, spec: Spec = SPEC, host: Host | None = None) -> dict:
    """Refuse unless manifest, plan, source, corpus, environment, encoder and arms are the frozen ones."""
    from bench import evaluation_manifest
    host = host or Host()
    directory = directory.resolve()
    if sha256(directory / 'manifest.json') != manifest_sha256:
        raise ValueError('Frozen manifest changed (or another manifest was reviewed)')
    manifest = json.loads((directory / 'manifest.json').read_text())
    lock = json.loads((directory / 'frozen.json').read_text())
    if lock.get('manifest_sha256') != manifest_sha256 or lock.get('protocol_version') != '1.0':
        raise ValueError('Freeze lock does not match the manifest')
    try:
        evaluation_manifest.validate(manifest, directory)
    except evaluation_manifest.ManifestError as error:
        raise ValueError(f'Frozen manifest no longer valid: {error}') from error
    if manifest['plan'] != record(directory / 'plan.json'):
        raise ValueError('Frozen plan changed')
    plan = json.loads((directory / 'plan.json').read_text())
    expected = frozen_values()
    if any(plan.get(key) != value for key, value in expected.items()) or plan['spec'] != asdict(spec):
        raise ValueError('Frozen protocol values differ from this harness')
    if manifest != manifest_document(plan, directory, manifest['hardware']):
        raise ValueError('Manifest and plan disagree')
    if host.attest(plan['source_commit']) != {key: plan[key] for key in ('code_files', 'extra_files')}:
        raise ValueError('Source commit or working tree changed')
    artifact = hashlib.sha256(host.archive(plan['source_commit'])).hexdigest()
    if artifact != plan['files']['source_artifact']['sha256']:
        raise ValueError('Source artifact changed')
    if sha256(Path(plan['data'])) != plan['dataset_sha256'] or plan['dataset_sha256'] != spec.dataset_sha256:
        raise ValueError('Dataset changed')
    if arm_configs() != plan['arms']:
        raise ValueError('Arm configuration changed')
    if host.environment() != (directory / 'requirements.lock').read_text():
        raise ValueError('Interpreter environment changed')
    if encoder_record(spec) != plan['encoder_record']:
        raise ValueError('Encoder snapshot changed')
    return plan


# ------------------------------------------------------------------- worker
class MLBlock(importlib.abc.MetaPathFinder):
    """Lexical arm: an ML import is refused and recorded, even when only attempted."""

    def __init__(self):
        self.attempts = []

    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ML_MODULES:
            self.attempts.append(name)
            raise ImportError('ML import forbidden in the lexical arm: ' + name)
        return None


def ml_loaded() -> list[str]:
    """Top-level ML packages present in this interpreter (any submodule counts)."""
    return sorted({name.split('.')[0] for name in sys.modules} & set(ML_MODULES))


class EngineClient:
    """Same tool contract as the v2 MCP client, dispatched in process on the arm's engine."""

    def __init__(self, server):
        self.server = server

    async def call(self, name: str, payload: dict) -> dict:
        out = self.server.dispatch(name, payload)
        if not isinstance(out, dict) or out.get('ok') is False:
            raise RuntimeError(f'Tool {name} returned an error')
        return out


def remove_store(store: Path, root: Path) -> None:
    if store.is_symlink() or store.parent != root or not store.is_dir():
        raise ValueError('Refusing cleanup outside the owned private conversation store')
    shutil.rmtree(store)


async def run_arm(plan: dict, arm: str, output: Path, *, block: MLBlock | None = None,
                  config_patch: dict | None = None) -> dict:
    """Ingest then search every conversation on a fresh store. Writes rankings only: no score here."""
    from .network import deny_ip_network
    deny_ip_network()
    lexical = arm == 'lexical'
    if lexical and not config_patch and (block is None or ml_loaded()):
        raise RuntimeError('The lexical arm requires a fresh interpreter with ML imports blocked')
    stages = {'start': ml_loaded()}
    offline = (os.environ.get('HF_HUB_OFFLINE'), os.environ.get('TRANSFORMERS_OFFLINE'))
    if not config_patch and offline != ('1', '1'):
        raise RuntimeError('Offline environment required (HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1)')
    import memory_os_v4  # first: the package pins single-threaded BLAS before any ML library loads
    from memory_os_v4 import Config, MemoryV4, server
    from memory_os_v4.config import LEXICAL_MARKER
    if not lexical and not config_patch:
        from .usage import install
        install(output / 'usage.json', plan['arms'][arm]['embed_model'])
    stages['import'] = ml_loaded()
    stores = output / 'stores'
    stores.mkdir()
    attestation = {'arm': arm, 'engine': 'memory_os_v4', 'engine_version': memory_os_v4.__version__,
                   'engine_file': memory_os_v4.__file__, 'config_patch': config_patch or {},
                   'python': sys.version, 'environment': {key: os.environ.get(key) for key in (
                       'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', *plan['arm_env'][arm])},
                   'conversations': [], 'encoder': None}
    adapter = AddSearchAdapter(EngineClient(server), plan['capture_namespace'])
    expected, started_all = [], time.monotonic()
    with (output / 'rankings.jsonl').open('x') as rankings:
        for conversation in conversations(Path(plan['data'])):
            store = stores / conversation.id
            if store.exists() or store.is_symlink():
                raise RuntimeError('Fresh conversation store required')
            config = Config(**{**plan['arms'][arm], **(config_patch or {}), 'data_dir': str(store)})
            started = time.monotonic()
            engine = MemoryV4(config=config)
            server.set_engine(engine)
            try:
                status = engine.status()
                if status['total'] or engine.store.meta.get('retrieval_profile') != (
                        LEXICAL_MARKER if lexical else None):
                    raise RuntimeError('Isolation/profile marker proof failed')
                if lexical and (engine.embedder is not None or engine.vector_index is not None):
                    raise RuntimeError('The lexical arm must own no encoder and no vector index')
                if not lexical and attestation['encoder'] is None and not config_patch:
                    profile = engine.embedder.encoding_profile()
                    attestation['encoder'] = {**profile, 'model': engine.embedder.model_name,
                                              'device': engine.embedder.device,
                                              'batch_size': engine.embedder.batch_size,
                                              'max_seq_length': engine.embedder.max_seq_length}
                    if profile['model_revision'] != plan['model']['revision']:
                        raise RuntimeError('Loaded encoder is not the frozen Granite revision')
                quotes = extract(project_for(adapter.namespace, conversation.id), conversation.turns,
                                 plan['chunk_chars'])
                if sum(len(q.text.encode()) for q in quotes) > plan['ingest_bytes']:
                    raise ResourceStop('Ingestion byte budget exceeded')
                by_id = {q.id: q for q in quotes}
                capture = await adapter._capture(conversation.id, conversation.turns, quotes)
                if capture['deduped'] or capture['added'] != len(quotes):
                    raise RuntimeError('Lossless capture parity failed')
                ingest_seconds = time.monotonic() - started
                search_ms = []
                for probe in conversation.probes:
                    expected.append(probe.id)
                    started = time.monotonic()
                    found = await adapter.search(conversation.id, probe.question, plan['top_k'])
                    search_ms.append((time.monotonic() - started) * 1000)
                    context = pack([by_id[hit['id']] for hit in found['results']], plan['top_k'],
                                   plan['context_bytes'])
                    # No category, evidence, answer or rubric: annotations are read by the scorer only.
                    rankings.write(json.dumps({
                        'qid': probe.id, 'conversation': conversation.id, 'arm': arm,
                        'returned': len(found['results']), 'turn_ids': [q.turn_id for q in context],
                        'quote_ids': [q.id for q in context], 'search_ms': search_ms[-1]}) + '\n')
                    rankings.flush()
                final = engine.status()
                attestation['conversations'].append({
                    'conversation': conversation.id, 'quotes': len(quotes), 'questions': len(search_ms),
                    'ingest_seconds': ingest_seconds, 'search_ms_total': sum(search_ms),
                    'items': final['counts'], 'vector_count': final['vector_count'],
                    'marker': engine.store.meta.get('retrieval_profile'),
                    'store_bytes': sum(p.stat().st_size for p in store.rglob('*') if p.is_file())})
            finally:
                server.set_engine(None)  # flush, close SQLite, release the writer lease
            remove_store(store, stores)
            stages[f'conversation-{len(attestation["conversations"])}'] = ml_loaded()
            (output / 'progress').write_text(conversation.id)
    stages['end'] = ml_loaded()
    attestation.update(ml_modules=stages, ml_import_attempts=list(block.attempts) if block else None,
                       ml_free=(lexical and block is not None and not block.attempts
                                and not any(stages.values())),
                       question_ids_sha256=hashlib.sha256(json.dumps(expected).encode()).hexdigest(),
                       questions=len(expected), seconds=time.monotonic() - started_all,
                       rankings_sha256=sha256(output / 'rankings.jsonl'))
    if lexical and not config_patch and not attestation['ml_free']:
        raise RuntimeError('The lexical arm imported or attempted to import an ML module')
    if (output / 'usage.json').exists():
        attestation['usage'] = json.loads((output / 'usage.json').read_text())
    write_json(output / 'attestation.json', attestation)
    return attestation


# ------------------------------------------------------------------ scoring
def exact_recall(value: float, evidence: int) -> Fraction:
    exact = Fraction(value).limit_denominator(evidence)
    if float(exact) != value:
        raise ValueError('Recall is not a ratio of annotated turns')
    return exact


def paired_bootstrap(groups: dict[str, list[Fraction]], seed: int = SEED, replicates: int = REPLICATES,
                     minimum: int = 20) -> list[Fraction] | None:
    """Whole-conversation resampling, same draws as beam_campaign.summarize, in exact arithmetic."""
    if replicates != REPLICATES:
        raise ValueError('The frozen bootstrap has exactly 2,000 replicates')
    keys = sorted(groups)
    if len(keys) < minimum:
        return None
    totals = {key: (sum(groups[key]), len(groups[key])) for key in keys}
    rng = random.Random(seed)
    draws = []
    for _ in range(replicates):
        chosen = rng.choices(keys, k=len(keys))
        draws.append(sum(totals[key][0] for key in chosen) / sum(totals[key][1] for key in chosen))
    draws.sort()
    return [draws[CI_INDEXES[0]], draws[CI_INDEXES[1]]]


def verdict(lexical: Fraction, semantic: Fraction, interval: list[Fraction] | None, *, executed: int,
            expected: int, failed: int) -> dict:
    """Mechanical application of the frozen threshold; exact comparisons, a missing interval fails."""
    complete = executed == expected and failed == 0
    point = lexical >= semantic - POINT_MARGIN
    lower = interval is not None and interval[0] >= -CI_MARGIN
    met = bool(complete and point and lower)
    return {'complete': complete, 'point_criterion_met': bool(point), 'ci_criterion_met': bool(lower),
            'threshold_met': met,
            'decision': 'lexical_may_become_recommended_default' if met
            else 'semantic_stays_recommended_lexical_light_profile_gap_published',
            'claim': 'retrospective parity check on BEAM 100K only; no claim of superior recall, no '
                     'statistical independence, nothing about generated answers or agent tasks'}


def rankings(path: Path) -> dict:
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # interrupted last row: counted as a failure below
            if row['qid'] in rows:
                raise ValueError('Duplicate ranking row')
            rows[row['qid']] = row
    return rows


def assemble(plan: dict, directory: Path, stopped: str | None, spec: Spec) -> dict:
    """Score both arms. With any failure nothing is scored: no partial observation exists."""
    output = directory / 'retrieval'
    split = [row['id'] for row in json.loads((directory / 'splits.json').read_text())['evaluation']]
    found = {arm: rankings(output / arm / 'rankings.jsonl') for arm in ARMS}
    attestations = {arm: json.loads(path.read_text()) if (path := output / arm / 'attestation.json').exists()
                    else None for arm in ARMS}
    failed = sorted(qid for qid in split if any(qid not in found[arm] for arm in ARMS))
    reasons = [stopped] if stopped else []
    if any(set(found[arm]) - set(split) for arm in ARMS):
        reasons.append('Rankings outside the frozen evaluation partition')
    for arm in ARMS:
        proof = attestations[arm]
        if proof is None:
            reasons.append(f'{arm}: no attestation')
        elif (proof['config_patch'] and not spec.synthetic) or proof['engine'] != 'memory_os_v4':
            reasons.append(f'{arm}: injected engine or configuration')
        elif proof.get('usage', {}).get('encoder_calls', 0) > plan['budgets']['encoder_calls']:
            reasons.append(f'{arm}: encoder call budget exceeded')
        elif (proof['questions'] != len(split)
              or proof['rankings_sha256'] != sha256(output / arm / 'rankings.jsonl')):
            reasons.append(f'{arm}: rankings differ from the worker attestation')
    encoder = (attestations['semantic'] or {}).get('encoder') or {}
    if attestations['semantic'] and not spec.synthetic and (
            encoder.get('model_revision') != plan['model']['revision'] or not encoder.get('device')):
        reasons.append('semantic: frozen encoder revision or device not attested')
    if attestations['lexical'] and not attestations['lexical']['ml_free'] and not spec.synthetic:
        reasons.append('lexical: ML-free proof missing')
    result = {'run_id': plan['run_id'], 'version': plan['version'], 'synthetic': spec.synthetic,
              'source_commit': plan['source_commit'], 'manifest_sha256': sha256(directory / 'manifest.json'),
              'plan_sha256': sha256(directory / 'plan.json'), 'dataset_sha256': plan['dataset_sha256'],
              'expected': len(split), 'executed': len(split) - len(failed), 'failed': len(failed),
              'failed_question_ids': failed, 'invalid_reasons': reasons, 'threshold': plan['threshold'],
              'bootstrap': {**plan['bootstrap'], 'seed': plan['seed']}, 'arms': attestations,
              'rankings_sha256': {arm: sha256(path) if (path := output / arm / 'rankings.jsonl').exists()
                                  else None for arm in ARMS}}
    if failed or reasons:
        outcome = verdict(Fraction(0), Fraction(0), None, executed=result['executed'], expected=len(split),
                          failed=max(len(failed), 1))
        outcome.update(point_criterion_met=None, ci_criterion_met=None)
        result.update(outcome, scored=None, clusters=None, recall_at_10=None, paired_delta=None,
                      note='incomplete or invalid run: nothing was scored, nothing may be observed or rerun '
                           'under this manifest')
        write_json(output / 'result.json', result)
        return result
    groups, sums, scored = {}, {arm: Fraction(0) for arm in ARMS}, 0
    with (output / 'questions.jsonl').open('x') as questions:
        for conversation in conversations(Path(plan['data'])):
            for probe in conversation.probes:
                values = {arm: score(found[arm][probe.id]['turn_ids'], None, probe.gold,
                                     plan['top_k'])['recall'] for arm in ARMS}
                row = {'qid': probe.id, 'conversation': conversation.id, 'category': probe.gold.category,
                       'evidence_turns': len(set(probe.gold.evidence)), **values}
                questions.write(json.dumps(row, allow_nan=False) + '\n')
                if not probe.gold.evidence:
                    continue
                exact = {arm: exact_recall(values[arm], row['evidence_turns']) for arm in ARMS}
                scored += 1
                for arm in ARMS:
                    sums[arm] += exact[arm]
                groups.setdefault(conversation.id, []).append(exact['lexical'] - exact['semantic'])
    if scored != spec.scored:
        raise ValueError('Scored denominator differs from the frozen protocol')
    means = {arm: sums[arm] / scored for arm in ARMS}
    interval = paired_bootstrap(groups, plan['seed'], plan['bootstrap']['replicates'], spec.minimum_clusters)
    delta = means['lexical'] - means['semantic']
    result.update(verdict(means['lexical'], means['semantic'], interval, executed=result['executed'],
                          expected=len(split), failed=0),
                  scored=scored, clusters=len(groups), minimum_clusters=spec.minimum_clusters,
                  recall_at_10={arm: {'value': float(means[arm]), 'exact': str(means[arm])} for arm in ARMS},
                  paired_delta={'definition': 'lexical minus semantic', 'mean': float(delta),
                                'exact': str(delta),
                                'ci95': None if interval is None else [float(v) for v in interval],
                                'ci95_exact': None if interval is None else [str(v) for v in interval]},
                  questions_sha256=sha256(output / 'questions.jsonl'))
    write_json(output / 'result.json', result)
    return result


def retrieve(directory: Path, manifest_sha256: str, *, spec: Spec = SPEC, host: Host | None = None,
             arms: tuple = ARMS) -> dict:
    """``arms`` is narrowed by the synthetic self-check only; H2 always runs both, in order."""
    if tuple(arms) != ARMS and not spec.synthetic:
        raise ValueError('H2 runs both arms')
    host = host or Host()
    directory = directory.resolve()
    plan = frozen(directory, manifest_sha256, spec=spec, host=host)
    first = host.guard(directory)
    output = directory / 'retrieval'
    output.mkdir(mode=0o700, exist_ok=False)  # one attempt per frozen manifest, never a restart
    deadline, stopped = time.monotonic() + plan['budgets']['retrieval_seconds'], None
    for arm in arms:
        (output / arm).mkdir(mode=0o700)
        try:
            frozen(directory, manifest_sha256, spec=spec, host=host)
            host.launch(plan, directory, arm, manifest_sha256, deadline)
            frozen(directory, manifest_sha256, spec=spec, host=host)
        except (ResourceStop, RuntimeError, ValueError, OSError) as error:
            stopped = f'{arm}: {error}'
            break  # no later arm is started
    result = assemble(plan, directory, stopped, spec)
    last = host.guard(directory)
    write_json(output / 'resources.json', {'first': first, 'last': last})
    return result


SELFCHECK_TOPICS = ('harbour lighthouse', 'saffron risotto', 'violin recital', 'glacier trek', 'pottery kiln',
                    'telescope mirror')
SELFCHECK_COUNTS = {'conversations': 4, 'messages': 24, 'questions': 40, 'scored': 32, 'minimum_clusters': 4}


def selfcheck_rows(count: int = 4) -> list[dict]:
    """Tiny invented corpus in the BEAM row schema; it shares nothing with BEAM."""
    from .beam import CATEGORIES
    rows = []
    for index in range(count):
        turns = [{'content': f'Conversation {index}: we discussed the {topic} plan number {index}{position}.',
                  'role': 'user' if position % 2 == 0 else 'assistant', 'id': position, 'index': 'SECRET',
                  'question_type': 'SECRET', 'time_anchor': None}
                 for position, topic in enumerate(SELFCHECK_TOPICS)]
        probes = {}
        for position, category in enumerate(CATEGORIES):
            key = {'abstention': 'ideal_response', 'contradiction_resolution': 'ideal_answer',
                   'instruction_following': 'expected_compliance',
                   'preference_following': 'expected_compliance',
                   'summarization': 'ideal_summary'}.get(category, 'answer')
            topic = SELFCHECK_TOPICS[position % len(SELFCHECK_TOPICS)]
            probe = {'question': f'What was said about the {topic} ({category})?', key: 'SCORER_SECRET',
                     'rubric': ['SCORER_RUBRIC_SECRET']}
            if category != 'abstention':  # one explicit empty annotation per conversation, as in BEAM
                probe['source_chat_ids'] = ([] if category == 'summarization'
                                            else [position % len(SELFCHECK_TOPICS)])
            probes[category] = [probe]
        rows.append({'conversation_id': f'conv-{index}', 'conversation_seed': 'SECRET',
                     'narratives': 'SECRET', 'user_profile': 'SECRET', 'conversation_plan': 'SECRET',
                     'user_questions': 'SECRET',
                     'chat': [turns[:3], turns[3:]], 'probing_questions': repr(probes)})
    return rows


def selfcheck_corpus(directory: Path, model_cache: str | None = str(GRANITE_CACHE)) -> tuple[Path, Spec]:
    directory.mkdir(parents=True)
    data = directory / 'synthetic.jsonl'
    data.write_text(''.join(json.dumps(row) + '\n' for row in selfcheck_rows()))
    spec = Spec(dataset_sha256=sha256(data), model_cache=model_cache, synthetic=True, **SELFCHECK_COUNTS)
    write_json(directory / 'PROVENANCE.json', {'revision': spec.revision, 'license': 'cc-by-sa-4.0',
                                               'derived_jsonl': {'sha256': spec.dataset_sha256},
                                               'note': 'invented self-check rows, not BEAM'})
    return data, spec


def selfcheck(directory: Path, revision: str, *, arms: tuple = ARMS, host: Host | None = None,
              model_cache: str | None = str(GRANITE_CACHE)) -> dict:
    """Same freeze, launcher, workers and scorer on invented rows: proves the tooling, observes no BEAM."""
    directory = directory.resolve()
    if directory.exists():
        raise ValueError('A new self-check directory is required')
    data, spec = selfcheck_corpus(directory / 'corpus', model_cache)
    done = prepare(directory / 'plan', data, revision, spec=spec, host=host)
    return retrieve(directory / 'plan', done['manifest_sha256'], spec=spec, host=host, arms=arms)


def worker(directory: Path, arm: str, manifest_sha256: str, synthetic: bool = False) -> None:
    block = None
    if arm == 'lexical':
        block = MLBlock()
        sys.meta_path.insert(0, block)
    spec = SPEC
    if synthetic:  # self-check only: the plan must declare itself synthetic, and its result says so
        spec = Spec(**json.loads((directory / 'plan.json').read_text())['spec'])
        if not spec.synthetic:
            raise ValueError('The frozen H2 plan is never run as a synthetic self-check')
    plan = frozen(directory, manifest_sha256, spec=spec)
    asyncio.run(run_arm(plan, arm, directory.resolve() / 'retrieval' / arm, block=block))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'retrieve', 'selfcheck', '_worker'])
    parser.add_argument('--plan-dir', type=Path, required=True)
    parser.add_argument('--data', type=Path, help='prepare: path of beam_100k.jsonl (read-only)')
    parser.add_argument('--source-commit', help='prepare: exact commit of the delivered harness')
    parser.add_argument('--manifest-sha256', help='retrieve: SHA-256 of the reviewed frozen manifest.json')
    parser.add_argument('--arm', choices=ARMS, help='selfcheck: run this arm only')
    parser.add_argument('--synthetic', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.operation == 'prepare':
            if not args.source_commit or not args.data:
                parser.error('--data and --source-commit required')
            done = prepare(args.plan_dir, args.data, args.source_commit)
            print(json.dumps({'plan_dir': str(args.plan_dir.resolve()),
                              'manifest_sha256': done['manifest_sha256'],
                              'audit': {key: done['plan']['audit'][key] for key in (
                                  'conversations', 'messages', 'questions', 'scored')}}))
            return 0
        if args.operation == 'selfcheck':
            if not args.source_commit:
                parser.error('--source-commit required')
            result = selfcheck(args.plan_dir, args.source_commit, arms=(args.arm,) if args.arm else ARMS)
            print(json.dumps({'result': str(args.plan_dir.resolve() / 'plan/retrieval/result.json'),
                              'synthetic': result['synthetic'], 'complete': result['complete'],
                              'invalid_reasons': result['invalid_reasons']}))
            return 0 if result['complete'] or args.arm else 1
        if not args.manifest_sha256:
            parser.error('--manifest-sha256 required')
        if args.operation == '_worker':
            if not args.arm:
                parser.error('--arm required')
            worker(args.plan_dir, args.arm, args.manifest_sha256, args.synthetic)
            return 0
        result = retrieve(args.plan_dir, args.manifest_sha256)
        print(json.dumps({'result': str(args.plan_dir.resolve() / 'retrieval/result.json'),
                          'complete': result['complete'], 'threshold_met': result['threshold_met']}))
        return 0 if result['complete'] else 1
    except (ValueError, OSError, ResourceStop) as error:
        print(f'STOP: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
