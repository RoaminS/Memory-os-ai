"""BEAM 100K v1: bounded literal decoding, observational input/gold firewall."""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import unicodedata

from .data import Gold, Turn, array_entries, sha256

CATEGORIES = ('abstention', 'contradiction_resolution', 'event_ordering',
              'information_extraction', 'instruction_following', 'knowledge_update',
              'multi_session_reasoning', 'preference_following', 'summarization', 'temporal_reasoning')
DATA_SHA = '613b85b9e5eca98b88de25bedb7490f1dbf61ae4c361d544452feea19bfa18a3'
PARQUET_SHA = 'c0519be25907005ba873c927c50877471d550873039d96c041554d0075a78ace'
REVISION = '3205395e897e7318c7b094ef4e6047b9b82dbb03'
S_SHA = '08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894'
MAX_ROW = 8_000_000
CAPTURE_DATE = '2000-01-01T00:00:00+00:00'  # synthetic capture, NEVER an inferred real date


def identity(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:24]


def pairs(entries):
    result = {}
    for key, value in entries:
        if key in result:
            raise ValueError('Duplicate object key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Non-finite JSON constant')


def decode(text, literal=False, *, max_bytes=MAX_ROW, max_nodes=100_000):
    if not isinstance(text, str) or len(text.encode()) > max_bytes:
        raise ValueError('Bounded string required')
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
        pending = [(value, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > max_nodes or depth > 30:
                raise ValueError('JSON node/depth budget exceeded')
            if isinstance(node, dict):
                pending.extend((v, depth + 1) for v in node.values())
            elif isinstance(node, list):
                pending.extend((v, depth + 1) for v in node)
        return value
    except json.JSONDecodeError:
        if not literal:
            raise
    # Published parquet stores Python repr. Only a bounded literal grammar is
    # admitted, never calls, names, tuple/set literals, operators or evaluation.
    tree = ast.parse(text, mode='eval')
    allowed = (ast.Expression, ast.Dict, ast.List, ast.Constant, ast.Load)
    pending = [(tree, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > max_nodes or depth > 30 or not isinstance(node, allowed):
            raise ValueError('Invalid/budget-exceeding BEAM literal')
        if isinstance(node, ast.Constant) and (type(node.value) not in (str, int, bool, type(None))):
            raise ValueError('Invalid literal scalar')
        if isinstance(node, ast.Dict):
            if any(not isinstance(k, ast.Constant) or type(k.value) is not str for k in node.keys):
                raise ValueError('String object keys required')
            names = [k.value for k in node.keys]
            if len(set(names)) != len(names):
                raise ValueError('Duplicate literal key')
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return ast.literal_eval(tree)


def evidence_ids(value):
    if isinstance(value, dict):
        return [i for part in value.values() for i in evidence_ids(part)]
    if isinstance(value, list):
        return [i for part in value for i in evidence_ids(part)]
    if type(value) is not int or value < 0:
        raise ValueError('Evidence must be nonnegative chat ids')
    return [value]


@dataclass(frozen=True)
class Probe:
    id: str
    question: str
    gold: Gold
    rubric: tuple[str, ...]


@dataclass(frozen=True)
class Conversation:
    source_id: str
    id: str
    turns: tuple[Turn, ...]
    probes: tuple[Probe, ...]


def conversations(path: Path, check=lambda: None, *, max_row=MAX_ROW, max_nodes=100_000):
    seen = set()
    with path.open('rb') as stream:
        while raw := stream.readline(max_row + 1):
            check()
            if len(raw) > max_row or not raw.endswith(b'\n'):
                raise ValueError('Oversized or unterminated JSONL row')
            row = decode(raw.decode('utf-8'), max_bytes=max_row, max_nodes=max_nodes)
            expected = {'conversation_id', 'conversation_seed', 'narratives', 'user_profile',
                        'conversation_plan', 'user_questions', 'chat', 'probing_questions'}
            if not isinstance(row, dict) or set(row) != expected:
                raise ValueError('Unrecognized BEAM row schema')
            cid = row['conversation_id']
            if not isinstance(cid, str) or not cid or cid in seen:
                raise ValueError('Nonempty unique conversation id required')
            seen.add(cid)
            observable = identity('beam-conversation-v1', cid)
            if not isinstance(row['chat'], list) or not row['chat']:
                raise ValueError('Nonempty sessions required')
            turns, chat_ids = [], set()
            for session_index, session in enumerate(row['chat']):
                if not isinstance(session, list) or not session:
                    raise ValueError('Nonempty session required')
                for turn in session:
                    if not isinstance(turn, dict) or set(turn) != {'content', 'id', 'index', 'question_type', 'role', 'time_anchor'}:
                        raise ValueError('Unrecognized observable turn schema')
                    tid = turn['id']
                    if type(tid) is not int or tid < 0 or tid in chat_ids:
                        raise ValueError('Unique nonnegative chat id required')
                    chat_ids.add(tid)
                    if turn['role'] not in {'user', 'assistant'} or not isinstance(turn['content'], str) or not turn['content'].strip():
                        raise ValueError('Invalid role/content')
                    anchor = turn['time_anchor']
                    if anchor is not None and (not isinstance(anchor, str) or not anchor.strip()):
                        raise ValueError('Invalid observable time anchor')
                    content = (f'[time_anchor: {anchor}]\n' if anchor else '') + turn['content']
                    # Narrative, seed, profile, plan, user_questions, index and
                    # question_type are deliberately NEVER included in inputs.
                    turns.append(Turn(identity(cid, tid), identity(cid, session_index),
                                      turn['role'], content, CAPTURE_DATE))
            questions = decode(row['probing_questions'], literal=True)
            if not isinstance(questions, dict) or set(questions) != set(CATEGORIES):
                raise ValueError('Exactly the ten BEAM categories required')
            probes, question_texts = [], set()
            for category in CATEGORIES:
                entries = questions[category]
                if not isinstance(entries, list) or not entries:
                    raise ValueError('Nonempty probing list required')
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get('question'), str) or not entry['question'].strip():
                        raise ValueError('Nonempty question required')
                    question = entry['question']
                    if question in question_texts:
                        raise ValueError('Duplicate question in conversation')
                    question_texts.add(question)
                    qid = identity(cid, question)  # no answer/category in identity
                    refs = evidence_ids(entry.get('source_chat_ids', []))
                    if (category != 'abstention' and 'source_chat_ids' not in entry) or not set(refs) <= chat_ids:
                        raise ValueError('Missing/dangling evidence annotation')
                    if category == 'abstention' and refs:
                        raise ValueError('Abstention unexpectedly has positive evidence')
                    answer_key = {'abstention': 'ideal_response', 'contradiction_resolution': 'ideal_answer',
                                  'instruction_following': 'expected_compliance', 'preference_following': 'expected_compliance',
                                  'summarization': 'ideal_summary'}.get(category, 'answer')
                    answer = entry.get(answer_key)
                    rubric = entry.get('rubric')
                    if not isinstance(answer, str) or not answer.strip() or not isinstance(rubric, list) or not rubric or any(not isinstance(r, str) or not r.strip() for r in rubric):
                        raise ValueError('Reference and string rubric required')
                    gold = Gold(qid, tuple(identity(cid, r) for r in dict.fromkeys(refs)),
                                (answer,), category, (observable,), 0)
                    probes.append(Probe(qid, question, gold, tuple(rubric)))
            yield Conversation(cid, observable, tuple(turns), tuple(probes))


def text_key(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def audit(data, provenance, longmemeval, check=lambda: None):
    manifest = decode(provenance.read_text())
    if (manifest.get('revision') != REVISION or manifest.get('license') != 'cc-by-sa-4.0'
            or manifest.get('sha256') != PARQUET_SHA or manifest.get('derived_jsonl', {}).get('sha256') != DATA_SHA
            or sha256(data, check) != DATA_SHA or sha256(data.with_name('beam_100k.parquet'), check) != PARQUET_SHA):
        raise ValueError('BEAM provenance/checksums mismatch')
    if sha256(longmemeval, check) != S_SHA:
        raise ValueError('LongMemEval-S checksum mismatch')
    old_questions, old_turns = set(), set()
    for entry in array_entries(longmemeval, check=check):
        old_questions.add(text_key(entry['question']))
        old_turns.update(text_key(t.get('content') or '') for s in entry['haystack_sessions'] for t in s if t.get('content'))
    categories, overlap_q, overlap_t = Counter(), [], 0
    ids, total_turns, evidence, missing_evidence = [], 0, 0, []
    for conversation in conversations(data, check):
        ids.append(conversation.source_id)
        total_turns += len(conversation.turns)
        for turn in conversation.turns:
            content = turn.content.split('\n', 1)[1] if turn.content.startswith('[time_anchor: ') else turn.content
            overlap_t += text_key(content) in old_turns
        for p in conversation.probes:
            categories[p.gold.category] += 1
            evidence += bool(p.gold.evidence)
            if p.gold.category != 'abstention' and not p.gold.evidence:
                missing_evidence.append(p.id)
            if text_key(p.question) in old_questions:
                overlap_q.append(p.id)
    if len(ids) != 20 or sum(categories.values()) != 400:
        raise ValueError('Expected fixed 20-conversation, 400-question 100K split')
    return {'conversations': len(ids), 'conversation_ids': ids, 'questions': sum(categories.values()),
            'turns': total_turns, 'categories': dict(categories), 'annotated_evidence_questions': evidence,
            'abstention_questions': categories['abstention'], 'overlap_question_ids': overlap_q,
            'empty_evidence_question_ids': missing_evidence,
            'overlap_turns': overlap_t, 'overlap_method': 'NFKC/casefold/whitespace exact questions and chat messages; NOT semantic duplication detection',
            'data_sha256': DATA_SHA, 'parquet_sha256': PARQUET_SHA, 'longmemeval_sha256': S_SHA,
            'provenance_sha256': sha256(provenance), 'license': 'CC-BY-SA-4.0', 'revision': REVISION}
