"""Reminder metadata stays protected and survives complete, cross-key backups."""
import json
import pytest
from tests.test_security import reset
from tests.test_storage_encryption import databases, seed, backup_service
from app.data_crypto import DataCipher
from app import task_order
from app.modules.tasks import ListPayload, TaskPayload, ensure_default_list

@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_reminder_payload_encryption_and_full_restore_preserve_tree(databases,engine):
    source=databases(engine);seed(source);uid='a'*32;list_id='reminder_list_001'
    with source.connection() as db:
        ensure_default_list(db,uid,123)
        list_payload=ListPayload(name='PRIVATE_LIST_MARKER',retain_completed=True).model_dump()
        db.execute('INSERT INTO task_lists(id,owner_id,payload,created,updated) VALUES(?,?,?,?,?)',(list_id,uid,json.dumps(list_payload),1,1))
        for tid,parent in [('reminder_root001',None),('reminder_child01','reminder_root001')]:
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',(tid,uid,'PRIVATE_REMINDER_TITLE','',1,1))
            payload=TaskPayload(notes='PRIVATE_REMINDER_NOTES',tags=['PRIVATE_TAG'],url='https://example.com/private',priority=3).model_dump()
            db.execute('INSERT INTO task_details(task_id,list_id,parent_id,payload,completed_at) VALUES(?,?,?,?,?)',(tid,list_id,parent,json.dumps(payload),122))
        task_order.save(db,list_id,{'reminder_root001':2,'reminder_child01':1})
        order_raw=db.raw.execute("SELECT value FROM app_settings WHERE key='task-order:reminder_list_001'").fetchone()[0]
        raw=db.raw.execute("SELECT payload FROM task_lists WHERE id='reminder_list_001'").fetchone()[0]
        details=db.raw.execute("SELECT payload FROM task_details WHERE task_id='reminder_child01'").fetchone()[0]
        if engine=='postgres':
            assert raw.startswith(DataCipher.TEXT) and details.startswith(DataCipher.TEXT) and order_raw.startswith(DataCipher.TEXT)
            assert 'PRIVATE_' not in raw+details
        assert json.loads(db.execute('SELECT payload FROM task_details WHERE task_id=?',('reminder_child01',)).fetchone()[0])['notes']=='PRIVATE_REMINDER_NOTES'
    if engine=='sqlite':assert b'PRIVATE_' not in source.path.read_bytes()
    service=backup_service(source)
    with service.temp() as stage:
        manifest=service.snapshot(stage)
        archive=service.encrypt(stage,manifest,'Reminder-backup-password-123').read_bytes()
    target=databases(engine);other=backup_service(target)
    with other.temp() as stage:
        metadata=other.prepare(archive,'Reminder-backup-password-123',stage)
        assert other.restore(stage,metadata)=='owner'
    with target.connection() as db:
        assert task_order.positions(db,list_id)=={'reminder_root001':2,'reminder_child01':1}
        assert json.loads(db.execute('SELECT payload FROM task_lists WHERE id=?',(list_id,)).fetchone()[0])==list_payload
        detail=db.execute('SELECT parent_id,payload,completed_at FROM task_details WHERE task_id=?',('reminder_child01',)).fetchone()
        assert detail['parent_id']=='reminder_root001' and detail['completed_at']==122
        assert json.loads(detail['payload'])['tags']==['PRIVATE_TAG']
        assert db.execute('SELECT day,done FROM tasks WHERE id=?',('reminder_child01',)).fetchone()['day']==''
