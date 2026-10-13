"""Extractive whole-unit selection; no model, write, external tool or text synthesis."""
from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
import json
import re

LAYOUT = "body_spans_v1"
ABBREVIATIONS = {"dr.", "mr.", "mrs.", "ms.", "m.", "mme.", "etc.", "e.g.", "i.e."}


def first_sentence(text):
    """Conservative complete sentence, never a character/token-length prefix."""
    if "```" in text or "~~~" in text:
        return None  # Whole fenced code or reference; no synthetic code fragments.
    for match in re.finditer(r"[.!?。！？](?:[\"”’»)]*)(?=\s|$)", text):
        end = match.end()
        prefix = text[:end]
        last = prefix.rsplit(None, 1)[-1].casefold() if prefix.strip() else ""
        if last in ABBREVIATIONS or prefix.count("`") % 2:
            continue
        return prefix if end < len(text.rstrip()) else None
    return None


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str
    group: str
    text: str
    order: tuple
    protected: bool = False
    representation: str = "full"

    def variants(self):
        yield self
        sentence = first_sentence(self.text) if self.kind != "task" else None
        if sentence:
            yield replace(self, text=sentence, representation="sentence")
        yield replace(self, text="", representation="reference")


def label(unit_id):
    # IDs in metadata are exact. Presentation must not inject extra physical
    # lines/brackets; rendered contents themselves remain unmodified DATA.
    return json.dumps(unit_id, ensure_ascii=False) if any(c in unit_id for c in "\n\r]") else unit_id


def task_text(task):
    return (f"{task['status']} | {task['title']} | owner={(task.get('owner') or {}).get('client_id')} | "
            f"next={task['next_action']} | blocked={task['blocked_reason']} | stale={task['stale']}")


def notices_for(units, pool, notices):
    codes = list(dict.fromkeys(notices))
    chosen = {u.id for u in units}
    if len(chosen) < len(pool) or any(u.representation != 'full' for u in units):
        codes.append("budget_saturated")
    if any(u.representation != "full" and u.kind == "block" for u in units):
        codes.append("block_truncated")
    if any(u.representation != "full" and u.kind != "block" for u in units):
        codes.append("item_truncated")
    if any(u.protected and u.id not in chosen for u in pool):
        codes.append("critical_omitted")
    if any(u.protected and u.representation == "reference" for u in units):
        codes.append("critical_reference_only")
    return list(dict.fromkeys(codes))


def assemble(base, units, codes):
    from .brief import WARNING
    out = deepcopy(base)
    units = list(units)
    if out.get('coverage'):
        from .brief_coverage import update
        out['coverage'] = update(out['coverage'], units)
    out.update(unit_layout=LAYOUT, blocks={}, references=[], notices=list(dict.fromkeys(codes)))
    lines, at = [], 0
    for unit in sorted(units, key=lambda u: u.order):
        line = "[" + label(unit.id) + "]" + (" " + unit.text if unit.text else "")
        meta = {"span": [at, at + len(line)], "rendering": unit.representation}
        if unit.protected:
            meta["protected"] = True
        if unit.kind == "block":
            out["blocks"][unit.id[6:]] = {"reference": unit.id, **meta}
        else:
            out["references"].append({"id": unit.id, "kind": unit.kind, **meta})
        lines.append(line)
        at += len(line) + 1
    body = "\n".join(lines)
    out["text"] = WARNING + "\n" + ("Notices: " + ", ".join(out["notices"]) + "\n" if out["notices"] else "") + body
    return out


def body_of(data):
    from .brief import WARNING
    body = data.get("text", "")
    if not isinstance(body, str):
        raise ValueError("Invalid brief text")
    if body.startswith(WARNING + "\n"):
        body = body[len(WARNING) + 1:]
        if body.startswith("Notices: "):
            body = body.split("\n", 1)[1] if "\n" in body else ""
    return body


def read_units(data):
    """Validate ranges, do not parse markers inside untrusted memory contents."""
    if data.get("unit_layout") != LAYOUT:
        return None
    body = body_of(data)
    rows = [(v['reference'], 'block', v) for v in data.get('blocks', {}).values()]
    rows.extend((v['id'], v['kind'], v) for v in data.get('references', []))
    rows.sort(key=lambda r: r[2]['span'][0])
    out, end, seen = [], 0, set()
    for index, (unit_id, kind, meta) in enumerate(rows):
        start, stop = meta['span']
        if (not isinstance(unit_id, str) or unit_id in seen
                or type(start) is not int or type(stop) is not int
                or start != end or not start <= stop <= len(body)):
            raise ValueError("Invalid/overlapping unit spans")
        rendering = meta['rendering']
        prefix = '[' + label(unit_id) + ']'
        line = body[start:stop]
        if rendering not in {'full', 'sentence', 'reference'} or not line.startswith(prefix):
            raise ValueError("Invalid unit representation")
        if rendering == 'reference':
            if line != prefix:
                raise ValueError("Reference-only unit has unexpected text")
            text = ''
        else:
            if not line.startswith(prefix + ' '):
                raise ValueError("Invalid text separator")
            text = line[len(prefix) + 1:]
        out.append(Unit(unit_id, kind, 'block' if kind == 'block' else 'task' if kind == 'task' else 'memory',
                        text, (index,), meta.get('protected', False), rendering))
        seen.add(unit_id)
        end = stop + 1
    if (rows and end - 1 != len(body)) or (not rows and body):
        raise ValueError("Unindexed trailing body")
    return out


def fits(data, count, byte_limit, token_limit, *, prospective=True):
    from .brief import measures
    if prospective:
        units = read_units(data)
        data = assemble(data, units or [], [*data.get('notices', []), 'unresolved_requests'])
    size, tokens = measures(data, count)
    return size <= byte_limit and tokens <= token_limit


def select(base, pool, notices, count, byte_limit, token_limit):
    """Protected pass, initial section caps, memory, then reassigned leftovers."""
    chosen = {}
    # Optional detail must not make an otherwise feasible minimum frame fail.
    # Freeze this representation before selection so trial units cannot cause
    # oscillation in available content capacity.
    from .brief_coverage import collapse
    base = deepcopy(base)
    while not fits(assemble(base, [], notices_for([], pool, notices)), count, byte_limit, token_limit):
        if not collapse(base):
            break
        notices = list(dict.fromkeys([*notices, *base.get('notices', [])]))

    def candidate(unit, *, cap=None):
        for variant in unit.variants():
            proposal = {**chosen, unit.id: variant}
            value = assemble(base, proposal.values(), notices_for(proposal.values(), pool, notices))
            if not fits(value, count, byte_limit, token_limit):
                continue
            if cap is not None:
                # Marginal section cost from the real final envelope, including
                # metadata/escaping and the worst local unresolved notice.
                section = [u for u in proposal.values() if u.group == unit.group and not u.protected]
                empty = assemble(base, [], [*notices, 'unresolved_requests'])
                filled = assemble(base, section, [*notices, 'unresolved_requests'])
                from .brief import measures
                eb, et = measures(empty, count)
                fb, ft = measures(filled, count)
                if max(0, fb-eb) > cap[0] or max(0, ft-et) > cap[1]:
                    continue
            chosen[unit.id] = variant
            return

    for unit in pool:
        if unit.protected or unit.order[0] == 0:
            candidate(unit)
    from .brief import measures
    empty = assemble(base, chosen.values(), notices_for(chosen.values(), pool, notices))
    reserve = assemble(empty, list(chosen.values()), [*empty['notices'], 'unresolved_requests'])
    eb, et = measures(reserve, count)
    remaining = (max(0, byte_limit-eb), max(0, token_limit-et))
    for group, share in (('block', .30), ('task', .20)):
        cap = (int(remaining[0]*share), int(remaining[1]*share))
        for unit in pool:
            if unit.group == group and unit.id not in chosen:
                candidate(unit, cap=cap)
    for unit in pool:
        if unit.group == 'memory' and unit.id not in chosen:
            candidate(unit)
    for unit in pool:
        # Reassign unused capacity AND upgrade provisional references/excerpts.
        if unit.id not in chosen or chosen[unit.id].representation != 'full':
            candidate(unit)
    result = assemble(base, chosen.values(), notices_for(chosen.values(), pool, notices))
    return rebudget_units(result, (), count, byte_limit, token_limit)


def rebudget_units(data, notices, count, byte_limit, token_limit):
    from .brief import measures, compact_error
    out = deepcopy(data)
    units = read_units(out)
    codes = list(dict.fromkeys([*out.get('notices', []), *notices]))
    if units is None:
        # Old bodies are opaque: never guess line boundaries or split a token.
        from .brief import WARNING
        opaque = body_of(out)
        out['notices'] = codes
        out['text'] = WARNING + '\n' + ('Notices: ' + ', '.join(codes) + '\n' if codes else '') + opaque
        size, tokens = measures(out, count)
        if size <= byte_limit and tokens <= token_limit:
            return out
        out.update(text='', blocks={}, references=[])
        units = []
        codes.append('legacy_body_omitted')
    original = list(units)
    while True:
        out = assemble(out, units, notices_for(units, original, codes))
        size, tokens = measures(out, count)
        if size <= byte_limit and tokens <= token_limit:
            return out
        codes.append('budget_saturated')
        from .brief_coverage import collapse
        if collapse(out):
            codes = list(dict.fromkeys([*codes, *out['notices']]))
            continue
        if units:
            # Drop one whole lowest-priority non-protected unit, never characters.
            removable = [u for u in units if not u.protected]
            victim = (removable or units)[-1]
            units.remove(victim)
            if victim.protected:
                codes.append('critical_omitted')
            continue
        error = compact_error({'code': 'budget_saturated', 'coverage': out.get('coverage')}, wire_format=out.get('wire_format', 'legacy'),
                              budget_tokens=out.get('budget_tokens'), notices=codes)
        if 'critical_omitted' in codes:
            error['notices'] = list(dict.fromkeys([*error.get('notices', []), 'critical_omitted']))
        size, tokens = measures(error, count)
        if size > byte_limit or tokens > token_limit:
            raise ValueError('Minimum brief error exceeds requested budget')
        return error
