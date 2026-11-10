"""Current device grants bound every database read, computation and side effect."""
import json
import base64
import copy
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.http import Application, HTTPError
from app.note_databases import DatabaseOperations
from app.federation.databases import execute, replay
from app.federation.notes import execute as note_execute
from app.federation.prototype import Intent, OPERATION_PATH
from app.federation.identity import FederationError
from app.federation.actors import verify_result,canonical
from tests.test_federation_storage import ACTOR, UID, NOTE, configured, databases


def fail(status, message):
    raise HTTPError(status, message)


@pytest.fixture(params=['sqlite', 'postgres'])
def pair(databases, request):
    backend = databases(request.param)
    service, peer, keys, card = configured(backend)
    service.m.fail = fail
    p = SimpleNamespace(backend=backend, service=service, peer=peer, keys=keys, card=card)
    def normal(callback):
        with service.state.transaction() as c:
            return callback(DatabaseOperations(c.application_connection, UID, {'user_id': UID}, lambda: int(time.time()), fail))
    p.normal = normal
    p.db = normal(lambda core: core.create('Shared database', NOTE)['id'])
    return p


def intent(p, operation, payload):
    return Intent(actor_server_id=p.peer.server_id, actor_id=ACTOR, target_actor_id=UID,
                  device_id=p.card.device_id, nonce='0'*64, event_id=uuid4().hex,
                  operation=operation, payload=payload)


def remote(p, operation, payload):
    with p.service.state.transaction() as c:
        return execute(p.service, c, p.peer, intent(p, operation, payload))[0]


def rich_read(p, nid):
    with p.service.state.transaction() as c:
        return note_execute(p.service, c, p.peer, intent(p, 'read', {'note_id': nid, 'revision': 1}))[0]


def grant(p, nid, role):
    with p.service.state.transaction() as c:
        c.execute('INSERT INTO grants VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(note_id,peer_id,actor_id) DO UPDATE SET role=excluded.role,active=excluded.active',
                  (nid, UID, p.peer.server_id, ACTOR, p.card.device_id, role, 1, int(role != 'none')))


def configure(p, nid, properties, views=None):
    return p.normal(lambda core: core.configure(nid, properties, views or [{'id': 'table', 'name': 'Table', 'type': 'table'}], core.read(nid)['version']))


def add(p, nid, title, values):
    return p.normal(lambda core: core.add(nid, title, values)['id'])


def test_remote_full_database_config_records_views_formula_and_typed_catalog(pair):
    p = pair
    properties = [{'id': 'price', 'name': 'Price', 'type': 'number'},
                  {'id': 'total', 'name': 'Total', 'type': 'formula', 'expression': 'prop("Price") * 2'},
                  {'id': 'status', 'name': 'Status', 'type': 'select', 'options': ['Todo', 'Done']},
                  {'id': 'date', 'name': 'Date', 'type': 'date'},
                  {'id': 'before', 'name': 'Before', 'type': 'dependency'}]
    views = [{'id': kind, 'name': kind, 'type': kind,
              **({'group_by': 'status'} if kind == 'board' else {}),
              **({'date_by': 'date'} if kind in ('calendar', 'timeline') else {}),
              'filter': {'property': 'status', 'operator': 'not_equals', 'value': 'Done'},
              'sort': {'property': 'price', 'direction': 'desc'}}
             for kind in ('table', 'list', 'board', 'calendar', 'gallery', 'timeline')]
    result = remote(p, 'database_configure', {'note_id': p.db, 'revision': 1, 'version': 1,
                                             'properties': properties, 'views': views})
    assert len(result['views']) == 6 and result['version'] == 2
    record = remote(p, 'database_record_create', {'note_id': p.db, 'revision': 1, 'title': 'Remote row', 'properties': {'price': 5}})['id']
    result = remote(p, 'database_read', {'note_id': p.db, 'revision': 1})
    assert result['records'][0]['properties']['total'] == 10
    assert result['records'][0]['revision'] == 1 and result['records'][0]['role'] == 'editor'
    assert rich_read(p, p.db)['kind'] == 'database'
    assert rich_read(p, record)['record_database']['record']['properties']['total'] == 10
    with p.service.state.transaction() as c:
        catalog = note_execute(p.service, c, p.peer, intent(p, 'catalog', {}))[0]
    assert next(n for n in catalog['notes'] if n['id'] == p.db)['kind'] == 'database'
    assert remote(p, 'database_formula_preview', {'note_id': p.db, 'revision': 1, 'record_id': record,
                                                 'expression': 'prop("Price") + 3'}) == {'value': 8, 'error': None, 'revision': 1, 'role': 'editor', 'owner_id': UID, 'actor_id': UID, 'peer_id': p.service.identity.descriptor.server_id}
    remote(p, 'database_record_update', {'note_id': record, 'revision': 1, 'version': 1, 'properties': {'price': 7}})
    with pytest.raises(HTTPError) as conflict:
        remote(p, 'database_record_update', {'note_id': record, 'revision': 1, 'version': 1, 'properties': {'price': 9}})
    assert conflict.value.status_code == 409


def test_unshared_relation_schema_ids_values_and_rollup_never_escape_and_links_survive_edit(pair):
    p = pair
    private = p.normal(lambda core: core.create('PRIVATE DATABASE TITLE')['id'])
    configure(p, private, [{'id': 'amount', 'name': 'Amount', 'type': 'number'}])
    secret = add(p, private, 'PRIVATE RECORD TITLE', {'amount': 987654321})
    props = [{'id': 'price', 'name': 'Price', 'type': 'number'},
             {'id': 'link', 'name': 'Relation', 'type': 'relation', 'database_id': private},
             {'id': 'roll', 'name': 'Rollup', 'type': 'rollup', 'relation_id': 'link', 'property': 'amount', 'aggregate': 'sum'}]
    configure(p, p.db, props)
    rid = add(p, p.db, 'Shared row', {'price': 1, 'link': [secret]})
    result = remote(p, 'database_read', {'note_id': p.db, 'revision': 1})
    encoded = json.dumps(result)
    assert all(value not in encoded for value in (private, secret, 'PRIVATE DATABASE TITLE', 'PRIVATE RECORD TITLE', '987654321'))
    assert result['properties'][1]['database_id'] is None and result['properties'][1]['unavailable']
    assert result['records'][0]['properties']['link'] == [] and result['records'][0]['properties']['roll'] is None
    remote(p, 'database_record_update', {'note_id': rid, 'revision': 1, 'version': 1, 'properties': {'price': 2, 'link': []}})
    with p.backend.connection() as c:
        assert json.loads(c.execute('SELECT properties FROM database_records WHERE note_id=?', (rid,)).fetchone()[0])['link'] == [secret]
    updated = remote(p, 'database_configure', {'note_id': p.db, 'revision': 1, 'version': result['version'],
                                              'properties': result['properties'], 'views': [{'id': 'list', 'name': 'List', 'type': 'list'}]})
    assert updated['views'][0]['type'] == 'list' and private not in json.dumps(updated)
    with pytest.raises(HTTPError):
        remote(p, 'database_record_update', {'note_id': rid, 'revision': 1, 'version': 2, 'properties': {'link': [secret]}})
    with pytest.raises(HTTPError):
        remote(p, 'database_configure', {'note_id': p.db, 'revision': 1, 'version': updated['version'],
                                         'properties': [], 'views': updated['views']})


def test_hidden_record_is_excluded_from_options_values_rollups_and_formula_results(pair):
    p = pair
    target = p.normal(lambda core: core.create('Shared target', NOTE)['id'])
    configure(p, target, [{'id': 'amount', 'name': 'Amount', 'type': 'number'}])
    visible = add(p, target, 'Allowed', {'amount': 10})
    hidden = add(p, target, 'HIDDEN RECORD', {'amount': 123456789})
    props = [{'id': 'link', 'name': 'Relation', 'type': 'relation', 'database_id': target},
             {'id': 'roll', 'name': 'Rollup', 'type': 'rollup', 'relation_id': 'link', 'property': 'amount', 'aggregate': 'sum'},
             {'id': 'formula', 'name': 'Formula', 'type': 'formula', 'expression': 'prop("Rollup") * 2'}]
    configure(p, p.db, props)
    add(p, p.db, 'Source', {'link': [visible, hidden]})
    grant(p, hidden, 'none')
    result = remote(p, 'database_read', {'note_id': p.db, 'revision': 1})
    row = result['records'][0]
    assert row['properties'] == {'link': [visible], 'roll': 10, 'formula': 20}
    assert hidden not in json.dumps(result) and 'HIDDEN RECORD' not in json.dumps(result) and '123456789' not in json.dumps(result)
    assert [item['id'] for item in result['relation_options']['link']['records']] == [visible]


def test_schema_changes_cannot_probe_or_reinterpret_hidden_record_values(pair):
    p=pair
    configure(p,p.db,[{'id':'value','name':'Value','type':'number'}])
    hidden=add(p,p.db,'Secret',{'value':987654321})
    grant(p,hidden,'none')
    version=remote(p,'database_read',{'note_id':p.db,'revision':1})['version']
    for kind in ('text','checkbox','number'):
        with pytest.raises(HTTPError) as denied:
            remote(p,'database_configure',{'note_id':p.db,'revision':1,'version':version,
                   'properties':[{'id':'value','name':'Renamed','type':kind}],
                   'views':[{'id':'table','name':'Table','type':'table'}]})
        assert denied.value.status_code==403 and '987654321' not in str(denied.value)


@pytest.mark.parametrize('kind',['relation','dependency'])
def test_hidden_saved_view_filter_is_redacted_and_preserved_on_unrelated_roundtrip(pair,kind):
    p=pair
    if kind=='relation':
        target=p.normal(lambda core:core.create('Private target')['id'])
        configure(p,target,[{'id':'number','name':'Number','type':'number'}])
        hidden=add(p,target,'Private row',{'number':918273645})
        prop={'id':'link','name':'Link','type':'relation','database_id':target}
    else:
        hidden=add(p,p.db,'Private dependency',{})
        prop={'id':'link','name':'Link','type':'dependency'}
        grant(p,hidden,'none')
    views=[{'id':'filtered','name':'Filtered','type':'table','filter':{'property':'link','operator':'contains','value':hidden},'sort':None}]
    configure(p,p.db,[prop],views)
    result=remote(p,'database_read',{'note_id':p.db,'revision':1})
    assert hidden not in json.dumps(result)
    assert result['views'][0]['filter'] is None and result['views'][0]['unavailable_filter']
    result['views'][0]['name']='Renamed';result['views'][0]['sort']={'property':'title','direction':'desc'}
    updated=remote(p,'database_configure',{'note_id':p.db,'revision':1,'version':result['version'],
                   'properties':result['properties'],'views':result['views']})
    assert hidden not in json.dumps(updated)
    with p.backend.connection() as c:
        saved=json.loads(c.execute('SELECT views FROM note_databases WHERE note_id=?',(p.db,)).fetchone()[0])[0]
    assert saved['filter']['value']==hidden and saved['name']=='Renamed' and saved['sort']['direction']=='desc'


def test_reverse_links_cannot_modify_a_viewer_or_unshared_record_and_rollback_is_atomic(pair):
    p = pair
    target = p.normal(lambda core: core.create('Target', NOTE)['id'])
    linked = add(p, target, 'Target record', {})
    configured = configure(p, p.db, [{'id': 'link', 'name': 'Relation', 'type': 'relation', 'database_id': target, 'two_way': True}])
    rid = add(p, p.db, 'Source', {'link': [linked]})
    grant(p, linked, 'viewer')
    with pytest.raises(HTTPError):
        remote(p, 'database_record_update', {'note_id': rid, 'revision': 1, 'version': 1, 'properties': {'link': []}})
    with p.backend.connection() as c:
        assert json.loads(c.execute('SELECT properties FROM database_records WHERE note_id=?', (rid,)).fetchone()[0])['link'] == [linked]
        inverse = configured['properties'][0]['inverse_id']
        assert json.loads(c.execute('SELECT properties FROM database_records WHERE note_id=?', (linked,)).fetchone()[0])[inverse] == [rid]
        target_version=c.execute('SELECT version FROM note_databases WHERE note_id=?',(target,)).fetchone()[0]
    grant(p,target,'viewer')
    data=remote(p,'database_read',{'note_id':p.db,'revision':1})
    updated=remote(p,'database_configure',{'note_id':p.db,'revision':1,'version':data['version'],
                   'properties':data['properties'],'views':[{'id':'list','name':'List','type':'list'}]})
    assert updated['views'][0]['type']=='list'
    with p.backend.connection() as c:
        assert c.execute('SELECT version FROM note_databases WHERE note_id=?',(target,)).fetchone()[0]==target_version


def test_individually_shared_record_has_only_own_schema_values_and_edit_authority(pair):
    p = pair
    private = p.normal(lambda core: core.create('DO NOT LEAK PARENT DATABASE')['id'])
    configure(p, private, [{'id': 'price', 'name': 'Price', 'type': 'number'},
                           {'id': 'double', 'name': 'Double', 'type': 'formula', 'expression': 'prop("Price") * 2'}])
    rid = add(p, private, 'Individually shared', {'price': 20})
    other = add(p, private, 'HIDDEN SIBLING', {'price': 999999})
    grant(p, rid, 'editor')
    result = rich_read(p, rid)
    assert result['parent_id'] is None and result['database_id'] is None
    assert result['record_database']['record']['properties']['double'] == 40
    assert all(value not in json.dumps(result) for value in (private, other, 'DO NOT LEAK PARENT DATABASE', 'HIDDEN SIBLING'))
    remote(p, 'database_record_update', {'note_id': rid, 'revision': 1, 'version': 1, 'properties': {'price': 21}})
    assert rich_read(p, rid)['record_database']['record']['properties']['double'] == 42
    with pytest.raises(HTTPError):
        remote(p, 'database_read', {'note_id': private, 'revision': 1})


def test_nearest_deny_viewer_locked_ancestor_and_bad_revision_are_enforced(pair):
    p = pair
    grant(p, p.db, 'viewer')
    assert remote(p, 'database_read', {'note_id': p.db, 'revision': 1})['role'] == 'viewer'
    with pytest.raises(HTTPError):
        remote(p, 'database_record_create', {'note_id': p.db, 'revision': 1, 'title': 'Forbidden', 'properties': {}})
    grant(p, p.db, 'none')
    assert all(row['id'] != p.db for row in remote(p, 'database_catalog', {})['databases'])
    with pytest.raises(HTTPError):
        remote(p, 'database_read', {'note_id': p.db, 'revision': 1})
    grant(p, p.db, 'editor')
    with pytest.raises(HTTPError):
        remote(p, 'database_read', {'note_id': p.db, 'revision': 2})
    with p.backend.connection() as c:
        c.execute('UPDATE notes SET locked=1 WHERE id=?', (NOTE,))
    assert remote(p, 'database_catalog', {})['databases'] == []
    with pytest.raises(HTTPError):
        remote(p, 'database_read', {'note_id': p.db, 'revision': 1})


def test_remote_record_delete_does_not_rewrite_unshared_backlink_pages(pair):
    p=pair;rid=add(p,p.db,'Shared record',{})
    from app.collaboration import load_doc
    from app.share_editing import replace_content
    blocks=[{'id':'link','type':'pageLink','props':{'pageId':rid,'title':'Shared record'},'children':[]}]
    title,body,blocks=replace_content(load_doc(None),'Private backlink page',blocks,fail)
    private=uuid4().hex[:16]
    with p.backend.connection() as c:
        c.execute('INSERT INTO notes(id,user_id,title,body,blocks,created,updated) VALUES(?,?,?,?,?,?,?)',
                  (private,UID,title,body,json.dumps(blocks),1,1))
    with p.service.state.transaction() as c:
        result=note_execute(p.service,c,p.peer,intent(p,'delete',{'note_id':rid,'revision':1}))[0]
    assert result['deleted']
    with p.backend.connection() as c:
        row=c.execute('SELECT body,blocks,version FROM notes WHERE id=?',(private,)).fetchone()
        assert tuple(row)==(body,json.dumps(blocks),1)


def test_database_delete_ignores_already_trashed_children_but_rejects_protected_live_rows(pair):
    p=pair
    trashed=add(p,p.db,'Already trashed',{})
    protected=add(p,p.db,'Protected live row',{})
    with p.service.state.transaction() as c:
        note_execute(p.service,c,p.peer,intent(p,'delete',{'note_id':trashed,'revision':1}))
    grant(p,protected,'viewer')
    with pytest.raises(FederationError):
        with p.service.state.transaction() as c:
            note_execute(p.service,c,p.peer,intent(p,'delete',{'note_id':p.db,'revision':1}))
    with p.backend.connection() as c:
        assert c.execute('SELECT deleted FROM notes WHERE id=?',(p.db,)).fetchone()[0] is None
        old_batch=c.execute('SELECT trash_batch FROM notes WHERE id=?',(trashed,)).fetchone()[0]
    grant(p,protected,'editor')
    with p.service.state.transaction() as c:
        assert note_execute(p.service,c,p.peer,intent(p,'delete',{'note_id':p.db,'revision':1}))[0]['deleted']
    with p.backend.connection() as c:
        assert c.execute('SELECT deleted FROM notes WHERE id=?',(p.db,)).fetchone()[0]
        assert c.execute('SELECT trash_batch FROM notes WHERE id=?',(trashed,)).fetchone()[0]==old_batch


def test_dependency_auto_shift_cannot_write_viewer_or_use_hidden_rows_and_rejects_cycles(pair):
    p = pair
    props = [{'id': 'date', 'name': 'Date', 'type': 'date'}, {'id': 'before', 'name': 'Before', 'type': 'dependency'}]
    views = [{'id': 'timeline', 'name': 'Timeline', 'type': 'timeline', 'date_by': 'date', 'dependency_by': 'before', 'auto_shift': True}]
    configure(p, p.db, props, views)
    a = add(p, p.db, 'A', {'date': {'start': '2026-10-01', 'end': '2026-10-03'}})
    b = add(p, p.db, 'B', {'date': {'start': '2026-10-04'}, 'before': [a]})
    hidden = add(p, p.db, 'HIDDEN', {'date': {'start': '2026-10-10'}, 'before': [a]})
    grant(p, b, 'viewer'); grant(p, hidden, 'none')
    change = {'note_id': a, 'revision': 1, 'version': 1, 'properties': {'date': {'start': '2026-10-01', 'end': '2026-10-06'}}}
    with pytest.raises(HTTPError): remote(p, 'database_record_update', change)
    grant(p, b, 'editor')
    remote(p, 'database_record_update', change)
    rows = remote(p, 'database_read', {'note_id': p.db, 'revision': 1})['records']
    assert next(row for row in rows if row['note_id'] == b)['properties']['date']['start'] == '2026-10-07'
    with p.backend.connection() as c:
        assert json.loads(c.execute('SELECT properties FROM database_records WHERE note_id=?', (hidden,)).fetchone()[0])['date']['start'] == '2026-10-10'
    with pytest.raises(HTTPError) as cycle:
        remote(p, 'database_record_update', {'note_id': a, 'revision': 1, 'version': 2, 'properties': {'before': [b]}})
    assert cycle.value.status_code == 422


def test_new_dependency_cannot_create_a_cycle_through_an_unshared_intermediate_record(pair):
    p=pair
    configure(p,p.db,[{'id':'before','name':'Before','type':'dependency'}])
    b=add(p,p.db,'B',{})
    hidden=add(p,p.db,'Private intermediate',{'before':[b]})
    a=add(p,p.db,'A',{'before':[hidden]})
    grant(p,hidden,'none')
    with pytest.raises(HTTPError) as denied:
        remote(p,'database_record_update',{'note_id':b,'revision':1,'version':1,'properties':{'before':[a]}})
    assert denied.value.status_code == 403 and hidden not in str(denied.value)
    with p.backend.connection() as c:
        assert json.loads(c.execute('SELECT properties FROM database_records WHERE note_id=?',(b,)).fetchone()[0]) == {}


def test_signed_idempotent_configuration_rebuilds_redacted_result_after_related_revoke(pair):
    p = pair
    target = p.normal(lambda core: core.create('Target', NOTE)['id'])
    hidden = add(p, target, 'Formerly visible', {})
    p.service.incoming = lambda raw: (p.peer, None)
    p.service.signed_result = lambda peer, verified, value: value
    app = Application(__name__); app.include_router(p.service.router)
    client = app.test_client()
    payload = {'note_id': p.db, 'revision': 1, 'version': 1,
               'properties': [{'id': 'link', 'name': 'Link', 'type': 'relation', 'database_id': target}],
               'views': [{'id': 'table', 'name': 'Table', 'type': 'table'}]}
    event = intent(p, 'database_configure', payload)
    def send():
        nonlocal event
        with p.service.state.transaction() as c:
            nonce = p.service.state.challenge(c, p.peer.server_id, int(time.time()))
        event = event.model_copy(update={'nonce': nonce})
        proof = p.keys.sign(p.card, p.service.identity.descriptor.server_id, event.model_dump(), int(time.time()))
        response = client.post(OPERATION_PATH, json={'intent': event.model_dump(), 'proof': proof})
        assert response.status_code == 200, response.text
        verify_result(p.service.identity.descriptor,p.card,event.model_dump(),response.get_json(),int(time.time()))
        return p.keys.open(response.get_json(), event.model_dump())
    assert target in json.dumps(send())
    grant(p, target, 'none')
    encoded = json.dumps(send())
    assert target not in encoded and hidden not in encoded
    with p.service.state.transaction() as c:
        assert json.loads(c.execute('SELECT result FROM events WHERE id=?', (event.event_id,)).fetchone()[0]) == {}


def test_legacy_record_viewer_reads_without_writes_and_editor_initializes_with_history(pair):
    p = pair
    rid = add(p, p.db, 'Legacy record', {})
    old = '## Legacy heading\n\n- First\n- Second\n'
    with p.backend.connection() as c:
        c.execute('UPDATE notes SET body=?,blocks=NULL WHERE id=?',(old,rid))
    grant(p,rid,'viewer')
    before = rich_read(p,rid)
    assert before['legacy'] and before['body'] == old and 'state' not in before
    with p.backend.connection() as c:
        assert c.execute('SELECT blocks FROM notes WHERE id=?',(rid,)).fetchone()[0] is None
        assert not c.execute('SELECT 1 FROM collaborative_docs WHERE note_id=?',(rid,)).fetchone()
    blocks = [{'id':'heading','type':'heading','props':{'level':2},'content':'Legacy heading','children':[]},
              {'id':'first','type':'bulletListItem','props':{},'content':'First','children':[]},
              {'id':'second','type':'bulletListItem','props':{},'content':'Second','children':[]}]
    payload = {'note_id':rid,'revision':1,'version':1,'blocks':blocks}
    with pytest.raises(HTTPError):remote(p,'database_record_initialize',payload)
    grant(p,rid,'editor')
    result = remote(p,'database_record_initialize',payload)
    assert result['epoch'] and result['state'] and not result.get('legacy') and result['version'] == 2
    assert [block['type'] for block in result['blocks']] == ['heading','bulletListItem','bulletListItem']
    with p.backend.connection() as c:
        assert c.execute('SELECT body FROM note_revisions WHERE note_id=?',(rid,)).fetchone()[0] == old
    with pytest.raises(HTTPError) as conflict:remote(p,'database_record_initialize',payload)
    assert conflict.value.status_code == 409


def test_legacy_initialization_rejects_private_asset_laundering_and_stale_versions(pair):
    p = pair
    rid = add(p,p.db,'Legacy',{})
    with p.backend.connection() as c:
        c.execute("UPDATE notes SET body='Original Markdown',blocks=NULL WHERE id=?",(rid,))
    payload = {'note_id':rid,'revision':1,'version':1,'blocks':[{'id':'image','type':'image','props':{'url':'https://home.example/api/media/'+'b'*32},'children':[]}]}
    with pytest.raises((FederationError,HTTPError)):remote(p,'database_record_initialize',payload)
    with p.backend.connection() as c:
        assert c.execute('SELECT body,blocks,version FROM notes WHERE id=?',(rid,)).fetchone()[0] == 'Original Markdown'
        assert not c.execute('SELECT 1 FROM collaborative_docs WHERE note_id=?',(rid,)).fetchone()


def attachment(p,nid):
    from app.file_store import file_store
    fid=uuid4().hex[:16];object_id=uuid4().hex;body=b'Private legacy attachment payload'
    writer=file_store(p.backend.connection,p.backend.root).writer(object_id)
    writer.write(body);writer.finish()
    with p.backend.connection() as c:
        c.execute('INSERT INTO files(id,user_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?)',
                  (nid,UID,'folder','Attachments','application/x-drg-note;space=private',1,1))
        c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (fid,UID,nid,'file','legacy.txt','text/plain',len(body),object_id,1,1))
    return fid,body


def test_legacy_initialization_cannot_launder_a_guessed_private_note_file(pair):
    p=pair
    source=p.normal(lambda core:core.create('Private source')['id'])
    fid,_=attachment(p,source)
    rid=add(p,p.db,'Legacy',{})
    with p.backend.connection() as c:c.execute("UPDATE notes SET body='Original',blocks=NULL WHERE id=?",(rid,))
    blocks=[{'id':'file','type':'file','props':{'url':'https://home.example/api/notes/'+source+'/files/'+fid,'name':'guessed.txt'},'children':[]}]
    with pytest.raises(HTTPError) as denied:
        remote(p,'database_record_initialize',{'note_id':rid,'revision':1,'version':1,'blocks':blocks})
    assert denied.value.status_code==403
    with p.backend.connection() as c:
        assert c.execute('SELECT body FROM notes WHERE id=?',(rid,)).fetchone()[0]=='Original'
        assert not c.execute('SELECT 1 FROM collaborative_docs WHERE note_id=?',(rid,)).fetchone()


def test_viewer_reads_existing_legacy_attachment_without_creating_a_document_or_mutating_body(pair):
    p=pair;rid=add(p,p.db,'Legacy attachment',{})
    fid,body=attachment(p,rid)
    original='[Original attachment](/api/notes/'+rid+'/files/'+fid+')'
    with p.backend.connection() as c:c.execute('UPDATE notes SET body=?,blocks=NULL WHERE id=?',(original,rid))
    grant(p,rid,'viewer')
    with p.service.state.transaction() as c:
        result=note_execute(p.service,c,p.peer,intent(p,'asset_read',{'note_id':rid,'revision':1,'asset_id':fid,'offset':0,'length':262144}))[0]
    assert base64.b64decode(result['body'])==body
    with p.backend.connection() as c:
        row=c.execute('SELECT body,blocks,version FROM notes WHERE id=?',(rid,)).fetchone()
        assert tuple(row)==(original,None,1)
        assert not c.execute('SELECT 1 FROM collaborative_docs WHERE note_id=?',(rid,)).fetchone()


def test_legacy_normalization_preserves_device_intent_and_signed_response_binding(pair):
    p=pair;rid=add(p,p.db,'Legacy',{})
    with p.backend.connection() as c:c.execute("UPDATE notes SET body='Original text',blocks=NULL WHERE id=?",(rid,))
    p.service.incoming=lambda raw:(p.peer,None)
    p.service.signed_result=lambda peer,verified,value:value
    p.service.m.app=SimpleNamespace(runtime=SimpleNamespace(run=lambda event:None))
    p.service.m.realtime_router=SimpleNamespace(publication_update=lambda *event:event)
    app=Application(__name__);app.include_router(p.service.router)
    with p.service.state.transaction() as c:nonce=p.service.state.challenge(c,p.peer.server_id,int(time.time()))
    payload={'note_id':rid,'revision':1,'version':1,'blocks':[{'id':'paragraph','type':'paragraph','props':{'linkDisplay':'auto'},'content':[{'type':'text','text':'Original text','styles':{}}],'children':[]}]}
    event=intent(p,'database_record_initialize',payload).model_copy(update={'nonce':nonce})
    original=copy.deepcopy(event.model_dump());before=canonical(original)
    proof=p.keys.sign(p.card,p.service.identity.descriptor.server_id,original,int(time.time()))
    response=app.test_client().post(OPERATION_PATH,json={'intent':original,'proof':proof})
    assert response.status_code==200,response.text
    envelope=response.get_json()
    verify_result(p.service.identity.descriptor,p.card,original,envelope,int(time.time()))
    result=p.keys.open(envelope,original)
    assert result['state'] and 'linkDisplay' not in result['blocks'][0]['props']
    assert canonical(event.model_dump())==before


def test_owner_inherited_record_is_collaborative_before_peer_read_and_connection_cache_invalidates(pair):
    p=pair;rid=add(p,p.db,'Owner opens first',{})
    from app.federation.notes import collaborative
    from app.collaboration import load_doc
    from app.share_editing import replace_content
    with p.backend.connection() as c:
        page=c.execute('SELECT * FROM notes WHERE id=?',(rid,)).fetchone()
        assert not c.execute('SELECT 1 FROM collaborative_docs WHERE note_id=?',(rid,)).fetchone()
        calls=[];execute=c._execute
        def counted(query,parameters=()):
            if query.startswith('WITH RECURSIVE inherited'):calls.append(query)
            return execute(query,parameters)
        c._execute=counted
        assert all(collaborative(c,page) for _ in range(10))
        assert len(calls)==1
        c.execute('UPDATE federation_grants SET active=0 WHERE note_id=?',(NOTE,))
        assert not collaborative(c,page)
        # An existing parent's private CRDT does not implicitly share descendants.
        assert collaborative(c,c.execute('SELECT * FROM notes WHERE id=?',(NOTE,)).fetchone())
        c.execute('UPDATE federation_grants SET active=1 WHERE note_id=?',(NOTE,))
        assert collaborative(c,page)
        c.execute('UPDATE notes SET parent_id=NULL WHERE id=?',(p.db,))
        assert not collaborative(c,page)
        doc=load_doc(None);replace_content(doc,'Owner opens first',[{'id':'text','type':'paragraph','content':'Owner text','children':[]}],fail)
        c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?)',(rid,doc.get_update(),'epoch',1))
        assert collaborative(c,page)
        c.execute('DELETE FROM collaborative_docs WHERE note_id=?',(rid,))
        assert not collaborative(c,page)
