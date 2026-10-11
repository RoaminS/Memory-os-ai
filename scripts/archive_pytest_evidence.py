#!/usr/bin/env python3
"""Lossless content archiving of one attested finished pytest directory.

Simulation by default; source never removed. gzip is available without installing
zstd. No restore/extraction, sweep, cleanup or production-store access.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile
import time
import uuid

from source_release_checks import resources

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'pytest-tmp', 'v3-tmp', 'v4-tmp', 'v3-tests', 'v4-tests',
           'source_tcp-tests', 'cached_semantic-tests', 'v3_cached-tests'}


def sha(path, *, guard=None):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            if guard is not None:
                guard()
            digest.update(chunk)
    return digest.hexdigest()


def inventory(source, *, hash_files=True, guard=None):
    result, links = {}, {}
    total = 0
    for directory, dirs, files in os.walk(source, followlinks=False):
        paths = [Path(directory)] + [Path(directory) / p for p in files]
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                paths.append(path)
                dirs.remove(name)
        for path in paths:
            if guard is not None:
                guard()
            st = path.lstat()
            kind = 'file' if stat.S_ISREG(st.st_mode) else 'dir' if stat.S_ISDIR(st.st_mode) else 'symlink' if stat.S_ISLNK(st.st_mode) else None
            if kind is None:
                raise ValueError('Special files require a separate preservation procedure')
            if (getattr(st, 'st_flags', 0) or
                    (hasattr(os, 'listxattr') and os.listxattr(path, follow_symlinks=False))):
                raise ValueError('Extended metadata refused; no silent metadata loss')
            row = {'kind': kind, 'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid,
                   'gid': st.st_gid, 'mtime_ns': st.st_mtime_ns}
            if kind == 'file':
                total += st.st_size
                if total > 512_000_000:
                    raise ValueError('One archive is capped at 512 MB uncompressed')
                row.update(size=st.st_size, sha256=sha(path, guard=guard) if hash_files else None)
                if st.st_nlink > 1:
                    group = links.setdefault((st.st_dev, st.st_ino), {'count': st.st_nlink, 'paths': []})
                    group['paths'].append(path.relative_to(source).as_posix())
            elif kind == 'symlink':
                row['linkname'] = os.readlink(path)  # Never read the target.
            result[path.relative_to(source).as_posix()] = row
            if len(result) > 50_000:
                raise ValueError('Inventory exceeds 50,000 entries')
    for group in links.values():
        if group['count'] != len(group['paths']):
            raise ValueError('Hardlink outside selected source: separate preservation required')
        names = sorted(group['paths'])
        for name in names[1:]:
            result[name]['hardlink_to'] = names[0]
    if sum(r.get('size', 0) for r in result.values()) > 512_000_000:
        raise ValueError('One archive is capped at 512 MB uncompressed')
    return result


def verify(archive, expected, *, guard=None):
    seen, verified_files = set(), {}
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            if guard is not None:
                guard()
            rel = '.' if member.name == 'evidence' else member.name.removeprefix('evidence/')
            if rel in seen or rel not in expected or (rel != '.' and member.name != 'evidence/' + rel):
                raise ValueError('Unexpected or duplicate member')
            seen.add(rel)
            kind = 'file' if member.isfile() or member.islnk() else 'dir' if member.isdir() else 'symlink' if member.issym() else None
            row = expected[rel]
            actual = {'kind': kind, 'mode': member.mode, 'uid': member.uid, 'gid': member.gid,
                      'mtime_ns': int(Decimal(member.pax_headers['mtime']) * 1_000_000_000)}
            if member.islnk():
                target = member.linkname.removeprefix('evidence/')
                if member.linkname != 'evidence/' + target or target not in verified_files:
                    raise ValueError('Invalid or forward hardlink')
                actual.update(verified_files[target], hardlink_to=target)
            elif kind == 'file':
                stream = tar.extractfile(member)
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    if guard is not None:
                        guard()
                    digest.update(chunk)
                actual.update(size=member.size, sha256=digest.hexdigest())
            elif kind == 'symlink':
                actual['linkname'] = member.linkname
            if actual != row:
                raise ValueError('Archive content/metadata differs: ' + rel)
            if kind == 'file':
                verified_files[rel] = {k: actual[k] for k in ('size', 'sha256')}
    if seen != set(expected):
        raise ValueError('Missing members')


def write_exclusive(path, text):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def archive(source, finished_report, output, *, apply=False, attest_finished=False):
    source, finished_report, output = Path(source), Path(finished_report), Path(output)
    private = (ROOT / '.local-qualification').resolve()
    if source.is_symlink() or not source.is_dir() or source.name not in ALLOWED:
        raise ValueError('Only a named pytest basetemp directory is eligible')
    source, finished_report = source.resolve(), finished_report.resolve()
    if not source.is_relative_to(private) or not source.is_relative_to(finished_report.parent):
        raise ValueError('Source and terminal report must belong to this clone/run')
    terminal = json.loads(finished_report.read_text())
    if not isinstance(terminal.get('ok'), bool):
        raise ValueError('Terminal report with boolean ok required, including failed runs')
    if not output.parent.resolve().is_relative_to(private) or output.suffixes[-2:] != ['.tar', '.gz']:
        raise ValueError('Archive output must be .tar.gz inside private qualification directory')
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(source):
        raise ValueError('Fresh, disjoint archive required')
    if apply:
        if not attest_finished:
            raise ValueError('Operator must attest no process can write this finished run')
        if os.getpriority(os.PRIO_PROCESS, 0) < 15:
            raise ValueError('Run archival via nice -n 15, concurrency 1')
        resources(ROOT)  # Before hashing/compression, including an already-low disk.
    last_check = time.monotonic()

    def guarded_sample():
        nonlocal last_check
        if time.monotonic() - last_check >= 1:
            resources(ROOT)
            last_check = time.monotonic()

    before = inventory(source, hash_files=apply, guard=guarded_sample if apply else None)
    report_sha = sha(finished_report)
    plan = {'source': str(source), 'finished_report_sha256': report_sha, 'source_preserved': True,
            'mode': 'simulation', 'source_bytes': sum(r.get('size', 0) for r in before.values()),
            'entries': len(before), 'disk_bytes_reclaimed': 0, 'verified': False}
    if not apply:
        return plan
    sample = resources(ROOT)
    bound = plan['source_bytes'] + 8192 * len(before) + 1024 * 1024
    if sample['free_bytes'] < 5_000_000_000 + bound:
        raise ValueError('Insufficient temporary space above the 5 GB reserve')
    partial = output.with_name(output.name + '.partial-' + uuid.uuid4().hex)
    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)

    class GuardedReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size):
            guarded_sample()
            return self.stream.read(size)

    # Partial output is kept on failure. No source cleanup exists in this script.
    with os.fdopen(fd, 'wb') as stream:
        with tarfile.open(fileobj=stream, mode='w|gz', dereference=False) as tar:
            for rel, row in sorted(before.items()):
                path = source if rel == '.' else source / rel
                info = tar.gettarinfo(str(path), 'evidence' if rel == '.' else 'evidence/' + rel)
                info.pax_headers['mtime'] = format(Decimal(row['mtime_ns']) / 1_000_000_000, '.9f')
                if info.isfile():
                    with path.open('rb') as data:
                        tar.addfile(info, GuardedReader(data))
                else:
                    tar.addfile(info)
        stream.flush()
        os.fsync(stream.fileno())
    resources(ROOT)
    verify(partial, before, guard=guarded_sample)
    if inventory(source, guard=guarded_sample) != before or sha(finished_report) != report_sha:
        raise ValueError('Source/report changed; publication refused, partial kept')
    manifest = {**plan, 'mode': 'archive-only', 'verified': True, 'files': before,
                'archive_sha256': sha(partial, guard=guarded_sample), 'partial_archive': str(partial)}
    # Exclusive hard-link publication: source and completed partial both retained.
    os.link(partial, output)
    write_exclusive(Path(str(output) + '.manifest.json'), json.dumps(manifest, indent=2) + '\n')
    write_exclusive(Path(str(output) + '.sha256'), manifest['archive_sha256'] + '  ' + output.name + '\n')
    fd = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {k: v for k, v in manifest.items() if k != 'files'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--finished-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--attest-finished', action='store_true')
    args = parser.parse_args()
    print(json.dumps(archive(args.source, args.finished_report, args.output,
                             apply=args.apply, attest_finished=args.attest_finished)))


if __name__ == '__main__':
    main()
