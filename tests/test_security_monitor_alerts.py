"""Security alerts retain critical capacity and count complete incident history."""
import json

from tests.test_security import owner, reset
from tests.test_admin_security import request
from app import main as m
from app.security_monitor import SecurityMonitor, ALERT_STATE_KEY, purge_security


def monitor():
    owner()
    with m.db() as c:
        c.execute('DELETE FROM security_events')
        c.execute('DELETE FROM security_incidents')
        c.execute("DELETE FROM notifications WHERE kind='security'")
        c.execute('DELETE FROM app_settings WHERE key=?', (ALERT_STATE_KEY,))
    stamp = [1800000000]
    return SecurityMonitor(m.db, lambda: stamp[0], m.protection), stamp


def notices():
    with m.db() as c:
        return c.execute("SELECT COUNT(*) FROM notifications WHERE kind='security'").fetchone()[0]


def test_warning_flood_reserves_critical_capacity_and_maintenance_retries_without_spam():
    audit, stamp = monitor()
    for ip in range(10):
        for _ in range(10):
            audit.record(request('192.0.2.'+str(ip+1)), 'access_denied', outcome='blocked')
    assert notices() == 8
    for ip in range(3):
        audit.record(request('198.51.100.'+str(ip+1)), 'refresh_replay', severity='critical')
    assert notices() == 10
    with m.db() as c:
        assert c.execute("SELECT notified_count FROM security_incidents WHERE ip=? AND kind='refresh_replay'", ('198.51.100.3',)).fetchone()[0] == 0
    stamp[0] += 3601
    with m.db() as c:
        purge_security(c, stamp[0])
        assert c.execute("SELECT notified_count FROM security_incidents WHERE ip=? AND kind='refresh_replay'", ('198.51.100.3',)).fetchone()[0] == 1
    assert notices() == 13
    audit.record(request('198.51.100.3'), 'refresh_replay', severity='critical')
    with m.db() as c: purge_security(c, stamp[0])
    assert notices() == 13


def test_suppressed_escalation_is_retried_even_if_incident_was_previously_notified():
    audit, stamp = monitor()
    for ip in range(8): audit.record(request('192.0.2.'+str(ip+1)), 'suspicious_action', severity='high')
    for ip in range(2): audit.record(request('198.51.100.'+str(ip+1)), 'refresh_replay', severity='critical')
    assert notices() == 10
    audit.record(request('192.0.2.1'), 'suspicious_action', severity='critical')
    assert notices() == 10
    stamp[0] += 3601
    with m.db() as c:
        purge_security(c, stamp[0])
        assert c.execute("SELECT notified_count FROM security_incidents WHERE ip=? AND kind='high_risk'", ('192.0.2.1',)).fetchone()[0] == 2
    assert notices() == 11
    audit.record(request('192.0.2.1'), 'suspicious_action', severity='critical')
    assert notices() == 11


def test_reviewed_and_deleted_incidents_leave_the_pending_queue():
    audit, stamp = monitor()
    for ip in range(10): audit.record(request('192.0.2.'+str(ip+1)), 'suspicious_action', severity='high')
    with m.db() as c:
        pending = [row[0] for row in c.execute('SELECT id FROM security_incidents WHERE notified_count=0')]
        assert len(pending) == 2
        c.execute("UPDATE security_incidents SET status='reviewed' WHERE id=?", (pending[0],))
        c.execute('DELETE FROM security_incidents WHERE id=?', (pending[1],))
        stamp[0] += 3601
        purge_security(c, stamp[0])
        state = json.loads(c.execute('SELECT value FROM app_settings WHERE key=?', (ALERT_STATE_KEY,)).fetchone()[0])
        assert state == {'sent': [], 'pending': []}
    assert notices() == 8


def test_incident_totals_count_separate_bursts_once_instead_of_window_peak():
    audit, stamp = monitor()
    for _ in range(8): audit.record(request(), 'auth_failure')
    stamp[0] += 1800
    for _ in range(8): audit.record(request(), 'auth_failure')
    with m.db() as c:
        assert c.execute("SELECT event_count FROM security_incidents WHERE kind='brute_force'").fetchone()[0] == 16
    audit.record(request(), 'auth_failure')
    with m.db() as c:
        assert c.execute("SELECT event_count FROM security_incidents WHERE kind='brute_force'").fetchone()[0] == 17
        assert c.execute("SELECT SUM(count) FROM security_events WHERE event='auth_failure'").fetchone()[0] == 17


def test_repeated_successes_after_failures_are_counted_once_each():
    audit, stamp = monitor()
    for _ in range(5): audit.record(request(), 'auth_failure', action='login', subject='owner')
    audit.record(request(), 'login_success', action='login-success', subject='owner', outcome='success')
    audit.record(request(), 'login_success', action='login-success', subject='owner', outcome='success')
    with m.db() as c:
        assert c.execute("SELECT event_count FROM security_incidents WHERE kind='success_after_failures'").fetchone()[0] == 7


def test_access_probe_total_includes_mixed_rejection_types():
    audit, stamp = monitor()
    for _ in range(5): audit.record(request(), 'csrf_rejected', outcome='blocked')
    for _ in range(5): audit.record(request(), 'access_denied', outcome='blocked')
    with m.db() as c:
        assert c.execute("SELECT event_count FROM security_incidents WHERE kind='access_probe'").fetchone()[0] == 10


def test_backup_preserves_incident_history_without_replaying_delivery_queue(monkeypatch):
    import base64
    import hashlib
    from app.backups import Backups
    audit, stamp = monitor()
    monkeypatch.setattr(m, 'now', lambda: stamp[0])
    for ip in range(10): audit.record(request('192.0.2.'+str(ip+1)), 'suspicious_action', severity='high')
    assert notices() == 8
    with m.db() as c:
        queued = c.execute('SELECT value FROM app_settings WHERE key=?', (ALERT_STATE_KEY,)).fetchone()[0]
    key = base64.urlsafe_b64encode(hashlib.sha256(m.APP_SECRET.encode()).digest()).decode()
    service = Backups(m.db, m.DATA, m.cipher, key, lambda: stamp[0])
    with service.temp() as stage:
        manifest = service.snapshot(stage)
        with service.snapshot_database(stage, write=True) as snapshot:
            assert not snapshot.execute('SELECT 1 FROM app_settings WHERE key=?', (ALERT_STATE_KEY,)).fetchone()
            # An older archive may contain pending delivery state. Restore must
            # discard it before maintenance can send historical notifications.
            snapshot.execute('INSERT INTO app_settings VALUES(?,?)', (ALERT_STATE_KEY, queued))
        service.restore(stage, manifest)
    stamp[0] += 3601
    with m.db() as c:
        purge_security(c, stamp[0])
        assert c.execute('SELECT COUNT(*) FROM security_incidents').fetchone()[0] == 10
        assert not c.execute('SELECT 1 FROM app_settings WHERE key=?', (ALERT_STATE_KEY,)).fetchone()
    assert notices() == 8
    audit.record(request('192.0.2.9'), 'suspicious_action', severity='high')
    assert notices() == 9
