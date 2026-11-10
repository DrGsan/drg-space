"""Personal conversation alerts, stored in the existing encrypted user settings."""
import json

KEY = 'chat_notification_mutes'
MUTE_DURATIONS = (3600, 28800, 86400, 604800)


def mute_until(settings, sid, stamp):
    values = settings.get(KEY, {})
    value = values.get(sid, 0) if isinstance(values, dict) else 0
    if type(value) is not int:
        return 0
    return value if value > stamp or value == -1 and sid.startswith('telegram:') else 0


def muted(c, uid, sid, stamp):
    if not isinstance(sid, str):
        return False
    row = c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()
    return bool(row and mute_until(json.loads(row[0]), sid, stamp))
