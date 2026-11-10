"""Explicitly isolated browser fixture; never uses a real Notion credential."""
import os
if os.getenv('DRG_NOTION_BROWSER_TEST')!='1':raise RuntimeError('Isolated browser fixture requires DRG_NOTION_BROWSER_TEST=1')
import httpx
import json,hmac,hashlib
from tests.browser_app import app
from app import main as m
from app.http import Request
from app.notion_sync.config import NotionConfig
from app.notion_sync.state import dump
from app.notion_sync.client import NotionError

ROOT='11111111-1111-4111-8111-111111111111'
CHILD='22222222-2222-4222-8222-222222222222'
DATABASE='88888888-8888-4888-8888-888888888888'
SOURCE='99999999-9999-4999-8999-999999999999'
CURRENT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
ARCHIVED='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
VIEW='cccccccc-cccc-4ccc-8ccc-cccccccccccc'
ARCHIVE_VIEW='dddddddd-dddd-4ddd-8ddd-dddddddddddd'
PENDING='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
database_title='Заметки'
presentation_enabled=False
from app.notion_sync import presentation
real_presentation_index=presentation.index
presentation.index=lambda c,uid:real_presentation_index(c,uid) if presentation_enabled else {'aliases':{},'containers':{}}
source_text='Здесь собраны полезные инструкции для нашей семьи. Изменения приходят из Notion автоматически.'

def rich(value):return [{'type':'text','text':{'content':value},'plain_text':value}]

def provider(request):
    path=request.url.path
    if path=='/v1/search':
        data=json.loads(request.content)
        pages=[{'id':identity,'object':'page','properties':{'title':{'type':'title','title':rich(title)}},'icon':{'type':'emoji','emoji':icon},'parent':{'workspace':True},'last_edited_time':'2026-11-01T00:00:00Z'} for identity,title,icon in [(ROOT,'Семейная библиотека','📚'),(CHILD,'Памятки для Mac','💻'),(CURRENT,'Test','🧨'),(ARCHIVED,'Работа','📝')]]
        if os.getenv('DRG_NOTION_TREE_TEST')=='1':
            pages[0]['properties']['title']['title']=rich('IT')
            pages[1]['parent']={'page_id':ROOT}
            pages.append({'id':DATABASE,'object':'database','title':rich('Заметки'),'parent':{'page_id':ROOT},'data_sources':[{'id':SOURCE}]})
            pages.append({'id':SOURCE,'object':'data_source','title':rich('Заметки'),'parent':{'database_id':DATABASE}})
        return httpx.Response(200,json={'results':[item for item in pages if data.get('query','').lower() in (''.join(part.get('plain_text','') for part in item.get('title',[])) or item.get('properties',{}).get('title',{}).get('title',[{'text':{'content':''}}])[0].get('text',{}).get('content','')).lower()],'has_more':False})
    if path=='/v1/pages/'+DATABASE:return httpx.Response(400,json={'code':'validation_error'})
    if path=='/v1/databases/'+DATABASE:return httpx.Response(200,json={'id':DATABASE,'object':'database','title':rich(database_title),'data_sources':[{'id':SOURCE,'name':database_title}]})
    if path=='/v1/data_sources/'+SOURCE:return httpx.Response(200,json={'id':SOURCE,'object':'data_source','title':rich(database_title),'parent':{'database_id':DATABASE},'properties':{'Название':{'id':'title','type':'title'},'Тег':{'id':'tag','type':'multi_select','multi_select':{'options':[{'name':'Архив'}]}}}})
    def record(identity):return {'id':identity,'object':'page','last_edited_time':'2026-11-01T00:00:00Z','parent':{'type':'data_source_id','data_source_id':SOURCE},'properties':{'Название':{'id':'title','type':'title','title':rich('Текущая запись' if identity==CURRENT else 'Архивная запись' if identity==ARCHIVED else 'Ожидающая запись')},'Тег':{'id':'tag','type':'multi_select','multi_select':[] if identity==CURRENT else [{'name':'Архив'}]}}}
    if path=='/v1/data_sources/'+SOURCE+'/query':return httpx.Response(200,json={'results':[record(CURRENT),record(ARCHIVED),record(PENDING)],'has_more':False})
    if path in ('/v1/pages/'+CURRENT,'/v1/pages/'+ARCHIVED,'/v1/pages/'+PENDING):return httpx.Response(200,json=record(path.rsplit('/',1)[-1]))
    if path=='/v1/views':return httpx.Response(200,json={'results':[{'id':VIEW},{'id':ARCHIVE_VIEW}],'has_more':False})
    if path.startswith('/v1/views/'):
        archived=path.endswith(ARCHIVE_VIEW)
        return httpx.Response(200,json={'id':ARCHIVE_VIEW if archived else VIEW,'parent':{'database_id':DATABASE},'data_source_id':SOURCE,'name':'Архив' if archived else 'Заметки','type':'list','filter':{'property':'tag','multi_select':{'contains' if archived else 'does_not_contain':'Архив'}},'sorts':[{'property':'title','direction':'ascending'}],'configuration':{'properties':[{'property_id':'tag','visible':True}]}})

    if path.startswith('/v1/pages/'):
        identity=path.rsplit('/',1)[-1]
        return httpx.Response(200,json={'id':identity,'object':'page','parent':{'type':'page_id','page_id':ROOT} if identity==CHILD else {'type':'workspace','workspace':True},'properties':{'title':{'id':'title','type':'title','title':rich(('IT' if os.getenv('DRG_NOTION_TREE_TEST')=='1' else 'Семейная библиотека') if identity==ROOT else 'Памятки для Mac')}},'icon':{'type':'external','external':{'url':'https://missing.invalid/icon.png'}} if identity==ROOT else {'type':'emoji','emoji':'💻'},'cover':{'type':'external','external':{'url':'https://missing.invalid/cover.jpg'}} if identity==ROOT else None,'last_edited_time':'2026-11-01T00:00:00.000Z'})
    if path=='/v1/blocks/'+PENDING+'/children':return httpx.Response(200,json={'results':[{'id':'77777777-7777-4777-8777-777777777777','type':'file','file':{'type':'external','external':{'url':'https://missing.invalid/pending.pdf'}}}],'has_more':False})
    if path.startswith('/v1/blocks/'):
        root=path.split('/')[3]==ROOT
        blocks=[{'id':'33333333-3333-4333-8333-333333333333','type':'paragraph','paragraph':{'rich_text':rich(source_text)}}]
        if root:blocks.append({'id':CHILD,'type':'child_page','child_page':{'title':'Памятки для Mac'},'has_children':True})
        return httpx.Response(200,json={'results':blocks,'has_more':False})
    return httpx.Response(400,json={'object':'error'})

m.notion_sync_service.config=NotionConfig(True,'browser-read-app','fixture-only-read-secret',redirect_uri='https://site.invalid/api/notion-sync/oauth/callback',read_webhook_secret='fixture-webhook')
m.notion_sync_service.connections.config=m.notion_sync_service.config
m.notion_sync_service.connections.transport=httpx.MockTransport(provider)
m.notion_sync_service.batch_seconds=0

def unavailable_cover(*_):raise NotionError('notionAssetUnavailable',502)
m.notion_sync_service.fetch=unavailable_cover

@app.post('/api/browser/notion-fixture')
def fixture(request:Request):
    session=m.notes_ready(request);stamp=m.now()
    with m.db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("INSERT INTO notion_connections(id,user_id,kind,workspace_id,bot_id,payload,created,updated,next_scan) VALUES(?,?,'read','fixture-workspace','fixture-bot',?,?,?,?) ON CONFLICT(user_id,kind,workspace_id) DO NOTHING",
                  ('fixture-connection',session['user_id'],dump({'access_token':'browser-fixture-token','workspace_name':'Семейные заметки'}),stamp,stamp,stamp+900))
    return {'source':'https://www.notion.so/'+ROOT.replace('-','')}

@app.post('/api/browser/notion-update')
def update_fixture(request:Request):
    global source_text
    session=m.notes_ready(request);source_text='Обновлённый текст из Notion без перезагрузки страницы.'
    from app.notion_sync.state import enqueue
    with m.db() as c:
        root=c.execute('SELECT id FROM notion_roots WHERE user_id=? AND notion_id=?',(session['user_id'],ROOT)).fetchone()
        enqueue(c,root['id'],ROOT,'pull',m.now())
    return {'text':source_text}

@app.post('/api/browser/notion-database-update')
def update_database_fixture(request:Request):
    global database_title
    m.notes_ready(request);database_title='Личные заметки'
    from app.notion_sync.events import receive
    event={'id':'database-live-update','workspace_id':'fixture-workspace','entity':{'id':DATABASE,'type':'database'},'type':'database.content_updated'}
    body=json.dumps(event).encode();signature='sha256='+hmac.new(b'fixture-webhook',body,hashlib.sha256).hexdigest()
    receive(m.notion_sync_service,'read',body,signature)
    return {'title':database_title}

@app.post('/api/browser/notion-presentation')
def enable_presentation(request:Request):
    global presentation_enabled
    m.notes_ready(request);presentation_enabled=True
    return {'ok':True}
