import asyncio
import io
from PIL import Image
from tests.test_security import owner, member, client, reset
from tests.test_social import family, join
from app.modules import note_links
from app import main as m

def test_metadata_relative_image_encoding_and_bounded_text():
    value, image = note_links.parse_metadata(b'<title>Fallback</title><meta property="og:title" content=" A &amp; B "><meta name="description" content="Readable text"><meta property="og:image" content="/cover.png">','https://example.com/list','text/html; charset=UTF-8')
    assert value == {'title':'A & B','description':'Readable text','site':'example.com'}
    assert image == 'https://example.com/cover.png'
    value, _ = note_links.parse_metadata('<meta charset="windows-1251"><title>Привет</title>'.encode('cp1251'),'https://example.com/','text/html')
    assert value['title'] == 'Привет'

def test_preview_requires_auth_module_and_csrf_and_caches_per_account(monkeypatch):
    c,_,_=owner();g,_=member(c);calls=[]
    image=io.BytesIO();Image.new('RGB',(64,48),'red').save(image,format='PNG')
    async def fetch(url,**kw):
        calls.append(url)
        if url.endswith('.png'): return image.getvalue(), url, 'image/png'
        return b'<meta property="og:title" content="Preview"><meta property="og:image" content="/image.png">',url,'text/html'
    monkeypatch.setattr(note_links,'fetch_public',fetch)
    url='https://example.com/cached-preview'
    assert client().post('/api/note-link-preview',json={'url':url}).status_code==401
    assert c.post('/api/note-link-preview',json={'url':url},headers={'x-csrf-token':'bad'}).status_code==403
    for _ in range(2):
        r=c.post('/api/note-link-preview',json={'url':url});assert r.status_code==200,r.text
        assert r.json()['title']=='Preview' and r.json()['image'].startswith('data:image/jpeg;base64,')
    assert len(calls)==2
    assert g.post('/api/note-link-preview',json={'url':url}).status_code==200
    assert len(calls)==4
    c.put('/api/modules',json={'enabled':{'notes':False}})
    assert c.post('/api/note-link-preview',json={'url':url}).status_code==403

def test_preview_rejects_unsafe_targets_and_falls_back_on_missing_site(monkeypatch):
    c,_,_=owner();calls=[]
    async def fetch(url,**kw):
        calls.append(url);raise ValueError('No public destination')
    monkeypatch.setattr(note_links,'fetch_public',fetch)
    for url in ['file:///etc/passwd','https://user:password@example.com/','https://example.com:444/']:
        r=c.post('/api/note-link-preview',json={'url':url});assert r.status_code==200 and not r.json()['available']
    assert calls==[]
    r=c.post('/api/note-link-preview',json={'url':'https://127.0.0.1/'});assert not r.json()['available']
    from app.safe_fetch import public_addresses
    for address in ['127.0.0.1','169.254.169.254','::1','localhost']:
        try: asyncio.run(public_addresses(address,443))
        except ValueError: pass
        else: raise AssertionError(address)

def test_personal_search_includes_friends_but_keeps_family_separate_and_owners(monkeypatch):
    c,_,_=owner();g,_=member(c)
    request=c.post('/api/social/friends',json={'login':'friend'});assert request.status_code==200,request.text
    accepted=g.post('/api/social/requests/'+request.json()['id']+'/accept');assert accepted.status_code==200,accepted.text
    sid=next(s['id'] for s in c.get('/api/social').json()['spaces'] if s['kind']=='friends')
    fid=family(c);join(c,g,fid)
    def create(user,scope):
        r=user.post('/api/notes',json={'title':'needle','space_id':scope});assert r.status_code==200,r.text;return r.json()
    personal=create(c,None);shared=create(g,sid);family_page=create(c,fid);create(g,None)
    result=c.get('/api/notes/search?q=needle&space_id=personal').json()
    assert {n['id'] for n in result['items']}=={personal['id'],shared['id']}
    assert {n['id'] for n in c.get('/api/notes/search?q=needle&space_id='+fid).json()['items']}=={family_page['id']}
    assert shared['owner']['id']==g.get('/api/me').json()['id']
    permissions=c.get('/api/notes/'+shared['id']+'/permissions').json()
    assert next(u for u in permissions['members'] if u['owner'])['id']==shared['owner']['id']


def test_link_display_preserves_url_and_format_in_storage_and_public_views():
    from tests.test_security import share
    c,_,_=owner()
    url='https://example.com/original'
    for mode in ('auto','url','bookmark','mention'):
        block={'id':'link','type':'paragraph','props':{'linkDisplay':mode},'content':[{'type':'link','href':url,'content':[{'type':'text','text':'Original label','styles':{}}]}],'children':[]}
        response=c.post('/api/notes',json={'title':'Link','blocks':[block]})
        assert response.status_code==200,response.text
        note=response.json();expected={} if mode=='auto' else {'linkDisplay':mode}
        for actual in [note,c.get('/api/notes/'+note['id']).json(),client().get('/api/shared/'+share(c,note['id'],'public')).json()]:
            assert actual['blocks'][0]['props']==expected
            assert actual['blocks'][0]['content']==block['content']
    for invalid in ('embed','',[],None):
        block['props']={'linkDisplay':invalid}
        assert c.post('/api/notes',json={'title':'Invalid','blocks':[block]}).status_code==422
