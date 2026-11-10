"""Persistent application bans and socket-based, explicitly trusted proxy parsing."""
from app.i18n import message as localized_message
import hashlib
import ipaddress
import os

from app.http import Router, HTTPError, Request


def positive_env(name, default, maximum):
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= maximum:
        raise RuntimeError(f'{name} must be between 1 and {maximum}')
    return value


def trusted_networks(value):
    try:
        return tuple(ipaddress.ip_network(item.strip(), strict=False) for item in value.split(',') if item.strip())
    except ValueError as error:
        raise RuntimeError('TRUSTED_PROXIES must be a comma-separated list of IP addresses or CIDRs') from error


def client_ip(request, networks):
    client = getattr(request, 'client', None)
    peer = client.host if client else 'unknown'
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return peer  # TestClient uses a non-IP socket peer.
    def trusted(ip):
        return any(ip.version == net.version and ip in net for net in networks)
    if not trusted(address):
        return str(address)
    forwarded = request.headers.get('x-forwarded-for', '')
    if len(forwarded) > 2048:
        return str(address)
    items = [item.strip() for item in forwarded.split(',')]
    if not forwarded or len(items) > 20:
        return str(address)
    try:
        chain = [ipaddress.ip_address(item) for item in items] + [address]
    except ValueError:
        return str(address)
    # Walk from the socket toward the visitor and stop at the first untrusted hop.
    for hop in reversed(chain):
        if not trusted(hop):
            return str(hop)
    return str(chain[0])


class Protection:
    def __init__(self, db, now):
        self.db, self.now = db, now
        self.networks = trusted_networks(os.getenv('TRUSTED_PROXIES', ''))
        self.limit = positive_env('AUTH_FAILURE_LIMIT', 8, 100)
        self.ip_limit = positive_env('AUTH_IP_FAILURE_LIMIT', 30, 1000)
        self.window = positive_env('AUTH_FAILURE_WINDOW', 900, 86400)
        self.ban_seconds = positive_env('AUTH_BAN_SECONDS', 900, 86400)
        self.max_ban = positive_env('AUTH_MAX_BAN_SECONDS', 86400, 2592000)
        self.monitor = None

    def audit(self, request, event, **values):
        if self.monitor is not None:
            return self.monitor.record(request, event, **values)

    def ip(self, request):
        return client_ip(request, self.networks)

    @staticmethod
    def key(ip, action='', subject=''):
        # Avoid delimiter collisions, and avoid storing login names/share secrets.
        import json
        return hashlib.sha256(json.dumps([ip, action, subject.strip().lower()]).encode()).hexdigest()

    def reject(self, until):
        retry = max(1, until - self.now())
        raise HTTPError(429, localized_message('temporaryLockAfterFailedAttemptsTryAgainAfter', value0=f'{retry}'),
                            headers={'Retry-After': str(retry)})

    def check(self, request, action='', subject=''):
        ip = self.ip(request)
        keys = [self.key(ip)]
        if action:
            keys.append(self.key(ip, action, subject))
        with self.db() as c:
            until = c.execute('SELECT MAX(until) FROM security_bans WHERE key IN (' + ','.join('?' for _ in keys) + ')', keys).fetchone()[0]
        if until and until > self.now():
            self.audit(request, 'request_blocked', action=action, subject=subject,
                       outcome='blocked', details={'until': until})
            self.reject(until)

    def failure(self, request, action, subject=''):
        ip, stamp = self.ip(request), self.now()
        blocked_until = 0
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM auth_failures WHERE stamp<=?', (stamp-self.window,))
            # Retain escalation history for 30 days after the last ban.
            c.execute('DELETE FROM security_bans WHERE until<=? AND updated<?', (stamp, stamp-2592000))
            for key, kind, threshold in [(self.key(ip), 'ip', self.ip_limit),
                                         (self.key(ip, action, subject), 'target', self.limit)]:
                existing = c.execute('SELECT * FROM security_bans WHERE key=?', (key,)).fetchone()
                if existing and existing['until'] > stamp:
                    blocked_until = max(blocked_until, existing['until'])
                    continue  # Blocked requests never extend the ban.
                c.execute('INSERT INTO auth_failures VALUES(?,?,?)', (key, ip, stamp))
                count = c.execute('SELECT COUNT(*) FROM auth_failures WHERE key=? AND stamp>?', (key, stamp-self.window)).fetchone()[0]
                if count >= threshold:
                    strikes = existing['strikes'] + 1 if existing else 1
                    duration = min(self.max_ban, self.ban_seconds * 2 ** min(strikes-1, 20))
                    until = stamp + duration
                    c.execute('INSERT INTO security_bans VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET strikes=excluded.strikes,until=excluded.until,updated=excluded.updated',
                              (key, kind, ip, action if kind == 'target' else 'all', strikes, until, stamp))
                    c.execute('DELETE FROM auth_failures WHERE key=?', (key,))
                    blocked_until = max(blocked_until, until)
        # Ban is committed even though this request returns an error.
        self.audit(request, 'auth_failure', action=action, subject=subject,
                   severity='warning' if action in ('2fa', 'admin-password', 'security-confirm') else 'info')
        if blocked_until:
            self.audit(request, 'ban_created', action=action, subject=subject,
                       outcome='blocked', severity='warning', details={'until': blocked_until})
            self.reject(blocked_until)

    def success(self, request, action, subject=''):
        with self.db() as c:
            c.execute('DELETE FROM auth_failures WHERE key=?', (self.key(self.ip(request), action, subject),))
        self.audit(request, 'auth_success', action=action, subject=subject, outcome='success')

    def rate(self, request, action, limit=10, period=900, subject=''):
        """All-attempt rate limits are distinct from bans based on failures."""
        key, stamp = self.key(self.ip(request), action, subject), self.now()
        denied = False
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM throttle WHERE stamp<=?', (stamp-86400,))
            count = c.execute('SELECT COUNT(*) FROM throttle WHERE key=? AND stamp>?', (key, stamp-period)).fetchone()[0]
            if count >= limit:
                denied = True
                oldest = c.execute('SELECT stamp FROM throttle WHERE key=? AND stamp>? ORDER BY stamp LIMIT 1 OFFSET ?', (key, stamp-period, count-limit)).fetchone()[0]
                retry = max(1, oldest+period-stamp)
            else:
                c.execute('INSERT INTO throttle VALUES(?,?)', (key, stamp))
        if denied:
            self.audit(request, 'rate_limited', action=action, subject=subject, outcome='blocked')
            raise HTTPError(429, localized_message('tooManyRequestsTryAgainLater'), headers={'Retry-After': str(retry)})

    def router(self, owner, fail):
        router = Router()

        @router.get('/api/security/bans')
        def bans(request: Request):
            owner(request)
            with self.db() as c:
                active = [dict(row) for row in c.execute('SELECT key AS id,kind,ip,action,strikes,until FROM security_bans WHERE until>? ORDER BY until DESC LIMIT 100', (self.now(),))]
                total = c.execute('SELECT COUNT(*) FROM security_bans WHERE until>?', (self.now(),)).fetchone()[0]
            return {'bans': active, 'total': total, 'policy': {'failure_limit': self.limit, 'ip_failure_limit': self.ip_limit,
                                                            'window': self.window, 'ban_seconds': self.ban_seconds, 'max_ban_seconds': self.max_ban}}

        @router.delete('/api/security/bans/{ban_id}')
        def unban(ban_id: str, request: Request):
            owner(request)
            fail(410, localized_message('lockManagementHasBeenMovedToAdministrationSecurity'))

        return router
