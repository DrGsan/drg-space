"""Read-only vault inventory. Reports counts only, never account IDs or content."""
import re
from pathlib import Path
from app.secret_vault import PREFIX,load,remote_ids
from app.page_links import page_links


def inventory(c,base,root,stamp):
    values={row[0][len(PREFIX):]:load(c,row[0][len(PREFIX):]) for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?',(PREFIX+'%',))}
    users={row[0] for row in c.execute('SELECT id FROM users')}
    live=set();queued=set();dangling=0;notes=0;files=0;expired=0
    for uid,value in values.items():
        dangling+=uid not in users
        queued.update((uid,identity) for identity in value.get('garbage',[]))
        expired+=sum(grant['expires']<=stamp for grant in value['grants'].values())
        for nid,note in value['notes'].items():
            notes+=1
            dangling+=len(page_links(note.get('blocks'))-value['notes'].keys())
            if note.get('parent_id'):dangling+=note['parent_id'] not in value['notes']
            record=note.get('record')
            if record:dangling+=not value['notes'].get(record['database_id'],{}).get('database')
            for prop in note.get('database',{}).get('properties',[]):
                if prop['type']=='relation':dangling+=not value['notes'].get(prop['database_id'],{}).get('database')
            for identity in re.findall(r'/api/secret-vault/notes/content/([a-zA-Z0-9_-]+)',str(note)):
                dangling+=identity not in value['files']
        for fid,item in value['files'].items():
            files+=item['kind']=='file'
            if item.get('object_id'):live.add((uid,item['object_id']))
            if item.get('note_id'):dangling+=item['note_id'] not in value['notes']
            if item.get('parent_id'):dangling+=value['files'].get(item['parent_id'],{}).get('kind')!='folder'
    stored=set();volume_strays=0
    directory=Path(root)/'secret'
    if directory.exists():
        for path in directory.glob('*/files/*'):
            if not path.is_file() or path.is_symlink():continue
            uid=path.parent.parent.name
            if re.fullmatch(r'[a-f0-9]{32}',uid) and re.fullmatch(r'[a-f0-9]{32}',path.name):
                if base.backend=='local':stored.add((uid,path.name))
                else:volume_strays+=1
            else:volume_strays+=1
    available=set(stored)
    if base.backend=='s3':
        prefix='/'.join(filter(None,(base.prefix,base.storage_id,'secret'))) +'/'
        for key in remote_ids(base.client,base.bucket,prefix):
            parts=key[len(prefix):].split('/')
            if len(parts)==2 and all(re.fullmatch(r'[a-f0-9]{32}',part) for part in parts):stored.add(tuple(parts))
            else:dangling+=1
        for page in base.client.get_paginator('list_objects_v2').paginate(Bucket=base.bucket,Prefix=prefix):
            for item in page.get('Contents',[]):
                parts=item['Key'][len(prefix):].split('/')
                if len(parts)==2 and all(re.fullmatch(r'[a-f0-9]{32}',part) for part in parts):available.add(tuple(parts))
    return {'vaults':len(values),'notes':notes,'files':files,'objects':len(stored),'queued_objects':len(queued),
            'missing':len(live-available),'unreferenced':len(stored-live-queued),
            'orphan_accounts':len({uid for uid,identity in stored if uid not in values}),
            'dangling_references':dangling,'volume_strays':volume_strays,'expired_grants':expired}
