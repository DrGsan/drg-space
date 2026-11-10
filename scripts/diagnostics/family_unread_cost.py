#!/usr/bin/env python3
"""Measure request amplification of the real bridge with an in-memory archive.

No app startup, user data, database, network, or production configuration needed.
This is a cost observation, not a regression budget that preserves current cost.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.modules.family_photo_events import FamilyPhotoEvents


class Archive(FamilyPhotoEvents):
    def __init__(self, count):
        self.groups = [{'id': '-' + str(1000 + i), 'latest_message_id': 100} for i in range(count)]
        self.calls = []
        self.reader_id = 99

    @contextmanager
    def db(self):
        yield SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: None))

    def status(self):
        return {'bot_messaging': True}

    def fetch(self, path):
        self.calls.append(path.split('?', 1)[0])
        return {'items': self.groups} if path == 'v1/groups' else {'unread': 0}


def main():
    cases = []
    for count in [1, 10, 50, 100]:
        archive = Archive(count)
        session = {'user_id': 'a' * 32, 'role': 'owner', 'settings': json.dumps({
            'modules': {'family_photos': True, 'chat': True},
            'family_photo_reads': {g['id']: 100 for g in archive.groups},
        })}
        values = archive.family_counts(session)
        assert len(values) == count and sum(values.values()) == 0
        cases.append({'accessible_groups': count, 'fully_read_groups': count,
                      'http_requests_per_counter_refresh': len(archive.calls),
                      'counter_refresh_seconds': 20,
                      'projected_archive_requests_per_second_at_500_visible_clients': len(archive.calls) * 500 / 20})
    print(json.dumps({'method': 'real FamilyPhotoEvents.family_counts, mocked HTTP transport and empty baseline state',
                      'production_load_generated': False, 'cases': cases}, indent=2))


if __name__ == '__main__':
    main()
