"""Read-only audit of this instance; never print credentials or user content.

Run inside the configured app container: python scripts/storage_audit.py
Exit 2 means inconsistencies were found; unsupported provider diagnostics are
reported separately. The exclusive storage gate prevents in-flight upload races.
"""
import json
import os
import sys
import time
from contextlib import nullcontext,closing
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.database import Database,Connection,COLUMNS,portable_connection
from app.data_crypto import DataCipher,MediaStore,StorageError
from app.file_store import FileStore
from app.storage import StorageGate
from app.storage_reconciliation import inventory,summary,MISSING_KEY


def audit(c,objects,media,root,stamp):
    result={'storage':summary(inventory(lambda:nullcontext(c),objects,media))}
    from app.vault_audit import inventory as vault_inventory
    result['secret_vault']=vault_inventory(c,objects,root,stamp)
    with closing(portable_connection()) as schema:
        foreign={}
        for table in COLUMNS:
            for fk in schema.execute('PRAGMA foreign_key_list('+table+')'):
                count=c.execute('SELECT COUNT(*) FROM '+table+' a LEFT JOIN '+fk['table']+' b ON a.'+fk['from']+'=b.'+fk['to']+' WHERE a.'+fk['from']+' IS NOT NULL AND b.'+fk['to']+' IS NULL').fetchone()[0]
                if count:foreign[table+'.'+fk['from']]=count
    result['dangling_foreign_keys']=foreign
    queries={'sessions':('sessions','expires<=?',stamp),'refresh_tokens':('refresh_tokens','expires<=?',stamp),
             'invitations':('invites','expires<=? OR used=1',stamp),'old_throttle':('throttle','stamp<=?',stamp-86400),
             'note_history':('note_revisions','created<=?',stamp-90*86400),'note_trash':('notes','deleted<=?',stamp-30*86400),
             'file_trash':('files','deleted<=?',stamp-7*86400),'notifications':('notifications','created<=?',stamp-7*86400),
             'expense_events':('expense_events','purge_at<=?',stamp),'guest_rooms':('guest_call_rooms','expires<=?',stamp)}
    result['expired_rows']={key:c.execute('SELECT COUNT(*) FROM '+table+' WHERE '+where,(value,)).fetchone()[0] for key,(table,where,value) in queries.items()}
    result['queued_object_deletions']=c.execute('SELECT COUNT(*) FROM blob_gc').fetchone()[0]
    from app.admin_access import IDLE_SECONDS
    transient=0
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ? OR key LIKE ?',('admin-access:%','passkey:challenge:%')):
        try:
            value=json.loads(row['value']);field='last_activity' if row['key'].startswith('admin-access:') else 'expires'
            transient+=type(value.get(field)) is not int or value[field]<=(stamp-IDLE_SECONDS if field=='last_activity' else stamp)
        except (ValueError,TypeError,AttributeError):transient+=1
    result['expired_rows']['transient_settings']=transient
    pending=c.execute('SELECT value FROM app_settings WHERE key=?',(MISSING_KEY,)).fetchone()
    result['confirmed_missing_pending']=len(json.loads(pending[0])) if pending else 0
    result['temporary_directories']=sum(p.is_dir() and not p.is_symlink() for prefix in ('.drg-backup-work-','.drg-personal-work-','.drg-notion-work-') for p in root.glob(prefix+'*'))
    result['volume_bytes']=0
    for path in root.rglob('*'):
        try:
            if path.is_file() and not path.is_symlink():result['volume_bytes']+=path.stat().st_size
        except FileNotFoundError:pass  # A leased export may finish outside gate.
    if objects.backend=='s3':
        own='/'.join(filter(None,(objects.prefix,objects.storage_id)))+'/'
        scopes={'current_instance':{'objects':0,'bytes':0},'other_namespaces':{'objects':0,'bytes':0}}
        unexpected=0
        for page in objects.client.get_paginator('list_objects_v2').paginate(Bucket=objects.bucket,Prefix=objects.prefix+'/' if objects.prefix else ''):
            for item in page.get('Contents',[]):
                group=scopes['current_instance' if item['Key'].startswith(own) else 'other_namespaces'];group['objects']+=1;group['bytes']+=item['Size']
                if item['Key'].startswith(own):
                    import re
                    unexpected+=not bool(re.fullmatch(r'(?:(?:files|tracker-attachments|media)/[a-f0-9]{32}|secret/[a-f0-9]{32}/[a-f0-9]{32})',item['Key'][len(own):]))
        result['s3']=scopes
        result['s3']['unexpected_object_keys']=unexpected
        result['s3']['unfinished_uploads']=sum(len(page.get('Uploads',[])) for page in objects.client.get_paginator('list_multipart_uploads').paginate(Bucket=objects.bucket,Prefix=own))
        for name,method in [('versioning','get_bucket_versioning'),('lifecycle','get_bucket_lifecycle_configuration')]:
            try:
                value=getattr(objects.client,method)(Bucket=objects.bucket)
                result['s3'][name]=value.get('Status','Disabled') if name=='versioning' else len(value.get('Rules',[]))
            except Exception as error:result['s3'][name]=getattr(error,'response',{}).get('Error',{}).get('Code','unavailable')
    return result


def main():
    root=Path(os.getenv('DATA_DIR','/data'))
    if not root.is_dir() or not (root/'.storage-format.json').is_file():raise StorageError('Configured data directory not found.')
    db=Database(root,cipher=DataCipher.from_env());db._root_marker()
    with StorageGate(root).exclusive_sync():
        raw=db._connect()
        try:
            if db.kind=='postgres':
                raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                db.table_oids={oid:table for oid,table in raw.execute("SELECT c.oid,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='r'",(db.schema,))}
            else:raw.execute('PRAGMA query_only=ON')
            c=Connection(db,raw);c.quota_disabled=True
            objects=FileStore(root,db.cipher,bind=False);media=MediaStore(root,db.cipher,objects if objects.backend=='s3' else None)
            result=audit(c,objects,media,root,int(time.time()))
            result.update(engine=db.kind,encrypted=db.cipher.enabled,file_storage=objects.backend)
            if db.kind=='postgres':
                result['postgres']={'bytes':raw.execute('SELECT pg_database_size(current_database())').fetchone()[0],
                                    'autovacuum':raw.execute('SHOW autovacuum').fetchone()[0],
                                    'dead_rows_estimate':int(raw.execute('SELECT COALESCE(SUM(n_dead_tup),0) FROM pg_stat_user_tables WHERE schemaname=%s',(db.schema,)).fetchone()[0])}
            print(json.dumps(result,indent=2))
            secret=result['secret_vault']
            return 2 if result['dangling_foreign_keys'] or any(v['missing'] or v['unreferenced'] for v in result['storage'].values()) or any(secret[field] for field in ('missing','unreferenced','orphan_accounts','dangling_references','volume_strays')) else 0
        finally:raw.rollback();raw.close()


if __name__=='__main__':
    try:sys.exit(main())
    except StorageError as error:print(str(error),file=sys.stderr);sys.exit(1)
    except Exception:print('Storage audit failed; no data was changed.',file=sys.stderr);sys.exit(1)
