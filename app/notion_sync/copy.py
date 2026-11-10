"""Independent private copies own their files; they never refer back to a link."""
import json
import secrets
from contextlib import suppress

from app.notion_sync.client import NotionError
from app.notion_sync.state import dump
from app.personal_data import NOTE_FILE_LINK


def independent(service,uid,nid,receipt=None,*,document=None,shallow=False):
    with service.db() as c:
        source=c.execute('SELECT n.* FROM notes n JOIN notion_pages p ON p.note_id=n.id WHERE n.id=? AND n.user_id=? AND n.space_id IS NULL AND n.deleted IS NULL',(nid,uid)).fetchone()
        if not source:raise NotionError('notionLinkUnavailable',404)
        if receipt:
            from app.import_receipts import previous
            saved=previous(c,uid,'notion-sync-copy',receipt['operation'],receipt['digest'],int(service.clock()))
            if saved:return saved
        nodes=[dict(source)] if shallow else [dict(row) for row in c.execute('WITH RECURSIVE tree(id) AS (SELECT id FROM notes WHERE id=? UNION SELECT n.id FROM notes n JOIN tree t ON n.parent_id=t.id WHERE n.deleted IS NULL) SELECT n.* FROM notes n JOIN tree t ON t.id=n.id',(nid,)).fetchall()]
        if document is not None:
            from app.notion_sync.blocks import plain
            node=next(row for row in nodes if row['id']==nid)
            node.update({key:document[key] for key in ('title','icon','cover')})
            node.update(blocks=dump(document['blocks']),body=plain(document['blocks']))
        if len(nodes)>2000 or any(row['user_id']!=uid or row['space_id'] for row in nodes):raise NotionError('notionUnsupportedDocument',422)
        identities=[row['id'] for row in nodes]
        for start in range(0,len(identities),200):
            batch=identities[start:start+200]
            if c.execute("SELECT 1 FROM notion_pages WHERE note_id IN ("+','.join('?' for _ in batch)+") AND status='importing' LIMIT 1",tuple(batch)).fetchone():raise NotionError('notionImportInProgress',409)
        from app.notion_sync import presentation
        shown={node['id']:presentation.page(c,node,uid) for node in nodes if not presentation.hidden(c,node['id'],uid)}
        aliases={old:new for old,new in presentation.index(c,uid)['aliases'].items() if new in shown}
        mapping={identity:secrets.token_urlsafe(12) for identity in shown}
        mapping.update({old:mapping[new] for old,new in aliases.items()})
        asset_nodes={node['id']:node for node in nodes}
        for old in aliases:
            if old not in asset_nodes:asset_nodes[old]=dict(c.execute('SELECT * FROM notes WHERE id=?',(old,)).fetchone())
        files={};images={};databases={};records={}
        for node in asset_nodes.values():
            for match in NOTE_FILE_LINK.finditer((node['blocks'] or '')+node['body']):
                fid=match[2]
                if fid in files:continue
                from app.modules.note_files import page_file
                def denied(*_):raise NotionError('notionAttachmentUnavailable',409)
                row=page_file(c,node,fid,denied,uid=uid)
                files[fid]={**row,'destination':mapping[node['id']],'new_id':secrets.token_urlsafe(12),'new_object':secrets.token_hex(16)}
            for row in c.execute('SELECT a.*,m.mime,m.size,m.width,m.height,m.frames FROM note_assets a JOIN media m ON m.id=a.id WHERE a.note_id=?',(node['id'],)).fetchall():
                images[row['id']]={**dict(row),'new_id':secrets.token_hex(16)}
            database=c.execute('SELECT * FROM note_databases WHERE note_id=?',(node['id'],)).fetchone()
            record=c.execute('SELECT * FROM database_records WHERE note_id=?',(node['id'],)).fetchone()
            if database:databases[node['id']]=dict(database)
            if record:records[node['id']]=dict(record)
    from app.file_store import file_store
    from app.data_crypto import media_store
    store=file_store(service.db,service.data_dir);media=media_store(service.db,service.data_dir)
    writers=[];created_images=[];stamp=int(service.clock());committed=False
    try:
        for row in files.values():
            writer=store.writer(row['new_object']);writers.append(writer)
            for part in store.iter_read(row['object_id']):writer.write(part)
            size,_=writer.finish()
            if size!=row['size']:raise NotionError('notionAttachmentUnavailable',409)
        for row in images.values():
            created_images.append(row['new_id']);media.write(row['new_id'],media.read(row['id']))
        def rewrite(value):
            if isinstance(value,list):return [rewrite(item) for item in value]
            if isinstance(value,dict):
                result={key:rewrite(item) for key,item in value.items()}
                if result.get('type')=='pageLink':
                    props=result.setdefault('props',{});props['pageId']=mapping.get(props.get('pageId'),props.get('pageId'))
                if result.get('type') in ('paragraph','heading','bulletListItem','numberedListItem','checkListItem','toggleListItem','quote','codeBlock','table','image','video','audio','file','divider','pageLink','callout','columnList','column'):result['id']=secrets.token_hex(12)
                return result
            if isinstance(value,str):
                value=NOTE_FILE_LINK.sub(lambda match:service.public_url+'/api/notes/'+files[match[2]]['destination']+'/files/'+files[match[2]]['new_id'] if match[2] in files else match[0],value)
                for old,new in mapping.items():value=value.replace('/page/'+old,'/page/'+new)
                return value
            return value
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if receipt:
                saved=previous(c,uid,'notion-sync-copy',receipt['operation'],receipt['digest'],stamp)
                if saved:return saved
            for node in asset_nodes.values():
                current=c.execute('SELECT version,deleted,user_id,space_id FROM notes WHERE id=?',(node['id'],)).fetchone()
                if not current or current['version']!=node['version'] or current['deleted'] is not None or current['user_id']!=uid or current['space_id'] is not None:raise NotionError('notionLocalChanged',409)
            for node in shown.values():
                values={key:node[key] for key in ('icon','cover')}
                for kind,value in values.items():
                    if value.startswith('image:') and value[6:] in images:values[kind]='image:'+images[value[6:]]['new_id']
                c.execute('INSERT INTO notes(id,user_id,title,body,blocks,icon,cover,full_width,position,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (mapping[node['id']],uid,node['title'],rewrite(node['body']),dump(rewrite(json.loads(node['blocks']))) if node['blocks'] else None,values['icon'],values['cover'],node['full_width'],node['position'],stamp,stamp))
            for node in shown.values():c.execute('UPDATE notes SET parent_id=? WHERE id=?',(mapping.get(node['parent_id']),mapping[node['id']]))
            for row in files.values():
                destination=row['destination']
                if not c.execute('SELECT 1 FROM files WHERE id=?',(destination,)).fetchone():c.execute('INSERT INTO files(id,user_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?)',(destination,uid,'folder','Notion copy','application/x-drg-note;space=private',stamp,stamp))
                c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(row['new_id'],uid,destination,'file',row['name'],row['mime'],row['size'],row['new_object'],stamp,stamp))
            for row in images.values():
                c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)',(row['new_id'],uid,row['mime'],row['size'],row['width'],row['height'],row['frames'],stamp))
                c.execute('INSERT INTO note_assets VALUES(?,?,?,?,?)',(row['new_id'],mapping[row['note_id']],row['kind'],row['payload'],stamp))
            for old,database in databases.items():
                props=json.loads(database['properties'])
                for prop in props:
                    if prop['type']=='relation':
                        target=prop['database_id'];prop['database_id']=mapping.get(target,target)
                        if target not in mapping:prop['two_way']=False;prop.pop('inverse_id',None)
                c.execute('INSERT INTO note_databases(note_id,properties,views) VALUES(?,?,?)',(mapping[old],dump(props),database['views']))
            for old,record in records.items():
                if record['database_id'] not in mapping:continue
                props=json.loads(databases[record['database_id']]['properties']);types={prop['id']:prop['type'] for prop in props};values=json.loads(record['properties'])
                for key,value in values.items():
                    if types.get(key) in ('relation','dependency') and isinstance(value,list):values[key]=[mapping.get(item,item) for item in value]
                c.execute('INSERT INTO database_records(note_id,database_id,properties) VALUES(?,?,?)',(mapping[old],mapping[record['database_id']],dump(values)))
            if receipt:
                from app.import_receipts import committed as save_receipt
                save_receipt(c,uid,'notion-sync-copy',receipt['operation'],receipt['digest'],{'note_id':mapping[nid]},stamp)
        committed=True
        return {'note_id':mapping[nid]}
    finally:
        if not committed:
            for writer in writers:
                with suppress(Exception):writer.abort()
            for aid in created_images:
                with suppress(Exception):media.delete(aid)
