"""Current page's local reactions and profiles; never retained between requests."""
import json
import re

from app.notifications import actors
from app.reactions import key, serialize
from .family_photo_events import has_access


class FamilyPhotoPage:
    def __init__(self, c, gid, items, stamp):
        items = [item for item in items if 'media' in item]
        mids = list(dict.fromkeys(item['id'] for item in items))
        space = 'telegram:' + gid
        values = {mid: {'users': {}} for mid in mids}
        for start in range(0, len(mids), 200):
            batch = mids[start:start + 200]
            identities = {key(space, mid): mid for mid in batch}
            marks = ','.join('?' for _ in batch)
            for row in c.execute('SELECT key,value FROM app_settings WHERE key IN (' + marks + ')', tuple(identities)):
                values[identities[row['key']]] = json.loads(row['value'])

        voters = sorted({uid for value in values.values() for uid in value['users']})
        eligible = set()
        for start in range(0, len(voters), 200):
            batch = voters[start:start + 200]
            marks = ','.join('?' for _ in batch)
            for row in c.execute('SELECT id,role,settings FROM users WHERE id IN (' + marks + ')', tuple(batch)):
                settings = json.loads(row['settings'])
                if (has_access(settings, row['role'], gid)
                        and (row['role'] == 'owner' or gid in settings.get('family_photo_access', {}).get('write_groups', []))):
                    eligible.add(row['id'])

        authors = {uid for item in items if (uid := self.author(item)) is not None}
        self.profiles = {profile['id']: profile for profile in actors(c, sorted(authors | eligible), stamp)}
        reaction_profiles = {uid: self.profiles[uid] for uid in eligible}
        self.reactions = {mid: serialize(value, reaction_profiles) for mid, value in values.items()}

    @staticmethod
    def author(item):
        uid = item.get('site_actor_id')
        return uid if isinstance(uid, str) and re.fullmatch('[a-f0-9]{32}', uid) else None

    def decorate(self, item):
        if (uid := self.author(item)) is not None:
            item['site_profile'] = self.profiles[uid]
        item['site_reactions'] = self.reactions[item['id']]
