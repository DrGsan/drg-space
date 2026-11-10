"""Workspace templates, independent of the existing membership/access model.

The legacy ``family`` kind means an owner-managed workspace, while ``friends``
is a bilateral contact. Templates describe managed workspaces; they do not grant
permissions. Missing metadata keeps every existing family a family.
"""
from app.i18n import message as localized_message
import json
from typing import Literal

SpaceTemplate = Literal['family', 'group', 'project', 'organization']
TEMPLATES = {'family', 'group', 'project', 'organization'}
PREFIX = 'space-profile:'


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'template'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['template'], str) or value['template'] not in TEMPLATES):
        raise ValueError(localized_message('invalidWorkspaceTemplate'))
    return value


def templates(c):
    return {row['key'][len(PREFIX):]: validate(json.loads(row['value']))['template']
            for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX + '%',))}


def save(c, sid, template):
    value = validate({'version': 1, 'template': template})
    if template == 'family':
        c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX + sid,))
    else:
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (PREFIX + sid, json.dumps(value, separators=(',', ':'))))


def purge_orphans(c):
    spaces = {row[0] for row in c.execute("SELECT id FROM spaces WHERE kind='family'")}
    for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?', (PREFIX + '%',)).fetchall():
        if row[0][len(PREFIX):] not in spaces:
            c.execute('DELETE FROM app_settings WHERE key=?', (row[0],))


def validate_archive(c, fail):
    try:
        spaces = {row[0] for row in c.execute("SELECT id FROM spaces WHERE kind='family'")}
        if any(sid not in spaces for sid in templates(c)):
            raise ValueError('Missing workspace')
    except (ValueError, TypeError):
        fail()
