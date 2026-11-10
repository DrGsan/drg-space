"""Run the shared page and database rules against a private, memory-only projection.

The projection contains only this vault's pages and one synthetic owner. Nothing
is inserted into the ordinary database, search, revisions or collaboration tables.
Only current pages/configuration/record values are sealed back into the vault.
"""
from app.i18n import message as localized_message
import json
import inspect
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from app.schema import SCHEMA
from app.secret_vault import save


def insert(c, table, row):
    c.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')', tuple(row.values()))


class ProjectionConnection(sqlite3.Connection):
    """Allow the shared note rules to attach their per-transaction access cache."""


def projection(value, uid):
    c = sqlite3.connect(':memory:',factory=ProjectionConnection)
    try:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA temp_store=MEMORY')
        c.execute('PRAGMA journal_mode=MEMORY')
        c.executescript(SCHEMA)
        insert(c, 'users', {'id':uid, 'login':'vault-owner', 'password':'', 'role':'member', 'created':0})
        columns={r['name'] for r in c.execute('PRAGMA table_info(notes)')}
        for nid, item in value['notes'].items():
            row={k:v for k,v in item.items() if k in columns}
            row.update(id=nid, user_id=uid, space_id=None, locked=0)
            row['blocks']=json.dumps(item.get('blocks')) if item.get('blocks') is not None else None
            insert(c, 'notes', row)
        for nid,item in value['notes'].items():
            if item.get('database'):
                d=item['database']
                insert(c, 'note_databases', {'note_id':nid, 'properties':json.dumps(d['properties']), 'views':json.dumps(d['views']), 'version':d['version']})
        for nid,item in value['notes'].items():
            if item.get('record'):
                record=item['record']
                insert(c, 'database_records', {'note_id':nid, 'database_id':record['database_id'], 'properties':json.dumps(record['properties']), 'version':record['version']})
        c.commit()
        return c
    except BaseException:
        c.close()
        raise


def collect(c, value):
    notes={}
    for row in c.execute('SELECT * FROM notes'):
        item=dict(row)
        for field in ('user_id','space_id','deleted','trash_batch'):
            item.pop(field,None)
        item['blocks']=json.loads(item['blocks']) if item['blocks'] is not None else None
        item['locked']=value['notes'].get(item['id'],{}).get('locked',0)
        notes[item['id']]=item
    for row in c.execute('SELECT * FROM note_databases'):
        notes[row['note_id']]['database']={'properties':json.loads(row['properties']), 'views':json.loads(row['views']), 'version':row['version']}
    for row in c.execute('SELECT * FROM database_records'):
        notes[row['note_id']]['record']={'database_id':row['database_id'], 'properties':json.loads(row['properties']), 'version':row['version']}
    value['notes']=notes


def register(r,service):
    from app.modules.notion import create_notion_router
    state=ContextVar('vault-database-request')

    @contextmanager
    def db():
        yield state.get()['projection']

    class Routes:
        def route(self,path,**options):
            allowed=bool(set(options.get('methods',[])) & {
                '/api/database-catalog':{'GET'},'/api/databases':{'POST'},
                '/api/databases/{nid}':{'GET','PUT'},'/api/databases/{nid}/records':{'POST'},
                '/api/databases/{nid}/formula-preview':{'POST'},'/api/database-records/{rid}':{'PATCH'},
                '/api/notes/{nid}/database-title':{'PATCH'},'/api/notes/{nid}/subpages':{'POST'},
            }.get(path,set()))
            def decorate(function):
                if not allowed:return function
                @wraps(function)
                async def guarded(*args,**kwargs):
                    request=kwargs['request']
                    if getattr(kwargs.get('data'),'space_id',None) is not None:
                        service.fail(422,localized_message('secretDatabasesAreOnlyAccessibleInThePersonal'))
                    with service.db() as c:
                        c.execute('BEGIN IMMEDIATE')
                        session,value,_=service.authorized(c,request,'notes')
                        with_memory=projection(value,session['user_id'])
                        token=state.set({'projection':with_memory,'session':session})
                        try:
                            result=function(*args,**kwargs)
                            if inspect.isawaitable(result):result=await result
                            if request.method!='GET':
                                # Recheck the grant after potentially expensive formulas.
                                service.authorized(c,request,'notes')
                                collect(with_memory,value)
                                save(c,session['user_id'],value)
                            if isinstance(result,dict) and 'records' in result:
                                for record in result['records']:
                                    nid=record['note_id'];note=value['notes'][nid]
                                    for kind in ('icon','cover'):
                                        field=note.get(kind,'')
                                        if field.startswith('image:'):
                                            record[kind+'_url']='/api/secret-vault/notes/content/'+field[6:]
                            return result
                        finally:
                            state.reset(token)
                            with_memory.close()
                r.route('/api/secret-vault/notes'+path[4:],**options)(guarded)
                return function
            return decorate
        def get(self,path):return self.route(path,methods=['GET'])
        def post(self,path):return self.route(path,methods=['POST'])
        def put(self,path):return self.route(path,methods=['PUT'])
        def patch(self,path):return self.route(path,methods=['PATCH'])
        def delete(self,path):return self.route(path,methods=['DELETE'])

    create_notion_router(db,lambda request:state.get()['session'],service.now,service.fail,router=Routes())
    from app.modules.notes import create_notes_router
    create_notes_router(db,lambda request:state.get()['session'],service.now,service.fail,router=Routes())


def boundary(notes,selected,fail):
    """No dangling record ownership or relations across the secret boundary."""
    for nid,item in notes.items():
        record=item.get('record')
        if record and ((nid in selected)!=(record['database_id'] in selected)):
            fail(409,localized_message('moveTheRecordAlongWithItsDatabaseBy'))
        for prop in item.get('database',{}).get('properties',[]):
            if prop['type']=='relation' and ((nid in selected)!=(prop['database_id'] in selected)):
                fail(409,localized_message('relatedDatabasesMustBeMovedTogetherFromThe'))


def remove_references(value,selected):
    """Permanent deletion must leave neither record IDs nor schema references."""
    from app.formulas import references
    for nid,item in value['notes'].items():
        if nid in selected:continue
        config=item.get('database')
        if config:
            props=config['properties']
            removed={p['id'] for p in props if p['type']=='relation' and p['database_id'] in selected}
            while True:
                names=removed|{p['name'] for p in props if p['id'] in removed}
                more={p['id'] for p in props if (p['type']=='rollup' and p['relation_id'] in removed)
                      or (p['type']=='formula' and references(p['expression']) & names)}
                if more<=removed:break
                removed|=more
            if removed:
                config['properties']=[p for p in props if p['id'] not in removed]
                for view in config['views']:
                    view['visible']=[key for key in view.get('visible',[]) if key not in removed]
                    for field in ('group_by','date_by','dependency_by'):
                        if view.get(field) in removed:view[field]=None
                    for field in ('filter','sort'):
                        if view.get(field,{} ) and view[field]['property'] in removed:view[field]=None
                config['version']+=1
                for record in value['notes'].values():
                    row=record.get('record')
                    if row and row['database_id']==nid:
                        for key in removed:row['properties'].pop(key,None)
                        row['version']+=1
        record=item.get('record')
        if record:
            props=value['notes'].get(record['database_id'],{}).get('database',{}).get('properties',[])
            for prop in props:
                if prop['type'] in ('relation','dependency'):
                    old=record['properties'].get(prop['id'])
                    if old and any(identity in selected for identity in old):
                        record['properties'][prop['id']]=[identity for identity in old if identity not in selected]
                        record['version']+=1
