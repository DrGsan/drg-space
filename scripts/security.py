#!/usr/bin/env python3
"""Local emergency access recovery for either engine; preserves user data."""
import argparse
import ipaddress
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from app.database import Database
from app.data_crypto import StorageError
load_dotenv(Path(__file__).resolve().parents[1]/'.env')

parser = argparse.ArgumentParser()
commands = parser.add_subparsers(dest='command', required=True)
commands.add_parser('list-bans')
commands.add_parser('service-status')
reopen = commands.add_parser('open-service')
reopen.add_argument('--revoke-sessions', action='store_true', help='Also require every account to sign in again')
reopen.add_argument('--reason', default='Восстановление доступа локальной командой', help='Reason recorded in the audit')
unban = commands.add_parser('unban')
unban.add_argument('--ip', required=True)
args = parser.parse_args()
database=Database(Path(os.getenv('DATA_DIR','./data')))
if database.kind=='sqlite' and not database.path.exists():raise SystemExit('Database not found.')
try:database.initialize(create=False)
except StorageError as error:raise SystemExit(str(error)) from None
from app.security_monitor import SecurityMonitor
from app.protection import Protection
protection=Protection(database.connection,lambda:int(time.time()))
monitor=SecurityMonitor(database.connection,lambda:int(time.time()),protection)
if args.command == 'unban':
    ip = str(ipaddress.ip_address(args.ip))
    with database.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM security_bans WHERE ip=?', (ip,))
        c.execute('DELETE FROM auth_failures WHERE ip=?', (ip,))
        print('Ban and failure counters cleared for', ip)
    monitor.record(None,'admin_action',action='cli-unban',outcome='success',details={'target_ip':ip})
elif args.command == 'service-status':
    import json
    print(json.dumps(monitor.public_mode(monitor.get_mode()),ensure_ascii=False))
elif args.command == 'open-service':
    with database.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        monitor.set_mode('normal',reason=args.reason,connection=c)
        if args.revoke_sessions:c.execute('DELETE FROM sessions')
        monitor.record(None,'admin_action',action='cli-open-service',outcome='success',severity='high',
                       details={'reason':args.reason,'mode':'normal','revoked':int(args.revoke_sessions)},connection=c)
    print('Service reopened.' + (' All account sessions revoked.' if args.revoke_sessions else ' Existing sessions preserved.'))
else:
    with database.connection() as c:
        for ip, action, until, strikes in c.execute('SELECT ip,action,until,strikes FROM security_bans WHERE until>? ORDER BY until DESC', (int(time.time()),)):
            print(ip, action, 'until', time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(until)), 'strikes', strikes)
