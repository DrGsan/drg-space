"""Versioned teams and workspace roles in the encrypted settings store."""
from app.i18n import message as localized_message
import copy
import json
import re

PREFIX = 'space-management:'
AUDIENCE_PREFIX = 'note-audience:'
ROLES = {'viewer': 1, 'commenter': 2, 'editor': 3}


def uid(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{32}', value) is not None


def identity(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_-]{16}', value) is not None


def people(value):
    return isinstance(value, list) and len(value) <= 50 and all(uid(u) for u in value) and len(set(value)) == len(value)


def advanced_available(c, sid):
    from app.space_profiles import templates
    return templates(c).get(sid, 'family') in {'project', 'organization'}


def default():
    return {'version': 1, 'revision': 0, 'advanced': False, 'admins': [], 'teams': {}}


def header(value, keys):
    return (isinstance(value, dict) and set(value) == keys and type(value['version']) is int
            and value['version'] == 1 and type(value['revision']) is int and value['revision'] >= 0)


def validate(value):
    valid = header(value, {'version', 'revision', 'advanced', 'admins', 'teams'})
    if not valid or type(value['advanced']) is not bool or not people(value['admins']) or not isinstance(value['teams'], dict) or len(value['teams']) > 50:
        raise ValueError(localized_message('invalidCommandSettings'))
    for tid, team in value['teams'].items():
        if (not identity(tid) or not isinstance(team, dict) or set(team) != {'name', 'members'}
                or not isinstance(team['name'], str) or not team['name'].strip() or len(team['name']) > 80
                or not people(team['members'])):
            raise ValueError(localized_message('invalidCommand'))
    return value


def validate_audience(value):
    valid = header(value, {'version', 'revision', 'mode', 'teams', 'members'})
    if (not valid or not isinstance(value['mode'], str) or value['mode'] not in {'inherit', 'workspace', 'restricted'}
            or not isinstance(value['teams'], dict) or len(value['teams']) > 50
            or not isinstance(value['members'], dict) or len(value['members']) > 50):
        raise ValueError(localized_message('invalidPageAudience'))
    for field, check in [('teams', identity), ('members', uid)]:
        if any(not check(key) or not isinstance(role, str) or role not in ROLES for key, role in value[field].items()):
            raise ValueError(localized_message('invalidPermission'))
    if value['mode'] != 'restricted' and (value['teams'] or value['members']):
        raise ValueError(localized_message('participantSelectionIsAvailableForThePrivateBranch'))
    return value


def read_all(c, prefix, validator):
    return {r['key'][len(prefix):]: validator(json.loads(r['value']))
            for r in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (prefix + '%',))}


def configs(c):
    return read_all(c, PREFIX, validate)


def audiences(c):
    return read_all(c, AUDIENCE_PREFIX, validate_audience)


def get(c, sid):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (PREFIX + sid,)).fetchone()
    return validate(json.loads(row[0])) if row else default()


def get_audience(c, nid):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (AUDIENCE_PREFIX + nid,)).fetchone()
    return validate_audience(json.loads(row[0])) if row else {'version': 1, 'revision': 0, 'mode': 'inherit', 'teams': {}, 'members': {}}


def invalidate(c, dirty=False):
    # Portable restore also accepts raw sqlite3 connections without attributes.
    if hasattr(c, "__dict__"):
        c.note_access_index = None
        if dirty: c.realtime_dirty = True


def write(c, key, value):
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (key, json.dumps(value, separators=(',', ':'))))
    invalidate(c, dirty=True)


def save(c, sid, value):
    validate(value)
    write(c, PREFIX + sid, value)


def save_audience(c, nid, value):
    validate_audience(value)
    write(c, AUDIENCE_PREFIX + nid, value)


def is_manager(c, sid, user_id):
    row = c.execute('SELECT owner_id,kind FROM spaces WHERE id=?', (sid,)).fetchone()
    return bool(row and row['kind'] == 'family' and c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (sid, user_id)).fetchone()
                and (row['owner_id'] == user_id or user_id in get(c, sid)['admins']))


def role(c, space, user_id, config=None):
    if space['owner_id'] == user_id:
        return 'owner'
    return 'admin' if user_id in (config or get(c, space['id']))['admins'] else 'member'


def purge(c):
    spaces = {r['id']: r for r in c.execute("SELECT id,owner_id FROM spaces WHERE kind='family'")}
    members = {}
    for row in c.execute('SELECT space_id,user_id FROM space_members'):
        members.setdefault(row['space_id'], set()).add(row['user_id'])
    settings = configs(c)
    for sid, value in settings.items():
        if sid not in spaces:
            c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX + sid,))
            continue
        updated = copy.deepcopy(value)
        updated['admins'] = [u for u in value['admins'] if u in members.get(sid, set()) and u != spaces[sid]['owner_id']]
        for team in updated['teams'].values():
            team['members'] = [u for u in team['members'] if u in members.get(sid, set())]
        if updated != value:
            updated['revision'] += 1
            save(c, sid, updated)
            settings[sid] = updated
    pages = {r['id']: r for r in c.execute('SELECT id,space_id FROM notes')}
    for nid, value in audiences(c).items():
        if nid not in pages or pages[nid]['space_id'] not in spaces:
            c.execute('DELETE FROM app_settings WHERE key=?', (AUDIENCE_PREFIX + nid,))
            continue
        sid = pages[nid]['space_id']
        updated = copy.deepcopy(value)
        updated['teams'] = {tid: r for tid, r in value['teams'].items() if tid in settings.get(sid, default())['teams']}
        updated['members'] = {u: r for u, r in value['members'].items() if u in members.get(sid, set())}
        if updated != value:
            updated['revision'] += 1
            save_audience(c, nid, updated)
    invalidate(c)


def validate_archive(c, fail):
    try:
        spaces = {r['id']: r for r in c.execute("SELECT id,owner_id FROM spaces WHERE kind='family'")}
        members = {}
        for row in c.execute('SELECT space_id,user_id FROM space_members'):
            members.setdefault(row['space_id'], set()).add(row['user_id'])
        settings = configs(c)
        for sid, value in settings.items():
            if sid not in spaces or not set(value['admins']) <= members.get(sid, set()) or spaces[sid]['owner_id'] in value['admins']:
                raise ValueError('Invalid administrators')
            if any(not set(t['members']) <= members.get(sid, set()) for t in value['teams'].values()):
                raise ValueError('Invalid team members')
        pages = {r['id']: r for r in c.execute('SELECT id,space_id FROM notes')}
        for nid, value in audiences(c).items():
            sid = pages[nid]['space_id'] if nid in pages else None
            if (sid not in spaces or not set(value['teams']) <= set(settings.get(sid, default())['teams'])
                    or not set(value['members']) <= members.get(sid, set())):
                raise ValueError('Invalid page audience')
    except (ValueError, TypeError):
        fail()
