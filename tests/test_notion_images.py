import asyncio
import hashlib
import json
import pytest
from tests.test_security import owner,member,reset
from tests.test_widgets import image_bytes
from app import main as m, notion_images
from app.file_store import file_store
from app.http import HTTPError
from app.notion_images import Download,download_images,repair_images


def download(body):return Download(body,'image/png','image.png',hashlib.sha256(body).hexdigest())


def test_existing_page_image_repair_is_private_atomic_and_idempotent():
    c,_,_=owner();g,_=member(c);uid=c.get('/api/me').json()['id'];url='https://images.example/one.png';body=image_bytes()
    blocks=[{'id':'img','type':'image','props':{'url':url,'caption':'Caption'}},{'id':'code','type':'codeBlock','content':'![code]('+url+')'}]
    p=c.post('/api/notes',json={'title':'Original','blocks':blocks,'body':'![Caption]('+url+')\n```\n![Code]('+url+')\n```'}).json()
    c.put('/api/notes/'+p['id'],json={'title':'Edited','blocks':blocks,'body':p['body'],'version':p['version']})
    foreign=g.post('/api/notes',json={'title':'Foreign','blocks':blocks}).json()
    store=file_store(m.db,m.DATA);before=store.ids()
    with m.db() as db:db.execute('UPDATE user_limits SET limit_bytes=used_bytes+10 WHERE user_id=?',(uid,))
    with pytest.raises(HTTPError):repair_images(m.db,store,uid,{url:download(body)},m.now,m.PUBLIC_URL)
    assert store.ids()==before and c.get('/api/notes/'+p['id']).json()['blocks']==blocks
    with m.db() as db:db.execute('UPDATE user_limits SET limit_bytes=? WHERE user_id=?',(1024**3,uid))
    report=repair_images(m.db,store,uid,{url:download(body)},m.now,m.PUBLIC_URL)
    assert report=={'pages':[p['id']],'files':1,'localized_urls':1}
    result=c.get('/api/notes/'+p['id']).json();image=result['blocks'][0]
    assert result['title']=='Edited' and image['id']=='img' and image['props']['caption']=='Caption'
    assert c.get(image['props']['url']).content==body
    assert g.get(image['props']['url']).status_code==404
    assert result['blocks'][1]==blocks[1] and '![Code]('+url+')' in result['body']
    assert g.get('/api/notes/'+foreign['id']).json()==foreign
    revision=c.get('/api/notes/'+p['id']+'/history').json()[0]
    assert c.get('/api/notes/'+p['id']+'/history/'+revision['id']).json()['blocks'][0]['props']['url']==image['props']['url']
    assert repair_images(m.db,store,uid,{url:download(body)},m.now,m.PUBLIC_URL)=={'pages':[],'files':0,'localized_urls':0}
    assert len(store.ids()-before)==1


def test_code_only_url_does_not_create_unused_attachment():
    c,_,_=owner();uid=c.get('/api/me').json()['id'];url='https://images.example/code.png'
    c.post('/api/notes',json={'title':'Code','body':'```\n![example]('+url+')\n```','blocks':[{'type':'codeBlock','content':url}]})
    store=file_store(m.db,m.DATA);before=store.ids()
    assert repair_images(m.db,store,uid,{url:download(image_bytes())},m.now,m.PUBLIC_URL)['files']==0
    assert store.ids()==before


def test_invalid_image_and_download_budget_leave_sources_in_failure_report(monkeypatch):
    async def fetch(url,**kwargs):return (b'<html>not image</html>' if url.endswith('bad') else image_bytes()),url,'image/png'
    monkeypatch.setattr(notion_images,'fetch_public',fetch)
    data,failed=asyncio.run(download_images(['https://images.example/good','https://images.example/bad']))
    assert list(data)==['https://images.example/good'] and failed==['https://images.example/bad']
    monkeypatch.setattr(notion_images,'MAX_TOTAL',1)
    data,failed=asyncio.run(download_images(['https://images.example/good']))
    assert not data and failed==['https://images.example/good']


def test_malformed_remote_port_does_not_abort_other_downloads(monkeypatch):
    async def unavailable(*args,**kwargs):raise ValueError('Invalid URL')
    monkeypatch.setattr(notion_images,'fetch_public',unavailable)
    url='https://avatars.mds.yandex.net:bad/get-zen_doc/image'
    data,failed=asyncio.run(download_images([url]));assert data=={} and failed==[url]
    assert notion_images.candidates('https://avatars.mds.yandex.net.evil.example/get-zen_doc/image')==['https://avatars.mds.yandex.net.evil.example/get-zen_doc/image']


def test_games_image_moved_to_vkplay_keeps_original_identity(monkeypatch):
    original='https://games.mail.ru/hotbox/content_files/article/2018/3/image.jpeg'
    moved='https://media.vkplay.ru/hotbox/content_files/article/2018/3/image.jpeg'
    body=image_bytes()
    async def fetch(url,**kwargs):
        if url==moved:return body,url,'image/png'
        raise OSError('Legacy CDN unavailable')
    monkeypatch.setattr(notion_images,'fetch_public',fetch)
    data,failed=asyncio.run(download_images([original]))
    assert not failed and list(data)==[original] and data[original].body==body
    assert notion_images.candidates('https://games.mail.ru.evil.example/hotbox/content_files/image.jpeg')==['https://games.mail.ru.evil.example/hotbox/content_files/image.jpeg']


def test_current_cdn_priority_is_opt_in_and_preserves_original_export_identity():
    original='https://games.mail.ru/hotbox/content_files/image.jpeg?version=1'
    current='https://media.vkplay.ru/hotbox/content_files/image.jpeg?version=1'
    assert notion_images.candidates(original)==[original,current]
    assert notion_images.candidates(original,prefer_current=True)==[current,original]
    signed='https://files.example/a.png?X-Amz-Signature=keep'
    assert notion_images.candidates(signed,prefer_current=True)==[signed]


def test_flaticon_move_keeps_size_asset_identity_and_query_and_is_host_path_scoped():
    original='https://image.flaticon.com/icons/png/512/25/25231.png?version=1'
    current='https://cdn-icons-png.flaticon.com/512/25/25231.png?version=1'
    assert notion_images.candidates(original)==[original,current]
    assert notion_images.candidates(original,prefer_current=True)==[current,original]
    for url in (
        'https://image.flaticon.com.evil.example/icons/png/512/25/25231.png',
        'https://user@image.flaticon.com/icons/png/512/25/25231.png',
        'https://image.flaticon.com:8443/icons/png/512/25/25231.png',
        'https://image.flaticon.com/icons/png/512/../25231.png',
        'https://image.flaticon.com/icons/png/512/25/25231.svg',
        'https://image.flaticon.com:bad/icons/png/512/25/25231.png',
        'ftp://image.flaticon.com/icons/png/512/25/25231.png',
    ):
        assert notion_images.candidates(url,prefer_current=True)==[url]
