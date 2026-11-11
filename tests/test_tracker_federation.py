"""Canonical project permissions bind signed requests, retries and attachments."""
import base64
import json
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest

from app import tracker
from app.federation.tracker import execute
from app.federation.prototype import Intent, OPERATION_PATH
from app.federation.identity import FederationError
from app.federation.actors import verify_result
from app.http import Application
from app.modules.tracker import add_member
from tests.test_federation_storage import configured, databases, UID, ACTOR


@pytest.fixture(params=['sqlite', 'postgres'])
def pair(databases, request):
    backend = databases(request.param)
    service, peer, keys, card = configured(backend)
    with backend.connection() as c:
        settings = json.loads(c.execute('SELECT settings FROM users WHERE id=?', (UID,)).fetchone()[0]); settings['modules'] = {'tracker': True}
        c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), UID))
        project = tracker.create_project(c, tracker.Project(name='Shared project', key='WORK'), UID, int(time.time()))
        task = tracker.create_issue(c, project['id'], tracker.Issue(title='Shared task'), UID, int(time.time()))
        add_member(c, project['id'], tracker.Member(actor_id=ACTOR, peer_id=peer.server_id), UID, int(time.time()))
    return SimpleNamespace(backend=backend, service=service, peer=peer, keys=keys, card=card, project=project, task=task)


def event(p, operation, payload):
    return Intent(actor_server_id=p.peer.server_id, actor_id=ACTOR, target_actor_id=UID,
                  device_id=p.card.device_id, nonce='0'*64, event_id=uuid4().hex, operation=operation, payload=payload)


def remote(p, operation, **payload):
    address = {} if operation == 'tracker_catalog' else {'project_id': p.project['id'], 'revision': 1}
    with p.service.state.transaction() as c:
        return execute(p.service, c, p.peer, event(p, operation, address | payload))[0]


def accept(p): return remote(p, 'tracker_accept')


def test_remote_consent_create_edit_comments_and_foreign_issue_binding(pair):
    p = pair
    catalog = remote(p, 'tracker_catalog')
    assert catalog['projects'][0]['pending'] is True and 'members' not in catalog['projects'][0]
    with pytest.raises(FederationError): remote(p, 'tracker_issue_read', issue_id=p.task['id'])
    assert accept(p)['grant_revision'] == 1
    task = remote(p, 'tracker_issue_create', data={'title': 'Remote work', 'status': 'work'})
    updated = remote(p, 'tracker_issue_update', issue_id=task['id'], data={'version': 1, 'status': 'done'})
    assert updated['status'] == 'done' and updated['author_peer'] == p.peer.server_id
    remote(p, 'tracker_comment', issue_id=task['id'], body='Remote comment')
    assert remote(p, 'tracker_issue_read', issue_id=task['id'])['comments'][0]['body'] == 'Remote comment'
    with p.backend.connection() as c:
        other = tracker.create_project(c, tracker.Project(name='PRIVATE_PROJECT', key='PRIVATE'), UID, int(time.time()))
        hidden = tracker.create_issue(c, other['id'], tracker.Issue(title='PRIVATE_ISSUE'), UID, int(time.time()))
    with pytest.raises(FederationError): remote(p, 'tracker_issue_read', issue_id=hidden['id'])
    assert 'PRIVATE_' not in json.dumps(remote(p, 'tracker_catalog'))


def test_viewer_revocation_and_module_disable_are_current_not_cached(pair):
    p = pair; accept(p)
    with p.backend.connection() as c:
        c.execute("UPDATE tracker_members SET role='viewer' WHERE project_id=?", (p.project['id'],))
    assert remote(p, 'tracker_issue_read', issue_id=p.task['id'])['id'] == p.task['id']
    with pytest.raises(FederationError): remote(p, 'tracker_issue_update', issue_id=p.task['id'], data={'version': 1, 'status': 'done'})
    with p.backend.connection() as c:
        c.execute("UPDATE tracker_members SET state='revoked',revision=revision+1 WHERE project_id=?", (p.project['id'],))
    with pytest.raises(FederationError): remote(p, 'tracker_issue_read', issue_id=p.task['id'])
    assert remote(p, 'tracker_catalog')['projects'] == []
    with p.backend.connection() as c:
        c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps({'modules': {'tracker': False}}), UID))
    with pytest.raises(FederationError): remote(p, 'tracker_catalog')


def test_remote_staged_upload_retry_download_and_cross_issue_denial(pair):
    p = pair; accept(p)
    body = b'PRIVATE_TRACKER_ATTACHMENT' * 100
    started = remote(p, 'tracker_upload_start', issue_id=p.task['id'], name='proof.txt', size=len(body))
    chunk = {'issue_id': p.task['id'], 'upload_id': started['id'], 'offset': 0, 'body': base64.b64encode(body).decode()}
    assert remote(p, 'tracker_upload_chunk', **chunk)['received'] == len(body)
    assert remote(p, 'tracker_upload_chunk', **chunk)['received'] == len(body)
    attachment = remote(p, 'tracker_upload_finish', issue_id=p.task['id'], upload_id=started['id'])
    read = remote(p, 'tracker_attachment_read', issue_id=p.task['id'], attachment_id=attachment['id'], offset=0, length=len(body))
    assert base64.b64decode(read['body']) == body
    other = remote(p, 'tracker_issue_create', data={'title': 'Another task'})
    with pytest.raises(FederationError): remote(p, 'tracker_attachment_read', issue_id=other['id'], attachment_id=attachment['id'])
    with p.backend.connection() as c: assert c.execute('SELECT COUNT(*) FROM tracker_uploads').fetchone()[0] == 0


def test_signed_retry_is_idempotent_and_rechecks_revoke(pair):
    p = pair; accept(p)
    p.service.incoming = lambda raw: (p.peer, None)
    p.service.signed_result = lambda peer, verified, value: value
    app = Application(__name__); app.include_router(p.service.router); client = app.test_client()
    action = event(p, 'tracker_issue_create', {'project_id': p.project['id'], 'revision': 1, 'data': {'title': 'Created once'}})
    def send():
        nonlocal action
        with p.service.state.transaction() as c: nonce = p.service.state.challenge(c, p.peer.server_id, int(time.time()))
        action = action.model_copy(update={'nonce': nonce})
        proof = p.keys.sign(p.card, p.service.identity.descriptor.server_id, action.model_dump(), int(time.time()))
        return client.post(OPERATION_PATH, json={'intent': action.model_dump(), 'proof': proof})
    first = send(); assert first.status_code == 200, first.text
    verify_result(p.service.identity.descriptor, p.card, action.model_dump(), first.get_json(), int(time.time()))
    identity = p.keys.open(first.get_json(), action.model_dump())['id']
    repeated = send(); assert repeated.status_code == 200, repeated.text
    assert p.keys.open(repeated.get_json(), action.model_dump())['id'] == identity
    with p.backend.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM tracker_issues WHERE project_id=?', (p.project['id'],)).fetchone()[0] == 2
        c.execute("UPDATE tracker_members SET state='revoked',revision=revision+1 WHERE project_id=?", (p.project['id'],))
    assert send().status_code == 403


def test_two_real_mtls_servers_exchange_tracker_work_and_enforce_revocation(databases, monkeypatch):
    """Exercise the actual listeners, TLS pins, signed intent and sealed reply."""
    import socket
    from app.federation.runtime import FederationRuntime
    from app.federation.actors import DeviceKeys
    from app.federation.prototype import CHALLENGE_PATH
    from tests.test_federation_runtime import free_port
    from tests.test_storage_encryption import seed
    nodes=[]
    for variable in ('FEDERATION_TLS_CERT','FEDERATION_TLS_KEY','FEDERATION_EXTERNAL_TLS'):
        monkeypatch.delenv(variable,raising=False)
    try:
        for name in ('tracker-first','tracker-second'):
            port=free_port(); origin=f'https://{name}.example:{port}'
            monkeypatch.setenv('FEDERATION_PUBLIC_URL',origin);monkeypatch.setenv('FEDERATION_BIND_PORT',str(port))
            backend=databases();seed(backend)
            with backend.connection() as c:
                settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(UID,)).fetchone()[0]);settings['modules']={'tracker':True}
                c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),UID))
            app=Application(name)
            main=SimpleNamespace(db=backend.connection,data_cipher=backend.cipher,DATA=backend.root,PUBLIC_URL='https://'+name+'.example',app=app)
            runtime=FederationRuntime(main);keys=DeviceKeys.generate();card=keys.card(runtime.service.identity.descriptor.server_id,UID)
            nodes.append(SimpleNamespace(runtime=runtime,backend=backend,port=port,keys=keys,card=card))
        first,second=nodes
        for node,other in ((first,second),(second,first)):
            runtime=node.runtime
            with runtime.state.transaction() as c:
                c.execute("INSERT INTO metadata VALUES('enabled','true')")
                descriptor=other.runtime.service.identity.descriptor
                c.execute('INSERT INTO peers VALUES(?,?,?,1)',(descriptor.server_id,descriptor.model_dump_json(),other.runtime.service.transport_public['fingerprint']))
                c.execute('INSERT INTO peer_certificates VALUES(?,?)',(descriptor.server_id,other.runtime.certificate))
                c.execute("INSERT INTO contacts VALUES(?,?,?,?,'accepted','')",(UID,descriptor.server_id,UID,other.card.model_dump_json()))
                c.execute('INSERT INTO federation_contact_devices VALUES(?,?,?,?,?,1)',(UID,descriptor.server_id,UID,other.card.device_id,other.card.model_dump_json()))
            runtime.service.transport.private_origins={other.runtime.origin:frozenset({'127.0.0.1'})}
        original=socket.getaddrinfo
        monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,*args,**kwargs:original('127.0.0.1' if host in ('tracker-first.example','tracker-second.example') else host,port,*args,**kwargs))
        for node in nodes:
            monkeypatch.setenv('FEDERATION_BIND_PORT',str(node.port));node.runtime.refresh()
        with second.backend.connection() as c:
            project=tracker.create_project(c,tracker.Project(name='Wire project',key='WIRE'),UID,int(time.time()))
            add_member(c,project['id'],tracker.Member(actor_id=UID,peer_id=first.card.server_id),UID,int(time.time()))
        def wire(operation,**payload):
            service=first.runtime.service;peer=second.runtime.service.identity.descriptor
            nonce=service.transport.post(peer,CHALLENGE_PATH,{},int(time.time()))['nonce']
            address={} if operation=='tracker_catalog' else {'project_id':project['id'],'revision':1}
            action=Intent(actor_server_id=first.card.server_id,actor_id=UID,target_actor_id=UID,device_id=first.card.device_id,nonce=nonce,event_id=uuid4().hex,operation=operation,payload=address|payload)
            proof=first.keys.sign(first.card,peer.server_id,action.model_dump(),int(time.time()))
            result=service.transport.post(peer,OPERATION_PATH,{'intent':action.model_dump(),'proof':proof},int(time.time()))
            verify_result(peer,first.card,action.model_dump(),result,int(time.time()))
            return first.keys.open(result,action.model_dump())
        assert wire('tracker_catalog')['projects'][0]['pending']
        wire('tracker_accept')
        task=wire('tracker_issue_create',data={'title':'Through real TLS','status':'work'})
        done=wire('tracker_issue_update',issue_id=task['id'],data={'version':1,'status':'done'})
        assert done['status']=='done'
        wire('tracker_comment',issue_id=task['id'],body='Across two servers')
        assert wire('tracker_issue_read',issue_id=task['id'])['comments'][0]['body']=='Across two servers'
        with second.backend.connection() as c:c.execute("UPDATE tracker_members SET state='revoked',revision=revision+1 WHERE project_id=?",(project['id'],))
        with pytest.raises(FederationError):wire('tracker_issue_read',issue_id=task['id'])
    finally:
        for node in nodes:node.runtime.stop()


def test_group_avatar_and_merge_request_follow_current_canonical_grant(pair):
    import io
    from PIL import Image
    p=pair;output=io.BytesIO();Image.new('RGB',(256,256),'purple').save(output,format='PNG')
    artwork=tracker.Project(name='Shared project',key='WORK',avatar=base64.b64encode(output.getvalue()).decode()).avatar
    with p.backend.connection() as c:
        row=tracker.project_row(c,p.project['id']);payload=json.loads(row['payload']);payload['avatar']=artwork
        c.execute('UPDATE tracker_projects SET payload=? WHERE id=?',(tracker.dump(payload),p.project['id']))
        group=tracker.create_group(c,p.project['id'],tracker.Group(name='Web App',description='Retained notes'),UID,int(time.time()))
        other=tracker.create_project(c,tracker.Project(name='Other',key='OTHER'),UID,int(time.time()))
        foreign=tracker.create_group(c,other['id'],tracker.Group(name='Foreign'),UID,int(time.time()))
    with pytest.raises(FederationError):remote(p,'tracker_avatar_read')
    accept(p)
    assert remote(p,'tracker_avatar_read')['body']==artwork
    assert remote(p,'tracker_group_read',group_id=group['id'])['description']=='Retained notes'
    with pytest.raises(FederationError):remote(p,'tracker_group_read',group_id=foreign['id'])
    item=remote(p,'tracker_issue_create',data={'title':'Remote grouped task','group_id':group['id'],'merge_request_url':'https://git.example/repo/pulls/4'})
    page=remote(p,'tracker_page',group_id=group['id'])
    assert [i['id'] for i in page['items']]==[item['id']] and page['view_counts']['todo']==1
    assert remote(p,'tracker_issue_read',issue_id=item['id'])['merge_request_url'].endswith('/4')
    with p.backend.connection() as c:c.execute("UPDATE tracker_members SET state='revoked',revision=revision+1 WHERE project_id=?",(p.project['id'],))
    with pytest.raises(FederationError):remote(p,'tracker_avatar_read')
    with pytest.raises(FederationError):remote(p,'tracker_group_read',group_id=group['id'])


def test_federated_cancellation_is_explicit_preserved_and_reopenable(pair):
    p=pair;accept(p)
    changed=remote(p,'tracker_issue_update',include_cancelled=True,issue_id=p.task['id'],data={'version':1,'status':'cancelled','cancellation_reason':'Remote cancellation'})
    assert changed['status']=='cancelled' and changed['completed_at'] is None
    assert remote(p,'tracker_page')['view_counts']['todo']==0
    assert remote(p,'tracker_page',include_cancelled=True)['view_counts']['cancelled']==1
    with pytest.raises(FederationError):remote(p,'tracker_issue_read',issue_id=p.task['id'])
    assert remote(p,'tracker_issue_read',include_cancelled=True,issue_id=p.task['id'])['cancellation_reason']=='Remote cancellation'
    reopened=remote(p,'tracker_issue_update',include_cancelled=True,issue_id=p.task['id'],data={'version':2,'status':'work'})
    assert reopened['status']=='work' and not reopened.get('cancellation_reason')
