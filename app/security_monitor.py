"""Bounded, secret-free security audit and shared emergency policy state.

Audit writes fail independently of authentication. Policy reads/writes deliberately
propagate storage errors: inability to read a shutdown must never reopen the site.
"""
from contextlib import nullcontext
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
from app.data_crypto import StorageError

EVENT_DAYS = 365
INCIDENT_DAYS = 365
MAX_EVENTS = 50000
MAX_INCIDENTS = 5000
MODE_KEY = 'security:service-mode'
HEALTH_KEY = 'security:audit-health'
ALERT_STATE_KEY = 'security:alert-state'
MAX_HOURLY_ALERTS = 10
MAX_HOURLY_NONCRITICAL_ALERTS = 8
LEVELS = {'info': 0, 'warning': 1, 'high': 2, 'critical': 3}
SUBJECT_ACTIONS = {'login', '2fa', 'admin-password', 'admin-confirm', 'security-confirm', 'enroll', 'confirm',
                   'activate', 'disable-2fa', 'change-password', 'passkey-manage',
                   'passkey-register', 'personal-data-password', 'login-success', 'security-control'}
NUMERIC_DETAILS = {'status', 'until', 'duration', 'strikes', 'count', 'revoked'}
TEXT_DETAILS = {'reason', 'stage', 'method', 'mode', 'previous_mode', 'target_type',
                'target_ip', 'target_user_id', 'target_session', 'role', 'previous_role', 'group_id'}
TITLES = {
    'brute_force': 'Серия неудачных попыток с одного IP',
    'target_attack': 'Повторные попытки доступа к одному аккаунту',
    'distributed_attack': 'Попытки доступа с разных IP',
    'factor_failures': 'Пароль принят, повторные ошибки второго фактора',
    'success_after_failures': 'Успешный вход после серии ошибок',
    'refresh_replay': 'Повторное использование токена обновления',
    'access_probe': 'Повторные отказы доступа или сканирование',
    'high_risk': 'Событие безопасности требует проверки',
}


def clean_text(value, length=256):
    return ''.join(ch for ch in str(value or '') if ch.isprintable())[:length]


def safe_route(request):
    """Only a matched route template may expose a URL; never a raw secret path."""
    source = getattr(request, 'source', None)
    rule = getattr(source, 'url_rule', None)
    if rule is not None:
        return re.sub(r'<(?:[^:>]+:)?([^>]+)>', r'{\1}', str(rule))[:180]
    path = getattr(getattr(request, 'url', None), 'path', '')
    # Fixed auth paths also cover synthetic requests. Everything else is opaque.
    if path in {'/api/login', '/api/setup', '/api/register', '/api/logout', '/api/refresh',
                '/api/2fa/verify', '/api/2fa/enroll', '/api/2fa/confirm', '/api/2fa/activate'}:
        return path
    return '/{unmatched}'


def event_dict(row):
    result = dict(row)
    result.pop('fingerprint', None)
    result.pop('subject_key', None)
    try: result['details'] = json.loads(result['details'])
    except (TypeError, ValueError): result['details'] = {}
    return result


def incident_dict(row):
    result = dict(row)
    for key in ('correlation_key', 'subject_key', 'notified_at', 'notified_count'):
        result.pop(key, None)
    return result


def deliver_security_alerts(c, stamp, queued=()):
    """Retry pending alerts with an exact rolling budget and critical reserve."""
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (ALERT_STATE_KEY,)).fetchone()
    try: saved = json.loads(row[0]) if row else {}
    except (TypeError, ValueError): saved = {}
    if not isinstance(saved, dict): saved = {}
    sent = saved.get('sent', [])
    if not isinstance(sent, list): sent = []
    sent = [item for item in sent if isinstance(item, list) and len(item) == 2
            and type(item[0]) is int and stamp-3600 < item[0] <= stamp
            and isinstance(item[1], str) and item[1] in LEVELS][-MAX_HOURLY_ALERTS:]
    pending = saved.get('pending', [])
    if not isinstance(pending, list): pending = []
    pending = {value for value in pending[:MAX_INCIDENTS] if isinstance(value, str)} | set(queued)
    # The incident cap bounds the queue. Reviewed/expired/evicted incidents leave
    # it. Historical incidents restored without this transient queue stay quiet.
    pending_ids = sorted(pending)[:MAX_INCIDENTS]
    active = ([dict(item) for item in c.execute("SELECT id,title,severity,last_seen,notified_count FROM security_incidents WHERE status='open' AND id IN ("+','.join('?' for _ in pending_ids)+') ORDER BY last_seen DESC,id DESC LIMIT ?', pending_ids+[MAX_INCIDENTS])]
              if pending_ids else [])
    pending.intersection_update(item['id'] for item in active)
    candidates = sorted((item for item in active if item['id'] in pending),
                        key=lambda item: (LEVELS.get(item['severity'], 0), item['last_seen']), reverse=True)
    owners = c.execute("SELECT id FROM users WHERE role='owner'").fetchall() if candidates else []
    noncritical = sum(level != 'critical' for _, level in sent)
    for item in candidates:
        if not owners or len(sent) >= MAX_HOURLY_ALERTS: break
        if item['severity'] != 'critical' and noncritical >= MAX_HOURLY_NONCRITICAL_ALERTS: continue
        from app.notifications import notify
        for owner in owners:
            notify(c, owner[0], 'security', dict(incident_id=item['id'], title=item['title'], severity=item['severity']), stamp)
        c.execute('UPDATE security_incidents SET notified_at=?,notified_count=notified_count+1 WHERE id=?', (stamp, item['id']))
        sent.append([stamp, item['severity']])
        noncritical += item['severity'] != 'critical'
        pending.remove(item['id'])
    state = {'sent': sent, 'pending': sorted(pending)}
    if state != saved and (row or sent or pending):
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (ALERT_STATE_KEY, json.dumps(state)))


def purge_security(c, stamp, queued_alerts=()):
    c.execute('DELETE FROM security_events WHERE last_seen<=?', (stamp-EVENT_DAYS*86400,))
    c.execute('DELETE FROM security_incidents WHERE last_seen<=?', (stamp-INCIDENT_DAYS*86400,))
    for table, maximum in (('security_events', MAX_EVENTS), ('security_incidents', MAX_INCIDENTS)):
        excess = c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]-maximum
        if excess > 0:
            c.execute('DELETE FROM '+table+' WHERE id IN (SELECT id FROM '+table+' ORDER BY last_seen,id LIMIT ?)', (excess,))
    deliver_security_alerts(c, stamp, queued_alerts)


class SecurityMonitor:
    def __init__(self, db, now, protection):
        self.db, self.now, self.protection = db, now, protection
        self._key = os.environ.get('APP_SECRET', 'security-monitor-local').encode()
        self._health_lock = threading.Lock()
        self._failed_writes = 0
        self._last_failure = 0
        self._last_success = 0
        self._last_purge = 0

    def key(self, *values):
        return hmac.new(self._key, json.dumps(values, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()

    def subject_key(self, subject):
        return self.key('subject', clean_text(subject, 100).strip().casefold()) if subject else ''

    def get_mode(self, connection=None):
        with nullcontext(connection) if connection is not None else self.db() as c:
            row = c.execute('SELECT value FROM app_settings WHERE key=?', (MODE_KEY,)).fetchone()
        if not row:
            return dict(mode='normal', until=0, reason='', updated=0, allowed_session='')
        try: state = json.loads(row[0])
        except (ValueError, TypeError) as error: raise StorageError('Invalid security service policy') from error
        if (not isinstance(state, dict) or state.get('mode') not in ('normal', 'logins_closed', 'lockdown')
                or type(state.get('until')) is not int or state['until'] < 0
                or not isinstance(state.get('allowed_session'), str)
                or type(state.get('updated')) is not int or state['updated'] < 0
                or not isinstance(state.get('reason'), str)
                or state['mode'] != 'normal' and not re.fullmatch(r'[a-f0-9]{64}', state['allowed_session'])):
            raise StorageError('Invalid security service policy')
        if state['until'] and state['until'] <= self.now():
            return dict(mode='normal', until=0, reason='', updated=state.get('updated', 0), allowed_session='')
        return state

    @staticmethod
    def public_mode(state):
        return {key: state.get(key) for key in ('mode', 'until', 'reason', 'updated')}

    def set_mode(self, mode, session=None, duration=0, reason='', connection=None):
        if mode not in ('normal', 'logins_closed', 'lockdown') or type(duration) is not int or not 0 <= duration <= 7*86400:
            raise ValueError('Invalid security service policy')
        if mode != 'normal' and (not session or session.get('role') != 'owner'):
            raise ValueError('An owner session is required')
        state = dict(mode=mode, until=self.now()+duration if duration and mode != 'normal' else 0,
                     reason=clean_text(reason, 300), updated=self.now(),
                     allowed_session=session['token'] if mode != 'normal' else '')
        with nullcontext(connection) if connection is not None else self.db() as c:
            if connection is None: c.execute('BEGIN IMMEDIATE')
            if session and not c.execute("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND s.stage='full' AND u.role='owner'", (session['token'], self.now())).fetchone():
                raise ValueError('Owner session expired')
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (MODE_KEY, json.dumps(state)))
        return state

    def health(self, connection=None):
        with nullcontext(connection) if connection is not None else self.db() as c:
            row = c.execute('SELECT value FROM app_settings WHERE key=?', (HEALTH_KEY,)).fetchone()
        try: stored = json.loads(row[0]) if row else {}
        except (TypeError, ValueError): stored = {}
        with self._health_lock:
            failure = max(self._last_failure, stored.get('last_failure', 0))
            success = max(self._last_success, stored.get('last_success', 0))
            return dict(ok=not failure or success > failure, last_failure=failure,
                        last_success=success, failed_writes=max(self._failed_writes, stored.get('failed_writes', 0)))

    def _failed(self, stamp, error):
        with self._health_lock:
            self._failed_writes += 1
            self._last_failure = stamp
        # Exception text can contain SQL/parameters; only the exception type is safe.
        logging.getLogger(__name__).error('Security audit write failed (%s)', type(error).__name__)

    def record(self, request, event, action='', subject='', user_id='', outcome='failure', severity='info', details=None, connection=None):
        stamp = self.now()
        if request is not None:
            request._security_recorded = True
            actor = getattr(request, '_security_actor', {})
            if not user_id and not subject and isinstance(actor, dict): user_id = actor.get('user_id', '')
        try:
            with nullcontext(connection) if connection is not None else self.db() as c:
                if connection is None: c.execute('BEGIN IMMEDIATE')
                # Fixed transaction-control statements contain no user values and
                # no encrypted columns. SQLGlot's SQLite-to-Postgres parser does
                # not support SAVEPOINT/RELEASE reliably, so use the DB-API handle.
                else: getattr(c, 'raw', c).execute('SAVEPOINT security_audit')
                try:
                    result = self._record(c, request, event, action, subject, user_id, outcome, severity, details, stamp)
                    if connection is not None: getattr(c, 'raw', c).execute('RELEASE SAVEPOINT security_audit')
                except Exception:
                    if connection is not None:
                        getattr(c, 'raw', c).execute('ROLLBACK TO SAVEPOINT security_audit')
                        getattr(c, 'raw', c).execute('RELEASE SAVEPOINT security_audit')
                    raise
            with self._health_lock: self._last_success = stamp
            return result
        except Exception as error:
            self._failed(stamp, error)
            return None

    def _record(self, c, request, event, action, subject, user_id, outcome, severity, details, stamp):
        event = clean_text(event, 60)
        action = clean_text(action, 60)
        if not re.fullmatch(r'[a-z0-9_-]{1,60}', event): event = 'security_event'
        if not re.fullmatch(r'[a-z0-9_-]{0,60}', action): action = ''
        outcome = outcome if outcome in ('failure', 'success', 'blocked', 'info') else 'info'
        severity = severity if severity in LEVELS else 'info'
        # Publication/reset/invitation credentials must not become searchable logs.
        subject = clean_text(subject, 100).strip() if action in SUBJECT_ACTIONS else ''
        user_id = clean_text(user_id, 64) if re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', str(user_id)) else ''
        if user_id:
            user = c.execute('SELECT login FROM users WHERE id=?', (user_id,)).fetchone()
            if user: subject = user[0]
        elif subject and action != 'login':
            user = c.execute('SELECT id,login FROM users WHERE id=?', (subject,)).fetchone()
            if user: user_id, subject = user['id'], user['login']
        elif subject:
            user = c.execute('SELECT id FROM users WHERE login=?', (subject.lower(),)).fetchone()
            if user: user_id = user[0]
        subject_key = self.subject_key(subject)
        ip = clean_text(self.protection.ip(request), 64) if request is not None else 'local'
        route = safe_route(request)
        agent = clean_text(request.headers.get('user-agent', ''), 256) if request is not None else 'local'
        safe_details = {}
        for key, value in (details or {}).items():
            if key in NUMERIC_DETAILS and type(value) is int: safe_details[key] = value
            elif key in TEXT_DETAILS and isinstance(value, str): safe_details[key] = clean_text(value, 300)
        bucket = secrets.token_hex(16) if event in ('admin_action', 'account_security_changed', 'site_role_changed') else stamp//60
        fingerprint = self.key('event', bucket, event, action, outcome, ip, subject_key, user_id, route)
        existing = c.execute('SELECT id,severity FROM security_events WHERE fingerprint=?', (fingerprint,)).fetchone()
        event_id = existing[0] if existing else secrets.token_hex(16)
        if existing:
            level = severity if LEVELS[severity] > LEVELS[existing['severity']] else existing['severity']
            c.execute('UPDATE security_events SET last_seen=?,severity=?,count=count+1 WHERE id=?', (stamp, level, event_id))
        else:
            c.execute('INSERT INTO security_events(id,fingerprint,event,action,outcome,severity,ip,subject,subject_key,user_id,route,agent,details,first_seen,last_seen,count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (event_id, fingerprint, event, action, outcome, severity, ip, subject, subject_key, user_id, route, agent, json.dumps(safe_details, ensure_ascii=False), stamp, stamp, 1))
        candidates = []
        if event == 'auth_failure':
            stats = c.execute("SELECT COALESCE(SUM(count),0),MIN(first_seen) FROM security_events WHERE ip=? AND last_seen>? AND event='auth_failure'", (ip, stamp-900)).fetchone()
            if stats[0] >= 8:
                candidates.append(('brute_force', self.key('ip', ip), 'high' if stats[0] >= 30 else 'warning', stats[0], 1, stats[1]))
            if subject_key and event == 'auth_failure':
                stats = c.execute("SELECT COALESCE(SUM(count),0),COUNT(DISTINCT ip),MIN(first_seen) FROM security_events WHERE subject_key=? AND event='auth_failure' AND last_seen>?", (subject_key, stamp-86400)).fetchone()
                if stats[0] >= 12 and stats[1] >= 3:
                    candidates.append(('distributed_attack', self.key('distributed', subject_key), 'high', stats[0], stats[1], stats[2]))
                recent = c.execute("SELECT COALESCE(SUM(count),0),COUNT(DISTINCT ip),MIN(first_seen) FROM security_events WHERE subject_key=? AND event='auth_failure' AND last_seen>?", (subject_key, stamp-900)).fetchone()
                if recent[0] >= 8:
                    candidates.append(('target_attack', self.key('target', subject_key), 'warning', recent[0], recent[1], recent[2]))
                if action == '2fa':
                    factor = c.execute("SELECT COALESCE(SUM(count),0),COUNT(DISTINCT ip),MIN(first_seen) FROM security_events WHERE subject_key=? AND action='2fa' AND event='auth_failure' AND last_seen>?", (subject_key, stamp-900)).fetchone()
                    if factor[0] >= 3:
                        candidates.append(('factor_failures', self.key('factor', subject_key), 'high', factor[0], factor[1], factor[2]))
        if event in ('access_denied', 'csrf_rejected', 'scan_detected', 'origin_rejected'):
            stats = c.execute("SELECT COALESCE(SUM(count),0),MIN(first_seen) FROM security_events WHERE ip=? AND last_seen>? AND event IN ('access_denied','csrf_rejected','scan_detected','origin_rejected')", (ip, stamp-900)).fetchone()
            if stats[0] >= 10:
                candidates.append(('access_probe', self.key('probe', ip), 'warning', stats[0], 1, stats[1] or stamp))
        if event == 'login_success' and subject_key:
            stats = c.execute("SELECT COALESCE(SUM(count),0),COUNT(DISTINCT ip),MIN(first_seen) FROM security_events WHERE subject_key=? AND event='auth_failure' AND last_seen>?", (subject_key, stamp-86400)).fetchone()
            if stats[0] >= 5:
                candidates.append(('success_after_failures', self.key('success', subject_key), 'high', stats[0]+1, stats[1], stats[2]))
        if event == 'refresh_replay':
            risk = c.execute("SELECT COALESCE(SUM(count),0),MIN(first_seen) FROM security_events WHERE ip=? AND subject_key=? AND event='refresh_replay' AND last_seen>=?", (ip, subject_key, stamp//86400*86400)).fetchone()
            candidates.append(('refresh_replay', self.key('replay', ip, subject_key), 'critical', risk[0], 1, risk[1] or stamp))
        elif severity in ('high', 'critical') and not candidates:
            risk = c.execute("SELECT COALESCE(SUM(count),0),MIN(first_seen) FROM security_events WHERE ip=? AND subject_key=? AND event=? AND action=? AND severity IN ('high','critical') AND last_seen>=?", (ip, subject_key, event, action, stamp//86400*86400)).fetchone()
            candidates.append(('high_risk', self.key('risk', event, action, ip, subject_key), severity, risk[0], 1, risk[1] or stamp))
        # A single event can contribute to both an address and an account incident.
        queued_alerts = []
        for kind, correlation, level, count, addresses, first in candidates:
            queued = self._incident(c, event_id, kind, correlation, level, ip, subject, subject_key, user_id, count, addresses, first or stamp, stamp, event, action)
            if queued: queued_alerts.append(queued)
        if stamp-self._last_purge >= 60 or not existing:
            purge_security(c, stamp, queued_alerts)
            self._last_purge = stamp
        else:
            deliver_security_alerts(c, stamp, queued_alerts)
        if self._failed_writes:
            health = dict(last_failure=self._last_failure, last_success=stamp, failed_writes=self._failed_writes)
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (HEALTH_KEY, json.dumps(health)))
        return event_id

    def _incident(self, c, event_id, kind, correlation, severity, ip, subject, subject_key, user_id, count, addresses, first, stamp, event, action):
        # Daily grouping bounds repeat notifications; reviewed incidents reopen only
        # when their severity increases, not on every identical blocked request.
        correlation = self.key(correlation, stamp//86400)
        previous = c.execute('SELECT * FROM security_incidents WHERE correlation_key=?', (correlation,)).fetchone()
        incident_id = previous['id'] if previous else secrets.token_hex(16)
        escalating = previous is not None and LEVELS[severity] > LEVELS[previous['severity']]
        # Detection uses rolling windows, but the displayed incident total spans
        # its full lifetime. SUM(count) counts each aggregated event once, including
        # separate bursts that fall outside the detection window.
        start = previous['first_seen'] if previous else first
        if kind in ('target_attack', 'distributed_attack', 'factor_failures', 'success_after_failures'):
            where, values = 'subject_key=?', [subject_key]
        else:
            where, values = 'ip=?', [ip]
        if kind in ('brute_force', 'target_attack', 'distributed_attack', 'factor_failures'):
            where += " AND event='auth_failure'"
            if kind == 'factor_failures': where += " AND action='2fa'"
        elif kind == 'success_after_failures':
            where += " AND event IN ('auth_failure','login_success')"
        elif kind == 'access_probe':
            where += " AND event IN ('access_denied','csrf_rejected','scan_detected','origin_rejected')"
        else:
            where += ' AND subject_key=? AND event=? AND action=?'
            values += [subject_key, event, action]
            if kind == 'high_risk': where += " AND severity IN ('high','critical')"
        totals = c.execute('SELECT COALESCE(SUM(count),0),COUNT(DISTINCT ip) FROM security_events WHERE '+where+' AND last_seen>=? AND first_seen<=?', values+[start, stamp]).fetchone()
        count, addresses = totals[0], totals[1]
        if previous:
            severity = severity if escalating else previous['severity']
            # Retention may already have evicted evidence during a flood. Keep
            # the known total and add this new contribution even in that case.
            count = max(count, previous['event_count']+1)
            addresses = max(addresses, previous['ip_count'])
            c.execute('UPDATE security_incidents SET severity=?,status=?,last_seen=?,event_count=?,ip_count=? WHERE id=?',
                      (severity, 'open' if escalating else previous['status'], stamp, count, addresses, incident_id))
        else:
            c.execute('INSERT INTO security_incidents(id,correlation_key,kind,title,severity,status,ip,subject,subject_key,user_id,first_seen,last_seen,event_count,ip_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (incident_id, correlation, kind, TITLES[kind], severity, 'open', ip, subject, subject_key, user_id, first, stamp, count, addresses))
        c.execute('UPDATE security_events SET incident_id=? WHERE id=?', (incident_id, event_id))
        return incident_id if previous is None or escalating or not previous['notified_count'] else None
