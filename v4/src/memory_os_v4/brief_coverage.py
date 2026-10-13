"""Exact scoped candidate accounting, recomputed after every whole-unit rebudget."""
from collections import Counter

COLUMNS = ('eligible', 'pool', 'content', 'reference_only', 'omitted', 'outside_pool')


def population(eligible, pool):
    candidates = Counter(unit.kind for unit in pool)
    kinds = sorted(kind for kind in set(eligible) | set(candidates) if eligible.get(kind, 0) or candidates[kind])
    rows = {kind: {'eligible': eligible.get(kind, 0), 'pool': candidates[kind],
                   'content': 0, 'reference_only': 0, 'omitted': candidates[kind]}
            for kind in kinds}
    totals = {key: sum(row.get(key, 0) for row in rows.values()) for key in COLUMNS[:5]}
    totals['outside_pool'] = totals['eligible'] - totals['pool']
    return {'totals': totals, 'by_kind': rows}


def update(coverage, units):
    if not isinstance(coverage, dict) or 'totals' not in coverage:
        return coverage
    raw = coverage['totals']
    totals = dict(zip(COLUMNS, raw)) if isinstance(raw, list) else dict(raw)
    # Only our validated nonnegative bounded counts can survive a relay rebudget.
    if set(totals) != set(COLUMNS) or any(type(v) is not int or not 0 <= v <= 10**12 for v in totals.values()):
        raise ValueError('Invalid brief coverage counts')
    content = Counter(u.kind for u in units if u.representation != 'reference')
    refs = Counter(u.kind for u in units if u.representation == 'reference')
    totals.update(content=sum(content.values()), reference_only=sum(refs.values()))
    totals['omitted'] = totals['pool'] - totals['content'] - totals['reference_only']
    if totals['omitted'] < 0 or totals['outside_pool'] != totals['eligible'] - totals['pool']:
        raise ValueError('Inconsistent brief coverage counts')
    result = {'totals': [totals[key] for key in COLUMNS] if isinstance(raw, list) else totals}
    if 'by_kind' in coverage:
        rows = {}
        for kind, source in coverage['by_kind'].items():
            row = dict(source)
            row.update(content=content[kind], reference_only=refs[kind])
            row['omitted'] = row['pool'] - row['content'] - row['reference_only']
            rows[kind] = row
        result['by_kind'] = rows
    return result


def collapse(data):
    """Remove optional detail first, then compact totals positionally; never lose counts."""
    coverage = data.get('coverage')
    if not coverage:
        return False
    if 'by_kind' in coverage:
        del coverage['by_kind']
    elif isinstance(coverage['totals'], dict):
        coverage['totals'] = [coverage['totals'][key] for key in COLUMNS]
    else:
        return False
    data['notices'] = list(dict.fromkeys([*data.get('notices', []), 'coverage_detail_omitted']))
    return True
