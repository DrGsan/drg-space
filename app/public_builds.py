"""Bounded history of public, immutable frontend builds, never private files.

Keep whole dependency graphs, deduplicate their files, and retire a build when
its replacement starts (not seven days after the original deployment).
"""
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


TTL = 7 * 86400
LIMIT = 256 * 1024 * 1024
MAX_BUILDS = 64


def public_name(name):
    # The build manifest emits flat, content-addressed filenames.
    return isinstance(name, str) and len(name) <= 240 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) is not None


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


class PublicBuilds:
    def __init__(self, root, *, now=time.time, ttl=TTL, limit=LIMIT):
        self.root = Path(root)
        self.objects = self.root / 'objects'
        self.index = self.root / 'index.json'
        self.now, self.ttl, self.limit = now, ttl, limit
        for directory in (self.root, self.objects):
            if directory.is_symlink():
                raise ValueError('Public build cache must not use symlinks')
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._signature = None
        self._snapshot = None

    @contextmanager
    def lock(self, *, shared=False):
        if self.root.is_symlink() or self.objects.is_symlink():
            raise ValueError('Public build cache must not use symlinks')
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def load(self, *, mutable=False):
        empty = {'format': 1, 'current': None, 'builds': {}, 'files': {}}
        try:
            stat = self.index.stat(follow_symlinks=False)
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if signature == self._signature and self._snapshot is not None:
                return json.loads(json.dumps(self._snapshot)) if mutable else self._snapshot
            if stat.st_size > 4 * 1024 * 1024:
                return empty
            fd = os.open(self.index, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as file:
                data = json.load(file)
            if not (data['format'] == 1 and isinstance(data['files'], dict) and isinstance(data['builds'], dict)
                    and len(data['files']) <= 20000 and len(data['builds']) <= MAX_BUILDS
                    and (data['current'] is None or data['current'] in data['builds'])):
                raise ValueError('Invalid public build index')
            for name, item in data['files'].items():
                if not (public_name(name) and type(item['size']) is int and item['size'] >= 0
                        and re.fullmatch(r'[a-f0-9]{64}', item['digest'])):
                    raise ValueError('Invalid public build index')
            for key, build in data['builds'].items():
                if not (re.fullmatch(r'[a-f0-9]{64}', key) and isinstance(build['names'], list)
                        and all(n in data['files'] for n in build['names'])):
                    raise ValueError('Invalid public build index')
                retired = build['retired']
                if not ((key == data['current'] and retired is None) or
                        (type(retired) in (int, float) and math.isfinite(retired) and retired >= 0)):
                    raise ValueError('Invalid public build index')
            self._signature, self._snapshot = signature, data
            return json.loads(json.dumps(data)) if mutable else data
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return empty

    def save(self, data):
        fd, name = tempfile.mkstemp(prefix='.index-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump(data, file, separators=(',', ':'))
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.index)
            self._signature = None
        finally:
            Path(name).unlink(missing_ok=True)

    def publish(self, static, *, promote=True):
        """Promote the installed build, or seed a trusted prior image once."""
        static = Path(static)
        if static.is_symlink() or (static / 'assets').is_symlink():
            raise ValueError('Packaged public assets must not use symlinks')
        manifest = json.loads((static / 'asset-manifest.json').read_text())
        names = sorted(set(manifest['immutable']))
        if len(names) > 10000 or not all(public_name(n) for n in names):
            raise ValueError('Invalid public build manifest')
        records = {}
        for name in names:
            path = static / 'assets' / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Only regular packaged public assets can be retained')
            records[name] = {'size': path.stat().st_size, 'digest': digest(path)}
        if sum(r['size'] for r in records.values()) > self.limit:
            raise ValueError('Public build exceeds cache budget')
        key = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
        with self.lock():
            data = self.load(mutable=True)
            for name, record in records.items():
                path = self.objects / name
                if path.is_symlink():
                    raise ValueError('Public cached asset must not use symlinks')
                if name in data['files'] and data['files'][name]['digest'] != record['digest']:
                    raise ValueError('Immutable public filename collision')
                if not path.is_file() or path.stat().st_size != record['size'] or digest(path) != record['digest']:
                    fd, temporary = tempfile.mkstemp(prefix='.asset-', dir=self.objects)
                    try:
                        with os.fdopen(fd, 'wb') as target, (static / 'assets' / name).open('rb') as source:
                            while chunk := source.read(1024 * 1024):
                                target.write(chunk)
                            target.flush()
                            os.fsync(target.fileno())
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                data['files'][name] = record
            stamp = self.now()
            if promote and data['current'] != key:
                previous = data['builds'].get(data['current'])
                if previous:
                    previous['retired'] = stamp
                data['current'] = key
            if promote or key not in data['builds']:
                data['builds'][key] = {'names': names, 'retired': None if key == data['current'] else stamp}
            self.compact(data, stamp)
            self.save(data)
            self.sweep(data)

    def compact(self, data, stamp):
        builds = data['builds']
        for key, build in list(builds.items()):
            if key != data['current'] and stamp - build['retired'] >= self.ttl:
                del builds[key]
        def used():
            return {name for build in builds.values() for name in build['names']}
        oldest = sorted((key for key in builds if key != data['current']), key=lambda k: builds[k]['retired'])
        while oldest and (len(builds) > MAX_BUILDS or sum(data['files'][n]['size'] for n in used()) > self.limit):
            del builds[oldest.pop(0)]
        data['files'] = {name: data['files'][name] for name in used()}

    def sweep(self, data):
        # Own cache only. A crashed writer's unregistered files are also removed.
        for path in self.objects.iterdir():
            if path.name not in data['files'] and (path.is_file() or path.is_symlink()):
                path.unlink(missing_ok=True)
        for path in self.root.glob('.index-*'):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)

    def prune(self):
        with self.lock():
            data = self.load(mutable=True)
            self.compact(data, self.now())
            self.save(data)
            self.sweep(data)

    def open(self, name):
        if not public_name(name):
            return None
        with self.lock(shared=True):
            data = self.load()
            valid = any(name in b['names'] and (k == data['current'] or self.now() - b['retired'] < self.ttl)
                        for k, b in data['builds'].items())
            if not valid:
                return None
            try:
                fd = os.open(self.objects / name, os.O_RDONLY | os.O_NOFOLLOW)
            except OSError:
                return None
            record = data['files'][name]
            status = os.fstat(fd)
            if not stat.S_ISREG(status.st_mode) or status.st_size != record['size']:
                os.close(fd)
                return None
            file = os.fdopen(fd, 'rb')
            # An open descriptor survives a concurrent expiry/eviction sweep.
            return file, record


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Seed public immutable assets from trusted previous application images.')
    parser.add_argument('--cache-dir', required=True)
    parser.add_argument('--seed-root', required=True, help='Directory containing previous static/ directories, each with its own manifest.')
    args = parser.parse_args()
    store = PublicBuilds(args.cache_dir)
    for source in sorted(Path(args.seed_root).iterdir()):
        if source.is_dir():
            store.publish(source, promote=False)
    data = store.load()
    print(json.dumps({'retained_builds': len(data['builds']), 'public_files': len(data['files']),
                      'bytes': sum(item['size'] for item in data['files'].values())}))
