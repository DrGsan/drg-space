"""Private viewer-owned names. Shared profiles and documents are never renamed."""
import json
import re

KEY = '_people_names'
MAX_NAMES = 2000


def valid(value):
    return (isinstance(value, dict) and len(value) <= MAX_NAMES
            and all(isinstance(uid, str) and re.fullmatch(r'[a-f0-9]{32}', uid)
                    and isinstance(name, str) and 0 < len(name) <= 80 and name == name.strip()
                    and not any(ord(char) < 32 or ord(char) == 127 for char in name)
                    for uid, name in value.items()))


def names(settings):
    value = settings.get(KEY, {})
    return value if valid(value) else {}


def display_name(c, viewer, person):
    row = c.execute('SELECT settings FROM users WHERE id=?', (viewer,)).fetchone()
    personal = names(json.loads(row[0])) if row else {}
    return personal.get(person.get('id')) or person.get('display_name') or person.get('login') or 'Удалённый пользователь'


def mention_text(c, viewer, sid, text):
    row = c.execute('SELECT settings FROM users WHERE id=?', (viewer,)).fetchone()
    personal = names(json.loads(row[0])) if row else {}
    aliases = {r['login'].lower(): personal[r['id']] for r in c.execute(
        'SELECT u.id,u.login FROM users u JOIN space_members m ON m.user_id=u.id WHERE m.space_id=?', (sid,)) if r['id'] in personal}
    if not aliases: return text
    pattern = re.compile(r'(?<![\w@])@(' + '|'.join(re.escape(login) for login in sorted(aliases, key=len, reverse=True)) + r')(?![\w@-]|\.[\w.-])', re.I)
    # Links and email addresses keep their literal identities.
    parts = re.split(r'''(https?://[^\s<>"']+)''', text, flags=re.I)
    return ''.join(part if i % 2 else pattern.sub(lambda m: '@' + aliases[m[1].lower()] if m[1].lower() in aliases else m[0], part) for i, part in enumerate(parts))


def purge(c):
    users = {r[0] for r in c.execute('SELECT id FROM users')}
    for row in c.execute('SELECT id,settings FROM users').fetchall():
        settings = json.loads(row['settings'])
        if KEY not in settings: continue
        kept = {uid: name for uid, name in names(settings).items() if uid in users and uid != row['id']}
        if kept == settings[KEY]: continue
        if kept: settings[KEY] = kept
        else: settings.pop(KEY, None)
        c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), row['id']))
