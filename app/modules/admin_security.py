"""Owner-only security center; every operation requires the admin unlock gate."""
from app.i18n import message as localized_message
import ipaddress
import json
from app.auth_tokens import session_device
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.http import Router, Request, Query, HTTPError, JSONResponse
from app.security_monitor import (EVENT_DAYS, INCIDENT_DAYS, MAX_EVENTS, MAX_INCIDENTS,
                                  event_dict, incident_dict)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(default='', max_length=100)
    reason: str = Field(default='', max_length=300)


class Ban(Confirmation):
    ip: str = Field(min_length=2, max_length=64)
    duration: int = Field(default=3600, ge=60, le=30*86400)


class Mode(Confirmation):
    mode: Literal['normal', 'logins_closed', 'lockdown']
    duration: int = Field(default=0, ge=0, le=7*86400)


class Review(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['open', 'reviewed']


def router(monitor, admin_access, tokens, protection, strong_confirm):
    routes = Router()
    db, now = monitor.db, monitor.now

    def access(request, write=False):
        session = admin_access.proof(request) if write else admin_access.require(request)
        if session.get('role') != 'owner':
            raise HTTPError(403, localized_message('availableOnlyToPortalAdministrator'))
        return session

    def confirm(request, data):
        access(request, True)
        session = strong_confirm(request, data.password, data.code)
        if session.get('role') != 'owner':
            raise HTTPError(403, localized_message('availableOnlyToPortalAdministrator'))
        return session

    def live(c, session):
        if not c.execute("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.user_id=? AND s.stage='full' AND s.expires>? AND u.role='owner'", (session['token'], session['user_id'], now())).fetchone():
            raise HTTPError(401, localized_message('sessionHasBeenRevokedOrExpired'))
        # Confirmation can finish before another owner session enables lockdown.
        # Recheck in the same serialized write transaction as every control so a
        # previously authorized request cannot override the designated session.
        state = monitor.get_mode(c)
        if state['mode'] == 'lockdown' and state.get('allowed_session') != session['token']:
            raise HTTPError(503, localized_message('theServiceIsTemporarilyClosedByTheAdministrator'),
                            {'Retry-After': '60', 'X-Service-Mode': 'lockdown'})

    def audit(request, session, action, **details):
        monitor.record(request, 'admin_action', action=action, user_id=session['user_id'], outcome='success', details=details)

    def policy():
        return dict(event_days=EVENT_DAYS, incident_days=INCIDENT_DAYS,
                    max_events=MAX_EVENTS, max_incidents=MAX_INCIDENTS,
                    failure_limit=protection.limit, ip_failure_limit=protection.ip_limit,
                    window=protection.window, ban_seconds=protection.ban_seconds,
                    max_ban_seconds=protection.max_ban)

    def filters(days=7, event='', outcome='', severity='', ip='', subject='', user_id=''):
        clauses, values = ['last_seen>?'], [now()-days*86400]
        for field, value in (('event', event), ('outcome', outcome), ('severity', severity), ('ip', ip), ('user_id', user_id)):
            if value:
                if len(value) > 100: raise HTTPError(422, localized_message('tooLongFilter'))
                clauses.append(field+'=?'); values.append(value)
        if subject:
            if len(subject) > 100: raise HTTPError(422, localized_message('tooLongFilter'))
            clauses.append('subject_key=?'); values.append(monitor.subject_key(subject))
        return ' AND '.join(clauses), values

    def query_events(days, event, outcome, severity, ip, subject, user_id, limit, offset):
        where, values = filters(days, event, outcome, severity, ip, subject, user_id)
        with db() as c:
            total = c.execute('SELECT COUNT(*) FROM security_events WHERE '+where, values).fetchone()[0]
            items = [event_dict(row) for row in c.execute('SELECT * FROM security_events WHERE '+where+' ORDER BY last_seen DESC,id DESC LIMIT ? OFFSET ?', values+[limit, offset])]
        return dict(events=items, total=total, limit=limit, offset=offset)

    @routes.get('/api/admin/security')
    def overview(request: Request, days: int = Query(1, ge=1, le=365)):
        access(request)
        with db() as c:
            counts = dict(failed=0, successful=0, blocked=0, events=0)
            for row in c.execute('SELECT outcome,SUM(count) FROM security_events WHERE last_seen>? GROUP BY outcome', (now()-days*86400,)):
                counts['events'] += row[1]
                if row[0] in ('failure', 'success', 'blocked'):
                    counts[{'failure': 'failed', 'success': 'successful', 'blocked': 'blocked'}[row[0]]] = row[1]
            # Password accepted and full login are different stages. Only completed
            # sessions count as successful logins in the overview.
            counts['successful'] = c.execute("SELECT COALESCE(SUM(count),0) FROM security_events WHERE event='login_success' AND last_seen>?", (now()-days*86400,)).fetchone()[0]
            counts['failed'] = c.execute("SELECT COALESCE(SUM(count),0) FROM security_events WHERE event='auth_failure' AND last_seen>?", (now()-days*86400,)).fetchone()[0]
            counts['incidents'] = c.execute('SELECT COUNT(*) FROM security_incidents WHERE last_seen>?', (now()-days*86400,)).fetchone()[0]
            counts['open_incidents'] = c.execute("SELECT COUNT(*) FROM security_incidents WHERE status='open'").fetchone()[0]
            counts['active_bans'] = c.execute('SELECT COUNT(*) FROM security_bans WHERE until>?', (now(),)).fetchone()[0]
            recent = [incident_dict(row) for row in c.execute('SELECT * FROM security_incidents ORDER BY last_seen DESC,id DESC LIMIT 8')]
            mode = monitor.public_mode(monitor.get_mode(c))
            health = monitor.health(c)
        return dict(server_time=now(), days=days, mode=mode, health=health, counts=counts, policy=policy(), recent_incidents=recent)

    @routes.get('/api/admin/security/events')
    def events(request: Request, days: int = Query(7, ge=1, le=365), event: str = '', outcome: str = '', severity: str = '',
               ip: str = '', subject: str = '', user_id: str = '', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=50000)):
        access(request)
        return query_events(days, event, outcome, severity, ip, subject, user_id, limit, offset)

    @routes.get('/api/admin/security/events/export')
    def export(request: Request, days: int = Query(7, ge=1, le=365), event: str = '', outcome: str = '', severity: str = '', ip: str = '', subject: str = '', user_id: str = ''):
        session = access(request)
        protection.rate(request, 'security-export', 10, subject=session['user_id'])
        result = query_events(days, event, outcome, severity, ip, subject, user_id, 5000, 0)
        result.update(exported_at=now(), truncated=result['total'] > 5000)
        audit(request, session, 'security-export', count=len(result['events']))
        return JSONResponse(result, headers={'Content-Disposition': 'attachment; filename="security-events.json"', 'Cache-Control': 'no-store'})

    @routes.get('/api/admin/security/incidents')
    def incidents(request: Request, status: str = '', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=5000)):
        access(request)
        if status not in ('', 'open', 'reviewed'): raise HTTPError(422, localized_message('unknownIncidentStatus'))
        where, params = (' WHERE status=?', [status]) if status else ('', [])
        with db() as c:
            total = c.execute('SELECT COUNT(*) FROM security_incidents'+where, params).fetchone()[0]
            items = [incident_dict(row) for row in c.execute('SELECT * FROM security_incidents'+where+' ORDER BY last_seen DESC,id DESC LIMIT ? OFFSET ?', params+[limit, offset])]
        return dict(incidents=items, total=total, limit=limit, offset=offset)

    @routes.get('/api/admin/security/incidents/{incident_id}')
    def incident(incident_id: str, request: Request):
        access(request)
        with db() as c:
            row = c.execute('SELECT * FROM security_incidents WHERE id=?', (incident_id,)).fetchone()
            if not row: raise HTTPError(404, localized_message('noIncidentFound'))
            field, value = ('subject_key', row['subject_key']) if row['subject_key'] and row['kind'] in ('distributed_attack', 'target_attack', 'factor_failures', 'success_after_failures') else ('ip', row['ip'])
            items = [event_dict(item) for item in c.execute('SELECT * FROM security_events WHERE '+field+'=? AND last_seen>=? AND first_seen<=? ORDER BY last_seen DESC,id DESC LIMIT 100', (value, row['first_seen'], row['last_seen']))]
        return dict(incident=incident_dict(row), events=items)

    @routes.post('/api/admin/security/incidents/{incident_id}/review')
    def review(incident_id: str, data: Review, request: Request):
        session = access(request, True)
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); live(c, session)
            result = c.execute('UPDATE security_incidents SET status=? WHERE id=?', (data.status, incident_id))
            if not result.rowcount: raise HTTPError(404, localized_message('noIncidentFound'))
        audit(request, session, 'incident-review', reason=data.status)
        return dict(ok=True)

    @routes.get('/api/admin/security/bans')
    def bans(request: Request):
        access(request)
        with db() as c:
            items = [dict(row) for row in c.execute('SELECT key AS id,kind,ip,action,strikes,until,updated FROM security_bans WHERE until>? ORDER BY until DESC LIMIT 500', (now(),))]
            total = c.execute('SELECT COUNT(*) FROM security_bans WHERE until>?', (now(),)).fetchone()[0]
        return dict(bans=items, total=total, policy=policy())

    @routes.post('/api/admin/security/bans')
    def ban(data: Ban, request: Request):
        access(request)
        try: ip = str(ipaddress.ip_address(data.ip.strip()))
        except ValueError: raise HTTPError(422, localized_message('enterASpecificIpv4OrIpv6Address'))
        if ip == protection.ip(request): raise HTTPError(409, localized_message('theCurrentAdministratorCannotBeBlocked'))
        session = confirm(request, data)
        key, stamp = protection.key(ip), now()
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); live(c, session)
            c.execute('INSERT INTO security_bans(key,kind,ip,action,strikes,until,updated) VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET kind=excluded.kind,action=excluded.action,until=excluded.until,updated=excluded.updated',
                      (key, 'ip', ip, 'manual', 1, stamp+data.duration, stamp))
        audit(request, session, 'ip-ban', reason=data.reason, duration=data.duration, target_ip=ip)
        return dict(ok=True, id=key, until=stamp+data.duration)

    @routes.delete('/api/admin/security/bans/{ban_id}')
    def unban(ban_id: str, data: Confirmation, request: Request):
        session = confirm(request, data)
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); live(c, session)
            row = c.execute('SELECT ip FROM security_bans WHERE key=?', (ban_id,)).fetchone()
            if not row: raise HTTPError(404, localized_message('noBlockFound'))
            c.execute('DELETE FROM security_bans WHERE ip=?', (row['ip'],))
            c.execute('DELETE FROM auth_failures WHERE ip=?', (row['ip'],))
        audit(request, session, 'ip-unban', reason=data.reason, target_ip=row['ip'])
        return dict(ok=True)

    @routes.get('/api/admin/security/sessions')
    def sessions(request: Request):
        session = access(request)
        with db() as c:
            items = []
            total = c.execute('SELECT COUNT(*) FROM sessions WHERE expires>?', (now(),)).fetchone()[0]
            for row in c.execute('SELECT s.token,s.user_id,u.login,u.role,u.settings,s.stage,s.created,s.expires,i.agent,i.ip,p.last_seen FROM sessions s JOIN users u ON u.id=s.user_id LEFT JOIN session_info i ON i.session_token=s.token LEFT JOIN session_presence p ON p.session_token=s.token WHERE s.expires>? ORDER BY s.created DESC LIMIT 500', (now(),)):
                item = dict(row); token = item.pop('token'); settings = json.loads(item.pop('settings'))
                item.update(session_device(item['agent'] or ''), name=settings.get('_session_names', {}).get(token, ''))
                item.update(id=monitor.key('session', token), current=token == session['token'])
                items.append(item)
        return dict(sessions=items, total=total)

    @routes.post('/api/admin/security/sessions/{session_id}/revoke')
    def revoke(session_id: str, data: Confirmation, request: Request):
        access(request)
        if len(session_id) != 64: raise HTTPError(404, localized_message('sessionNotFound'))
        session = confirm(request, data)
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); live(c, session)
            target = next((row for row in c.execute('SELECT token,user_id FROM sessions WHERE expires>?', (now(),)) if monitor.key('session', row[0]) == session_id), None)
            if not target: raise HTTPError(404, localized_message('sessionNotFound'))
            if target[0] == session['token'] or target[0] == monitor.get_mode(c).get('allowed_session'):
                raise HTTPError(409, localized_message('thisSessionIsRequiredForManagingTheService'))
            c.execute('DELETE FROM sessions WHERE token=?', (target[0],))
        audit(request, session, 'session-revoke', reason=data.reason, revoked=1, target_user_id=target['user_id'], target_session=session_id)
        return dict(ok=True)

    @routes.post('/api/admin/security/mode')
    def mode(data: Mode, request: Request):
        session = confirm(request, data)
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); live(c, session)
            previous = monitor.get_mode(c)
            state = monitor.set_mode(data.mode, session, data.duration, data.reason, c)
        audit(request, session, 'service-mode', mode=data.mode, previous_mode=previous['mode'], duration=data.duration, reason=data.reason)
        return dict(ok=True, mode=monitor.public_mode(state))

    return routes
