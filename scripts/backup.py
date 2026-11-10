#!/usr/bin/env python3
"""Offline encrypted portable backup, for SQLite and PostgreSQL."""
import argparse
import base64
import getpass
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from app.database import Database
from app.data_crypto import StorageError
from app.backups import Backups,BackupError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--offline',action='store_true',help='Confirm that all app processes are stopped.')
    parser.add_argument('--password-file',help='Private UTF-8 file containing the archive password; otherwise prompt.')
    args=parser.parse_args()
    if not args.offline:parser.error('Stop all application processes and pass --offline, or use the online administrator backup menu.')
    output=Path(args.output)
    if output.suffix!='.drgbackup':parser.error('Output must end in .drgbackup; plaintext SQLite snapshots are not produced.')
    if output.exists():parser.error('Output already exists.')
    password=Path(args.password_file).read_text().rstrip('\r\n') if args.password_file else getpass.getpass('Archive password: ')
    if not 12<=len(password)<=256:parser.error('Archive password must contain 12–256 characters.')
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    root=Path(os.getenv('DATA_DIR','./data'));secret=os.getenv('APP_SECRET','')
    if len(secret)<32:raise StorageError('APP_SECRET is required to make portable 2FA backups.')
    database=Database(root)
    if database.kind=='sqlite' and not database.path.exists():raise StorageError('Database not found.')
    database.initialize(create=False)
    totp_key=base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()
    service=Backups(database.connection,root,Fernet(totp_key.encode()),totp_key,lambda:int(time.time()))
    with service.temp() as directory:
        metadata=service.snapshot(directory)
        archive=service.encrypt(directory,metadata,password)
        with os.fdopen(os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as destination,archive.open('rb') as source:
            shutil.copyfileobj(source,destination,length=1024*1024);destination.flush();os.fsync(destination.fileno())
    print('Encrypted portable backup completed.')

if __name__=='__main__':
    try:main()
    except (StorageError,BackupError) as error:raise SystemExit(str(error)) from None
