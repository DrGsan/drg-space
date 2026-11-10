"""Read-only publication of a database and its direct, unlocked records."""
from app.i18n import message as localized_message
import json
from app.modules.note_assets import asset_payload
from app.modules.notion import record_preview


def publication_page(c, share, fail, record=None):
    root=c.execute('SELECT * FROM notes WHERE id=? AND deleted IS NULL',(share['note_id'],)).fetchone()
    if not root:fail(404,localized_message('pageIsUnavailable'))
    if not record:return root
    page=c.execute('SELECT n.* FROM notes n JOIN database_records r ON r.note_id=n.id WHERE n.id=? AND r.database_id=? AND n.parent_id=? AND n.deleted IS NULL AND n.locked=0',(record,root['id'],root['id'])).fetchone()
    if not page:fail(404,localized_message('theRecordIsNotAvailableInThisPublication'))
    return page


def database_payload(c,root,share):
    config=c.execute('SELECT * FROM note_databases WHERE note_id=?',(root['id'],)).fetchone()
    if not config:return None
    # Cross-database relations and computed values must never reveal other spaces.
    properties=[p for p in json.loads(config['properties']) if p['type'] in ('text','number','checkbox','select','multi_select','date','url')]
    ids={p['id'] for p in properties};views=[]
    for original in json.loads(config['views']):
        view={k:v for k,v in original.items() if k in ('id','name','type','group_by','date_by','filter','sort','visible')}
        view['visible']=[i for i in view.get('visible',[]) if i in ids]
        for key in ('filter','sort'):
            if view.get(key) and view[key]['property'] not in ids|{'title'}:view[key]=None
        views.append(view)
    records=[];prefix='/api/shared/'+share['token']
    for row in c.execute('SELECT n.*,r.note_id,r.properties FROM notes n JOIN database_records r ON r.note_id=n.id WHERE r.database_id=? AND n.parent_id=? AND n.deleted IS NULL AND n.locked=0 ORDER BY n.position,n.created',(root['id'],root['id'])):
        item={k:row[k] for k in ('note_id','title','icon','cover')}
        item.update(asset_payload(c,row,prefix+'/assets'))
        item['properties']={k:v for k,v in json.loads(row['properties']).items() if k in ids}
        preview=record_preview(c,row)
        if preview:
            from app.personal_data import NOTE_FILE_LINK
            match=next(NOTE_FILE_LINK.finditer(preview),None)
            if match:item['preview_url']=prefix+'/files/'+match[2]+'?record='+row['note_id']
        records.append(item)
    return {'note_id':root['id'],'title':root['title'],'note_version':root['version'],'version':config['version'],'properties':properties,'views':views,'records':records,'role':'viewer','relation_options':{}}
