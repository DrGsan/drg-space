#!/usr/bin/env python3
"""Measure real presence payload SQL on disposable encrypted SQLite, offline.

Counts exclude authentication and pool reset queries. Timings are local test
observations, not PostgreSQL/production latency or a multi-user load test.
"""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.data_crypto import DataCipher
from app.database import Database
from app.notifications import actors


class Counted:
    def __init__(self, connection):
        self.connection = connection
        self.queries = 0

    def execute(self, *args, **kwargs):
        self.queries += 1
        return self.connection.execute(*args, **kwargs)


def main():
    with TemporaryDirectory(prefix='drg-presence-audit-') as root:
        backend = Database(root, url='', cipher=DataCipher.ephemeral(), redis_url='')
        backend.initialize()
        stamp = int(time.time())
        with backend.connection() as c:
            for n in range(500):
                uid = f'{n:032x}'
                c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
                          (uid, 'audit-' + str(n), 'test-only', 'user', json.dumps({'profile': {'display_name': 'Audit'}}), stamp))
                c.execute('INSERT INTO user_presence(user_id,last_seen) VALUES(?,?)', (uid, stamp - 10))
        cases = []
        for count in [1, 10, 100, 500]:
            with backend.connection() as raw:
                c = Counted(raw)
                ids = [row[0] for row in c.execute('SELECT id FROM users ORDER BY id LIMIT ?', (count,))]
                started = time.perf_counter()
                people = actors(c, ids, stamp)
                elapsed = time.perf_counter() - started
                assert len(people) == count and all(not person['online'] for person in people)
                cases.append({'people': count, 'payload_sql_statements': c.queries,
                              'local_encrypted_sqlite_payload_ms': round(elapsed * 1000, 2),
                              'refresh_seconds': 5,
                              'projected_payload_sql_per_second_at_500_clients': c.queries * 500 / 5})
        print(json.dumps({'method': 'real batched presence rendering, recent presence and no full sessions; isolated encrypted data',
                          'production_load_generated': False, 'cases': cases}, indent=2))


if __name__ == '__main__':
    main()
