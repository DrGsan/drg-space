import copy
import json
import httpx
import pytest
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,imported,ROOT,CHILD,TEXT,NEW,paragraph,rt
from app import main as m
from app.notion_sync.client import NotionError
from app.notion_sync.state import dump


def enabled(mode='success',empty=False,texts=None):
    c,s,provider,service,stamp=initialized();provider.blocks[ROOT]=[paragraph('Hello')]
    if texts is not None:
        import uuid
        provider.blocks[ROOT]=[]
        for index,text in enumerate(texts):
            block=paragraph(text);block['id']=TEXT if index==0 else str(uuid.uuid5(uuid.NAMESPACE_URL,'source-paragraph:'+str(index)));provider.blocks[ROOT].append(block)
    if empty:provider.blocks[ROOT].append({'id':CHILD,'type':'paragraph','paragraph':{'rich_text':[]},'has_children':False})
    identity,mapping=imported(service,s);original=provider.handle;patches=[]
    def md(block):
        items=block['paragraph']['rich_text']
        if not items or not items[0]['text']['content']:return '<p />'
        item=items[0];value=item['text']['content']
        return '**'+value+'**' if item.get('annotations',{}).get('bold') else value
    def respond(request):
        path=request.url.path
        if path.endswith('/markdown'):
            provider.requests.append((request.method,path))
            if request.method=='PATCH':
                patches.append(json.loads(request.content));operation=patches[-1]['update_content']['content_updates'][0]
                if mode=='forbidden':return httpx.Response(403)
                if mode=='race':provider.blocks[ROOT][0]['paragraph']['rich_text']=rt('Human changed it')
                document='\n\n'.join(md(block) for block in provider.blocks[ROOT])
                if document.count(operation['old_str'])!=1:return httpx.Response(400,json={'code':'validation_error'})
                import uuid
                updated=document.replace(operation['old_str'],operation['new_str'],1);blocks=[]
                for index,value in enumerate(updated.split('\n\n')):
                    bold=value.startswith('**') and value.endswith('**');block=paragraph(value[2:-2] if bold else '' if value=='<p />' else value)
                    block['paragraph']['rich_text'][0]['annotations']['bold']=bold
                    block['id']=NEW if index==0 else str(uuid.uuid5(uuid.NAMESPACE_URL,'fixture:'+str(index)+value));blocks.append(block)
                provider.blocks[ROOT]=blocks
                if mode=='cursor':provider.blocks[ROOT].append({'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'notion-cursor'+str(len(blocks)))),'type':'paragraph','paragraph':{'rich_text':[]},'has_children':False})
                if mode=='uncertain':return httpx.Response(503,json={'code':'service_unavailable'})
            identity=path.split('/')[3]
            selected=provider.blocks[ROOT] if identity==ROOT else [block for block in provider.blocks[ROOT] if block['id']==identity]
            return httpx.Response(200,json={'object':'page_markdown','markdown':'\n\n'.join(md(block) for block in selected),'truncated':False,'unknown_block_ids':[]})
        return original(request)
    service.connections.transport=httpx.MockTransport(respond)
    root=service.status(s['user_id'])['roots'][0]
    service.experimental(s['user_id'],identity,root['version'],True,'read')
    return c,s,provider,service,stamp,identity,mapping,patches


def edit(c,nid,value):
    page=c.get('/api/notes/'+nid).json();assert page['role']=='editor'
    blocks=copy.deepcopy(page['blocks']);blocks[0]['content'][0]['text']=value
    response=c.put('/api/notes/'+nid,json={'title':page['title'],'body':value,'blocks':blocks,'version':page['version']})
    assert response.status_code==200,response.text
    return response.json()


def test_explicit_two_way_uses_conditional_leaf_patch_and_ack_does_not_bump_document_version():
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    first=edit(c,mapping[ROOT],'First change');stamp[0]+=10
    assert service.work_once();assert not service.work_once()
    assert len(patches)==1
    assert patches[0]['type']=='update_content' and patches[0]['allow_async'] is False
    assert patches[0]['update_content']['content_updates']==[{'old_str':'**Hello**','new_str':'**First change**'}]
    after=c.get('/api/notes/'+mapping[ROOT]).json()
    assert after['version']==first['version'] and after['blocks'][0]['id']==TEXT
    second=edit(c,mapping[ROOT],'Second change');stamp[0]+=10
    assert service.work_once()
    assert len(patches)==2 and c.get('/api/notes/'+mapping[ROOT]).json()['version']==second['version']
    with m.db() as db:
        state=json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(identity,)).fetchone()[0])
        assert state['block_map'][TEXT]==NEW and 'outbound' not in state


def added(identity,text):return {'id':identity,'type':'paragraph','props':{'textColor':'default','backgroundColor':'default','textAlignment':'left','title':'','description':'','url':''},'content':[{'type':'text','text':text,'styles':{}}],'children':[]}


@pytest.mark.parametrize('position',['before','after'])
def test_new_paragraph_uses_unique_existing_anchor_and_next_edit_keeps_mapping(position):
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(page['blocks']);new=added('local-new-paragraph','New paragraph')
    # A real BlockNote round trip populates the shared paragraph schema's
    # empty bookmark fields on existing paragraphs, not just inserted ones.
    blocks[0].setdefault('props',{}).update(title='',description='',url='')
    blocks.insert(0 if position=='before' else 1,new);blocks.append(added('editor-cursor',''))
    result=c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'local','blocks':blocks,'version':page['version']});assert result.status_code==200
    stamp[0]+=3;assert service.work_once();assert not service.work_once()
    assert len(patches)==1 and patches[0]['update_content']['content_updates'][0]['old_str']=='**Hello**'
    assert [b['paragraph']['rich_text'][0]['text']['content'] for b in provider.blocks[ROOT]]==(['New paragraph','Hello'] if position=='before' else ['Hello','New paragraph'])
    saved=c.get('/api/notes/'+page['id']).json();assert len(saved['blocks'])==3
    changed=copy.deepcopy(saved['blocks']);changed[0 if position=='before' else 1]['content'][0]['text']='Changed inserted paragraph'
    assert c.put('/api/notes/'+page['id'],json={'title':saved['title'],'body':'changed','blocks':changed,'version':saved['version']}).status_code==200
    stamp[0]+=3;assert service.work_once();assert len(patches)==2
    assert any(b['paragraph']['rich_text'][0]['text']['content']=='Changed inserted paragraph' for b in provider.blocks[ROOT])


def test_new_empty_editor_cursor_does_not_call_notion_or_pause_and_later_text_is_sent():
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(page['blocks'])+[added('cursor','')]
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':page['body'],'blocks':blocks,'version':page['version']}).status_code==200
    count=len(provider.requests);stamp[0]+=3;assert service.work_once();assert not service.work_once()
    assert len(provider.requests)==count and not patches and service.status(s['user_id'])['roots'][0]['status']=='active'
    page=c.get('/api/notes/'+page['id']).json();blocks[-1]['content'][0]['text']='Cursor becomes actual text'
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'text','blocks':blocks,'version':page['version']}).status_code==200
    stamp[0]+=3;assert service.work_once();assert len(patches)==1


def test_actual_web_paragraph_defaults_and_original_empty_block_are_supported():
    c,s,provider,service,stamp,identity,mapping,patches=enabled(empty=True)
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(page['blocks'])
    for block in blocks:block.setdefault('props',{}).update(title='',description='',url='')
    blocks[1]['content']=[{'type':'text','text':'Тест','styles':{}}];blocks.append(added('editor-tail',''))
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'Hello\n\nТест','blocks':blocks,'version':page['version']}).status_code==200
    stamp[0]+=3;assert service.work_once();assert not service.work_once()
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    assert patches[0]['update_content']['content_updates']==[{'old_str':'<p />','new_str':'Тест'}]
    assert len(provider.blocks[ROOT])==2 and provider.blocks[ROOT][1]['paragraph']['rich_text'][0]['text']['content']=='Тест'


def test_notion_materialized_cursor_is_acknowledged_and_can_be_filled_next():
    c,s,provider,service,stamp,identity,mapping,patches=enabled('cursor')
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(page['blocks'])+[added('new-text','123'),added('web-cursor','')]
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'Hello\n\n123','blocks':blocks,'version':page['version']}).status_code==200
    stamp[0]+=3;assert service.work_once();assert service.status(s['user_id'])['roots'][0]['status']=='active'
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(identity,)).fetchone()[0]);assert 'web-cursor' in value['block_map']
    page=c.get('/api/notes/'+page['id']).json();blocks[-1]['content'][0]['text']='Cursor is now actual text';blocks.append(added('next-web-cursor',''))
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'text','blocks':blocks,'version':page['version']}).status_code==200
    stamp[0]+=3;assert service.work_once();assert service.status(s['user_id'])['roots'][0]['status']=='active'
    assert patches[-1]['update_content']['content_updates'][0]['old_str']=='<p />'


@pytest.mark.parametrize('guard',['none','uncertain','local','remote','role','grant'])
def test_completed_write_recovers_only_when_all_confirmed_values_still_match(monkeypatch,guard):
    from app.notion_sync import outbound
    c,s,provider,service,stamp,identity,mapping,patches=enabled('cursor')
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(page['blocks'])+[added('new-text','123'),added('web-cursor','')]
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'Hello\n\n123','blocks':blocks,'version':page['version']}).status_code==200
    original=outbound.verified_document;monkeypatch.setattr(outbound,'verified_document',lambda *args:None)
    stamp[0]+=3;assert service.work_once();monkeypatch.setattr(outbound,'verified_document',original)
    with m.db() as db:
        row=db.execute('SELECT id,payload FROM notion_pages WHERE root_id=?',(identity,)).fetchone();value=json.loads(row['payload'])
        if guard=='uncertain':value['outbound']['state']='sending'
        if guard=='remote':value['conflict']['remote']['blocks'][0]['paragraph']['rich_text']=rt('Genuine external change')
        if guard in ('uncertain','remote'):db.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(value),row['id']))
        if guard=='local':db.execute('UPDATE notes SET title=? WHERE id=?',('Different local title',page['id']))
        if guard=='role':db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
        if guard=='grant':db.execute("UPDATE notion_connections SET status='reauthorize' WHERE kind='read'")
        version=db.execute('SELECT version FROM notes WHERE id=?',(page['id'],)).fetchone()[0]
    count=len(provider.requests);result=service.status(s['user_id'])['roots'][0]
    assert result['status']==('active' if guard=='none' else 'conflict')
    assert len(provider.requests)==count and len(patches)==1
    if guard=='none':
        assert service.review_conflict(s['user_id'],identity)['resolved']
        with m.db() as db:
            assert not db.execute('SELECT 1 FROM notion_jobs WHERE root_id=?',(identity,)).fetchone()
            assert db.execute('SELECT version FROM notes WHERE id=?',(page['id'],)).fetchone()[0]==version
            assert db.execute('SELECT next_scan FROM notion_connections WHERE id=?',('read',)).fetchone()[0]==0


def test_retry_unsupported_write_retains_duplex_and_browser_version():
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    saved=edit(c,mapping[ROOT],'Retry this text');stamp[0]+=3
    # A stopped previous worker version retained this same unsent intent.
    with m.db() as db:
        root=db.execute('SELECT payload FROM notion_roots WHERE id=?',(identity,)).fetchone();value=json.loads(root[0]);value['error']='notionUnsupportedWrite'
        db.execute("UPDATE notion_roots SET status='paused',payload=? WHERE id=?",(dump(value),identity))
        db.execute("UPDATE notion_jobs SET error='notionUnsupportedWrite',due=? WHERE root_id=?",(int(stamp[0])+3600,identity))
    state=service.status(s['user_id'])['roots'][0];service.action(s['user_id'],identity,'resume',state['version'])
    assert c.get('/api/notes/'+saved['id']).json()['version']==saved['version']
    assert service.status(s['user_id'])['roots'][0]['mode']=='duplex'
    assert service.work_once();assert len(patches)==1 and service.status(s['user_id'])['roots'][0].get('error') is None


@pytest.mark.parametrize('change',['heading','nested','colored','card'])
def test_adding_paragraph_support_does_not_allow_other_structural_writes(change):
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(page['blocks']);new=added('inserted','Keep local')
    if change=='heading':new['type']='heading';new['props']={'level':2}
    if change=='nested':new['children']=[added('nested','Child')]
    if change=='colored':new['props']['textColor']='red'
    if change=='card':new['props']['title']='Actual card metadata'
    blocks.append(new)
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'Local structural change','blocks':blocks,'version':page['version']}).status_code==200
    count=len(provider.requests);stamp[0]+=3;assert service.work_once()
    assert len(provider.requests)==count and patches==[]
    result=c.get('/api/notes/'+page['id']).json();assert result['body']=='Local structural change'
    assert result['notion_sync']['status']=='paused' and result['notion_sync']['error']=='notionUnsupportedWrite'


@pytest.mark.parametrize('removed',[(0,),(1,),(3,),(0,1),(1,2),(0,2),(0,1,2,3)])
def test_delete_whole_paragraph_runs_keeps_neighbours_and_acknowledges_cursor(removed):
    values=['Alpha','Beta','Gamma','Delta']
    c,s,provider,service,stamp,identity,mapping,patches=enabled(texts=values)
    page=c.get('/api/notes/'+mapping[ROOT]).json();kept=[b for i,b in enumerate(page['blocks']) if i not in removed]
    blocks=kept+[added('deletion-cursor','')]
    saved=c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'local deletion','blocks':blocks,'version':page['version']});assert saved.status_code==200
    stamp[0]+=3;assert service.work_once();assert not service.work_once()
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    actual=[b['paragraph']['rich_text'][0]['text']['content'] for b in provider.blocks[ROOT]]
    assert actual==([text for i,text in enumerate(values) if i not in removed] or [''])
    assert all(p['update_content']['content_updates'][0]['new_str']=='' for p in patches)
    assert c.get('/api/notes/'+page['id']).json()['version']==saved.json()['version']


def test_deletion_can_be_combined_with_existing_text_edit_and_new_paragraph():
    c,s,provider,service,stamp,identity,mapping,patches=enabled(texts=['Alpha','Beta','Gamma'])
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(page['blocks'][1:]);blocks[0]['content'][0]['text']='Beta updated';blocks.insert(1,added('new-between','Inserted text'))
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'mixed edit','blocks':blocks,'version':page['version']}).status_code==200
    stamp[0]+=3;assert service.work_once()
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    assert [b['paragraph']['rich_text'][0]['text']['content'] for b in provider.blocks[ROOT]]==['Beta updated','Inserted text','Gamma']


@pytest.mark.parametrize('mode',['race','uncertain'])
def test_deletion_race_or_uncertain_response_preserves_intent_without_replay(mode):
    c,s,provider,service,stamp,identity,mapping,patches=enabled(mode,texts=['Alpha','Beta','Gamma'])
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=page['blocks'][1:]
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'local retained','blocks':blocks,'version':page['version']}).status_code==200
    stamp[0]+=3;assert service.work_once();stamp[0]+=1000;assert not service.work_once()
    assert len(patches)==1 and service.status(s['user_id'])['roots'][0]['status']=='conflict'
    assert c.get('/api/notes/'+page['id']).json()['blocks']==blocks


@pytest.mark.parametrize('kind',['pageLink','image','file','heading','nested','card','colored'])
def test_deletion_plan_rejects_other_content_before_any_mutation(kind):
    from app.notion_sync.outbound import plan
    item=added('source','Source text')
    if kind=='nested':item['children']=[added('child','Nested content')]
    elif kind=='card':item['props']['url']='https://example.com'
    elif kind=='colored':item['props']['textColor']='red'
    else:item['type']=kind
    before={'blocks':[item]};after={'blocks':[]}
    with pytest.raises(NotionError,match='notionUnsupportedWrite'):plan(before,after)


def test_deletion_request_budget_counts_every_block_in_a_group():
    from app.notion_sync.outbound import plan
    with pytest.raises(NotionError,match='notionUnsupportedWrite'):
        plan({'blocks':[added(str(i),'Text '+str(i)) for i in range(51)]},{'blocks':[]})


def test_paragraph_insertion_conflict_preserves_both_sides():
    c,s,provider,service,stamp,identity,mapping,patches=enabled('race')
    page=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(page['blocks'])+[added('new','Retained local paragraph')]
    assert c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'Local','blocks':blocks,'version':page['version']}).status_code==200
    stamp[0]+=3;assert service.work_once();stamp[0]+=600;assert not service.work_once()
    assert len(patches)==1 and provider.blocks[ROOT][0]['paragraph']['rich_text'][0]['text']['content']=='Human changed it'
    assert c.get('/api/notes/'+page['id']).json()['blocks'][1]['content'][0]['text']=='Retained local paragraph'
    assert service.status(s['user_id'])['roots'][0]['status']=='conflict'


@pytest.mark.parametrize('mode',['race','uncertain'])
def test_race_or_ambiguous_write_retains_local_version_and_never_blindly_replays(mode):
    c,s,provider,service,stamp,identity,mapping,patches=enabled(mode)
    edit(c,mapping[ROOT],'Local change');stamp[0]+=10
    assert service.work_once();stamp[0]+=1000
    assert not service.work_once() and len(patches)==1
    assert service.status(s['user_id'])['roots'][0]['status']=='conflict'
    page=c.get('/api/notes/'+mapping[ROOT]).json()
    assert page['blocks'][0]['content'][0]['text']=='Local change'
    expected='Human changed it' if mode=='race' else 'Local change'
    assert provider.blocks[ROOT][0]['paragraph']['rich_text'][0]['text']['content']==expected


def test_unsupported_metadata_change_is_retained_locally_and_rejected_before_any_provider_call():
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    page=c.get('/api/notes/'+mapping[ROOT]).json()
    result=c.put('/api/notes/'+mapping[ROOT],json={'title':'Changed title','body':page['body'],'blocks':page['blocks'],'version':page['version']})
    assert result.status_code==200
    before=len(provider.requests);stamp[0]+=10;assert service.work_once()
    assert len(provider.requests)==before and patches==[]
    assert c.get('/api/notes/'+mapping[ROOT]).json()['title']=='Changed title'
    assert service.status(s['user_id'])['roots'][0]['status']=='paused'


def test_other_grant_or_disabled_server_cannot_enable_write():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    root=service.status(s['user_id'])['roots'][0]
    with pytest.raises(NotionError,match='notionWriteForbidden'):service.experimental(s['user_id'],identity,root['version'],True,'missing-write-grant')
    assert service.status(s['user_id'])['roots'][0]['mode']=='pull'


def test_write_permission_denied_does_not_revoke_shared_read_connection():
    c,s,provider,service,stamp,identity,mapping,patches=enabled('forbidden')
    edit(c,mapping[ROOT],'Retained local write');stamp[0]+=3;assert service.work_once()
    root=service.status(s['user_id'])['roots'][0]
    assert root['status']=='paused' and root['error']=='notionWriteAccessRequired'
    with m.db() as db:
        assert db.execute("SELECT status FROM notion_connections WHERE id='read'").fetchone()[0]=='active'
    client=service.connections.client('read')
    try:assert client.request('GET','/v1/pages/'+ROOT)['id']==ROOT
    finally:client.close()
    assert provider.blocks[ROOT][0]['paragraph']['rich_text'][0]['text']['content']=='Hello'
    assert len(patches)==1


def test_shared_token_cannot_write_when_subtree_switch_is_off():
    from app.notion_sync.outbound import guard
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    root=service.status(s['user_id'])['roots'][0];service.experimental(s['user_id'],identity,root['version'],False)
    count=len(provider.requests)
    client=service.connections.client('read',mutation_check=lambda:guard(service,identity,'read'))
    try:
        with pytest.raises(NotionError,match='notionWriteForbidden'):client.request('PATCH','/v1/pages/'+ROOT+'/markdown',{})
    finally:client.close()
    assert len(provider.requests)==count and not patches


def test_switch_disabled_while_waiting_prevents_the_outbound_http_request():
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    edit(c,mapping[ROOT],'Keep this local');stamp[0]+=3;switched=[False]
    def wait(seconds):
        with m.db() as db:value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(identity,)).fetchone()[0])
        if not switched[0] and value.get('outbound',{}).get('state')=='sending':
            root=service.status(s['user_id'])['roots'][0];service.experimental(s['user_id'],identity,root['version'],False);switched[0]=True
        stamp[0]+=seconds
    service.connections.wait=wait
    assert service.work_once() and switched[0]
    assert not patches and service.status(s['user_id'])['roots'][0]['mode']=='pull'
    assert provider.blocks[ROOT][0]['paragraph']['rich_text'][0]['text']['content']=='Hello'


@pytest.mark.parametrize('mode',['pull','duplex'])
def test_primary_reauthorization_preserves_root_and_mode_and_retires_old_write_token(mode):
    from tests.test_notion_sync_connections import session
    from urllib.parse import urlsplit,parse_qs
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    if mode=='pull':
        root=service.status(s['user_id'])['roots'][0];service.experimental(s['user_id'],identity,root['version'],False)
    page=c.get('/api/notes/'+mapping[ROOT]).json()
    with m.db() as db:
        db.execute("INSERT INTO notion_connections(id,user_id,kind,workspace_id,bot_id,payload,created,updated) VALUES('old-write',?,'write','workspace','old-bot',?,0,0)",(s['user_id'],dump({'access_token':'obsolete-test-token'})))
        root=db.execute('SELECT payload FROM notion_roots WHERE id=?',(identity,)).fetchone();value=json.loads(root[0]);value['write_connection_id']='old-write';db.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(value),identity))
    url=service.connections.start(session(c));state=parse_qs(urlsplit(url).query)['state'][0];service.connections.complete(state,'code')
    root=service.status(s['user_id'])['roots'][0]
    assert root['id']==identity and root['connection_id']=='read' and root['mode']==mode
    assert c.get('/api/notes/'+mapping[ROOT]).json()['version']==page['version']
    with m.db() as db:
        assert not db.execute("SELECT 1 FROM notion_connections WHERE id='old-write'").fetchone()
        assert 'write_connection_id' not in json.loads(db.execute('SELECT payload FROM notion_roots WHERE id=?',(identity,)).fetchone()[0])


def test_accepting_source_preserves_an_independent_local_copy_and_retry_is_idempotent():
    c,s,provider,service,stamp,identity,mapping,patches=enabled('race')
    edit(c,mapping[ROOT],'Local change');stamp[0]+=10;assert service.work_once()
    review=service.review_conflict(s['user_id'],identity)
    assert 'Local change' in review['local'] and 'Human changed it' in review['remote']
    result=service.accept_source(s['user_id'],identity,review['note_id'],review['hash'],review['version'])
    assert c.get('/api/notes/'+result['independent_copy']).json()['blocks'][0]['content'][0]['text']=='Local change'
    source=c.get('/api/notes/'+mapping[ROOT]).json()
    assert source['blocks'][0]['content'][0]['text']=='Human changed it' and source['role']=='viewer'
    assert service.status(s['user_id'])['roots'][0]['mode']=='pull'
    replay=service.accept_source(s['user_id'],identity,review['note_id'],review['hash'],review['version'])
    assert replay==result
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==2
    assert len(patches)==1


def test_cached_conflict_is_immediate_but_fresh_review_keeps_external_changes():
    c,s,provider,service,stamp,identity,mapping,patches=enabled('race')
    edit(c,mapping[ROOT],'Local change');stamp[0]+=10;assert service.work_once()
    count=len(provider.requests)
    cached=service.review_conflict(s['user_id'],identity,False)
    assert cached['cached'] and len(provider.requests)==count
    assert 'Local change' in cached['local'] and 'Hello' in cached['remote']
    provider.blocks[ROOT][0]['paragraph']['rich_text']=rt('Latest external version')
    fresh=service.review_conflict(s['user_id'],identity,True)
    assert not fresh['cached'] and 'Latest external version' in fresh['remote']
    assert len(provider.requests)>count


def test_pause_while_waiting_for_write_budget_prevents_http_and_keeps_grant_valid():
    c,s,provider,service,stamp,identity,mapping,patches=enabled()
    edit(c,mapping[ROOT],'Local change');stamp[0]+=10
    paused=[False]
    def wait(seconds):
        with m.db() as db:state=json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(identity,)).fetchone()[0])
        if not paused[0] and state.get('outbound',{}).get('state')=='sending':
            root=service.status(s['user_id'])['roots'][0]
            service.action(s['user_id'],identity,'pause',root['version']);paused[0]=True
        stamp[0]+=seconds
    service.connections.wait=wait
    assert service.work_once();assert paused[0]
    assert patches==[] and service.status(s['user_id'])['roots'][0]['status']=='paused'
    with m.db() as db:assert db.execute("SELECT status FROM notion_connections WHERE id='read'").fetchone()[0]=='active'
