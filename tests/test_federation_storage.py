"""Canonical encrypted stores: revocation, reference boundaries and full restore."""
import json
from types import SimpleNamespace
from pathlib import Path

from app.http import Application
import pytest
from app.data_crypto import MediaStore
from app.federation.actors import DeviceKeys
from app.federation.identity import FederationError, SigningIdentity
from app.federation.prototype import FederationPrototype, Intent
from app.federation.storage import FederationState
from app.federation.notes import ensure_document, execute
from app.federation.revocations import REVOCATION_PATH, enqueue, deliver
from tests.test_storage_encryption import databases, seed, backup_service

UID, ACTOR, NOTE = 'a'*32, 'd'*32, 'n'*16


def fail(status,message):
    raise ValueError(message)


def configured(backend):
    seed(backend)
    main = SimpleNamespace(db=backend.connection,data_cipher=backend.cipher,DATA=backend.root,
                           PUBLIC_URL='https://home.example',fail=fail)
    state = FederationState(main.db,main.data_cipher,main.DATA)
    service = FederationPrototype(main,state=state)
    peer = SigningIdentity.generate('https://other.example').descriptor
    keys = DeviceKeys.generate();card = keys.card(peer.server_id,ACTOR)
    blocks = [{'id':'initial','type':'paragraph','props':{},'content':[{'type':'text','text':'Private shared text','styles':{}}],'children':[]}]
    with state.transaction() as c:
        c.execute("INSERT INTO metadata VALUES('enabled','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        c.execute('INSERT INTO peers VALUES(?,?,?,1)',(peer.server_id,peer.model_dump_json(),'e'*64))
        c.execute("INSERT INTO contacts VALUES(?,?,?,?,'accepted','')",(UID,peer.server_id,ACTOR,card.model_dump_json()))
        c.execute('INSERT INTO federation_contact_devices VALUES(?,?,?,?,?,1)',(UID,peer.server_id,ACTOR,card.device_id,card.model_dump_json()))
        c.execute('INSERT INTO grants VALUES(?,?,?,?,?,?,1,1)',(NOTE,UID,peer.server_id,ACTOR,card.device_id,'editor'))
        c.execute('UPDATE notes SET blocks=? WHERE id=?',(json.dumps(blocks),NOTE))
        page = c.execute('SELECT * FROM notes WHERE id=?',(NOTE,)).fetchone()
        ensure_document(c.application_connection,page,fail,100)
    return service,peer,keys,card


def intent(card,operation,payload):
    return Intent(actor_server_id=card.server_id,actor_id=card.actor_id,target_actor_id=UID,
                  device_id=card.device_id,nonce='0'*64,event_id='1'*32,operation=operation,payload=payload)


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_primary_revocation_is_permanent_additional_device_retains_grant_and_delivery_retries(databases,engine):
    backend = databases(engine);service,peer,keys,card = configured(backend)
    second = DeviceKeys.generate().card(peer.server_id,ACTOR)
    with service.state.transaction() as c:
        c.execute('INSERT INTO federation_contact_devices VALUES(?,?,?,?,?,1)',(UID,peer.server_id,ACTOR,second.device_id,second.model_dump_json()))
    service.local_user = lambda **kwargs: UID
    service.incoming = lambda body: (peer,None)
    service.signed_result = lambda peer,verified,value:value
    app = Application(__name__);app.include_router(service.router)
    response = app.test_client().post(REVOCATION_PATH,json={'actor_id':ACTOR,'device_id':card.device_id,'target_actor_id':''})
    assert response.status_code == 200, response.text
    with service.state.transaction() as c:
        with pytest.raises(FederationError): service.confirmed_card(c,UID,peer.server_id,ACTOR,card.device_id)
        assert service.confirmed_card(c,UID,peer.server_id,ACTOR,second.device_id) == second
        c.execute("INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,'member','{}',1)",('e'*32,'another-local-user','not-a-login-fixture'))
        c.execute("INSERT INTO contacts VALUES(?,?,?,?,'accepted','')",('e'*32,peer.server_id,ACTOR,second.model_dump_json()))
        enqueue(c,UID,device_id='f'*64)
    response = app.test_client().post(REVOCATION_PATH,json={'actor_id':ACTOR,'device_id':'','target_actor_id':UID})
    assert response.status_code == 200
    with service.state.transaction() as c:
        assert service.contact(c,'e'*32,peer.server_id,ACTOR,accepted=True)['status'] == 'accepted'
        with pytest.raises(FederationError): service.contact(c,UID,peer.server_id,ACTOR,accepted=True)
    service.transport.post = lambda *args: (_ for _ in ()).throw(FederationError('peer_unavailable'))
    deliver(service)
    with service.state.transaction() as c:
        assert c.execute('SELECT COUNT(*) FROM federation_revocations').fetchone()[0] == 1
    calls = []
    def acknowledge(peer,path,value,now):
        calls.append((path,value));return {'revoked':True}
    service.transport.post = acknowledge
    deliver(service)
    assert calls == [(REVOCATION_PATH,{'actor_id':UID,'device_id':'f'*64,'target_actor_id':''})]
    with service.state.transaction() as c:
        assert c.execute('SELECT COUNT(*) FROM federation_revocations').fetchone()[0] == 0


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_new_child_cannot_launder_guessed_private_attachments_and_positions_remain_portable(databases,engine):
    backend = databases(engine);service,peer,keys,card = configured(backend)
    for reference in ('https://home.example/api/media/'+'b'*32,
                      'https://home.example/api/notes/'+'p'*16+'/files/'+'f'*16):
        blocks = [{'id':'guess','type':'file','props':{'url':reference},'children':[]}]
        with pytest.raises(FederationError,match='upload_media_to_shared_note'):
            with service.state.transaction() as c:
                execute(service,c,peer,intent(card,'create',{'parent_id':NOTE,'title':'Guess','blocks':blocks}))
    with service.state.transaction() as c:
        assert c.execute('SELECT COUNT(*) FROM notes').fetchone()[0] == 1
        result,_ = execute(service,c,peer,intent(card,'create',{'parent_id':NOTE,'title':'Allowed child','blocks':[]}))
        assert result['position'] == 0
        assert result['parent_id'] == NOTE
        from app.personal_data import Page
        row = dict(c.execute('SELECT * FROM notes WHERE id=?',(result['id'],)).fetchone())
        row = {key:value for key,value in row.items() if key in Page.model_fields}
        row['blocks'] = json.loads(row['blocks'])
        assert Page.model_validate(row).position == 0


@pytest.mark.parametrize('source_engine,target_engine',[('sqlite','postgres'),('postgres','sqlite'),('sqlite','sqlite')])
def test_federation_identity_and_private_crdt_archive_rekey_with_authority_disabled(databases,source_engine,target_engine):
    source = databases(source_engine);service,peer,keys,card = configured(source)
    with service.state.transaction() as c:
        c.execute('INSERT INTO federation_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('c'*16,peer.server_id,ACTOR,UID,card.device_id,'','ringing',100,0,0,0,
             json.dumps({'caller_sdp':'PRIVATE_SDP','caller_handle':'h'*16,'caller_seen':100,'recipient_seen':100})))
    backups = backup_service(source)
    with backups.temp() as stage:
        manifest = backups.snapshot(stage)
        with backups.snapshot_database(stage) as snapshot:
            assert snapshot.execute('SELECT payload FROM federation_calls').fetchone()[0] == '{}'
        archive = backups.encrypt(stage,manifest,'Federation-archive-password-123').read_bytes()
        assert b'PRIVATE_SDP' not in archive and source.cipher.key not in archive
    target = databases(target_engine);restore = backup_service(target)
    with restore.temp() as stage:
        manifest = restore.prepare(archive,'Federation-archive-password-123',stage)
        assert restore.restore(stage,manifest) == 'owner'
    restored = FederationState(target.connection,target.cipher,target.root)
    assert restored.identity(service.identity.descriptor.origin).descriptor == service.identity.descriptor
    with restored.transaction() as c:
        assert not service.enabled(c)
        assert c.execute('SELECT active FROM peers').fetchone()[0] == 0
        assert c.execute('SELECT active FROM federation_contact_devices').fetchone()[0] == 0
        assert c.execute('SELECT active,revision FROM grants').fetchone()[0] == 0
        assert c.execute('SELECT status,payload FROM federation_calls').fetchone()[0] == 'failed'
        note = c.execute('SELECT * FROM notes WHERE id=?',(NOTE,)).fetchone()
        saved = c.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(NOTE,)).fetchone()
        from app.collaboration import project,load_doc
        assert project(load_doc(saved['state']),fail)[1] == note['body']
    assert MediaStore(target.root,target.cipher).read('b'*32) == MediaStore(source.root,source.cipher).read('b'*32)
