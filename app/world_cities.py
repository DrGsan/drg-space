"""Read-only public GeoNames reference. Never stores account data or queries."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata

from app.process_locks import sync_file_lock

REFERENCE = Path(__file__).parent / 'geodata'


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value).casefold() if not unicodedata.combining(c))


class WorldCities:
    def __init__(self, root):
        self.root = Path(root)
        self.metadata = json.loads((REFERENCE / 'metadata.json').read_text())
        self.directory = self.root / 'public-reference'
        self.path = self.directory / ('cities-' + self.metadata['database_sha256'][:16] + '.sqlite')

    def prepare(self):
        with sync_file_lock(self.root, 'world-cities-reference'):
            if self.directory.is_symlink() or self.path.is_symlink():
                raise OSError('City reference must not be a symbolic link')
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.exists():
                return
            descriptor, filename = tempfile.mkstemp(prefix='.cities-', dir=self.directory)
            temporary = Path(filename)
            try:
                digest, size = hashlib.sha256(), 0
                with os.fdopen(descriptor, 'wb') as output, gzip.open(REFERENCE / 'cities.sqlite.gz', 'rb') as source:
                    while data := source.read(1024 * 1024):
                        size += len(data)
                        if size > self.metadata['uncompressed_bytes']:
                            raise ValueError('City reference exceeds its declared size')
                        digest.update(data); output.write(data)
                    output.flush(); os.fsync(output.fileno())
                if size != self.metadata['uncompressed_bytes'] or digest.hexdigest() != self.metadata['database_sha256']:
                    raise ValueError('Invalid city reference checksum')
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def search(self, query, language='ru'):
        tokens = re.findall(r'[^\W_]+', query, re.UNICODE)
        if not tokens or not 2 <= len(query.strip()) <= 100:
            return []
        expression = '"' + ' '.join(tokens) + '"*'
        self.prepare()
        with sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            # Population makes city namesakes useful without collecting location.
            rows = db.execute('SELECT c.*,r.ru,r.en FROM names JOIN cities c ON c.id=names.rowid LEFT JOIN countries r ON r.code=c.country_code WHERE names MATCH ? ORDER BY c.population DESC LIMIT 100', (expression,)).fetchall()
        needle = normalized(query.strip())
        result = []
        for row in rows:
            aliases = json.loads(row['aliases'])
            exact = next((name for name in aliases if normalized(name) == needle), None)
            prefix = next((name for name in aliases if normalized(name).startswith(needle)), None)
            rank = 0 if exact else 1 if prefix else 2
            name = exact or prefix or row['name']
            result.append((rank, -row['population'], {'name': name, 'country': row['en' if language == 'en' else 'ru'] or row['country_code'],
                           'country_code': row['country_code'], 'region': row['region'], 'latitude': row['latitude'],
                           'longitude': row['longitude'], 'timezone': row['timezone']}))
        result.sort(key=lambda item: item[:2])
        return [row[2] for row in result[:5]]
