"""Device-authorized database operations, evaluated within current note grants."""
from app.i18n import message as localized_message
import copy
import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.http import HTTPError
from app.note_databases import DatabaseOperations
from .identity import FederationError
from .notes import effective_grant

ID = r'^[A-Za-z0-9_-]{16}$'
READS = {'database_catalog', 'database_read', 'database_formula_preview'}


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Address(Model):
    note_id: str = Field(pattern=ID)
    revision: int = Field(ge=1)


class Configuration(Address):
    version: int = Field(ge=1)
    properties: list[dict]
    views: list[dict]


class Title(Address):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)


class CreateRecord(Address):
    title: str = Field(min_length=1, max_length=200)
    properties: dict = Field(default_factory=dict)


class UpdateRecord(Address):
    version: int = Field(ge=1)
    properties: dict


class Preview(Address):
    expression: str = Field(max_length=2000)
    record_id: str | None = Field(default=None, pattern=ID)


class Create(Model):
    parent_id: str = Field(pattern=ID)
    revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)


class Initialize(Address):
    version: int = Field(ge=1)
    blocks: list[dict] = Field(min_length=1)


MODELS = {'database_catalog': Model, 'database_read': Address, 'database_configure': Configuration,
          'database_title': Title, 'database_record_create': CreateRecord, 'database_record_update': UpdateRecord,
          'database_formula_preview': Preview, 'database_create': Create, 'database_record_initialize': Initialize}


def validate_payload(operation, payload):
    try:
        return MODELS[operation].model_validate(copy.deepcopy(payload))
    except (KeyError, ValidationError):
        raise FederationError('invalid_database_fields') from None


class DatabaseScope:
    def __init__(self, service, connection, peer, intent):
        self.service, self.connection, self.peer, self.intent = service, connection, peer, intent
        self.c = connection.application_connection if hasattr(connection, 'application_connection') else connection
        self.uid = intent.target_actor_id
        # Request-local only: repeated relations and rollups share the current
        # transaction's grants. Nothing survives into the next signed RPC.
        self._grants, self._owners, self._allowed, self._membership = {}, {}, {}, {}

    @staticmethod
    def denied():
        raise HTTPError(403, localized_message('theObjectIsNotAccessibleForInterServer'))

    def owner_access(self, nid):
        if nid in self._owners:return self._owners[nid]
        try:
            note = self.service.authorized_note(self.c, nid, self.uid)
        except FederationError:
            self.denied()
        seen, current = set(), note
        while current:
            if current['id'] in seen or len(seen) >= 100 or current['deleted'] or current['locked']:
                self.denied()
            seen.add(current['id'])
            current = self.c.execute('SELECT * FROM notes WHERE id=?', (current['parent_id'],)).fetchone() if current['parent_id'] else None
        self._owners[nid]=note
        return note

    def grant(self, nid, *, write=False):
        if (nid,write) in self._grants:return self._grants[nid,write]
        try:
            grant = effective_grant(self.service, self.connection, nid, self.peer.server_id,
                                    self.intent.actor_id, self.intent.device_id, write=write)
        except FederationError:
            self.denied()
        if grant['owner_id'] != self.uid:
            self.denied()
        self._grants[nid,write]=grant
        return grant

    def access(self, nid, *, write=False):
        self.grant(nid, write=write)
        note=self.owner_access(nid)
        if write:
            from app.notion_sync.access import require_writable
            require_writable(self.c,note,self.service.m.fail)
        return note

    def allowed(self, nid):
        if not isinstance(nid,str) or not re.fullmatch(ID,nid):return False
        if nid in self._allowed:return self._allowed[nid]
        try:
            self.access(nid)
            self._allowed[nid]=True
            return True
        except (HTTPError, FederationError):
            self._allowed[nid]=False
            return False

    def role(self, nid):
        return self.grant(nid)['role']

    def metadata(self, nid):
        grant = self.grant(nid)
        return {'revision': grant['revision'], 'role': grant['role'], 'owner_id': self.uid, 'actor_id': self.uid,
                'peer_id': self.service.identity.descriptor.server_id}

    def reference(self, prop, rid, database_id):
        target = prop.get('database_id') if prop['type'] == 'relation' else database_id
        if prop['type'] == 'relation' and not self.allowed(target):
            return False
        if not self.allowed(rid):
            return False
        if rid not in self._membership:
            row = self.c.execute('SELECT database_id FROM database_records WHERE note_id=?', (rid,)).fetchone()
            self._membership[rid]=row[0] if row else None
        return self._membership[rid] == target

    def dependency_chain(self, rid, database_id):
        database=self.c.execute('SELECT properties FROM note_databases WHERE note_id=?',(database_id,)).fetchone()
        fields=[prop['id'] for prop in json.loads(database[0]) if prop['type']=='dependency']
        seen=set();queue=[rid]
        while queue:
            identity=queue.pop()
            if identity in seen:continue
            if len(seen)>=2000:self.denied()
            seen.add(identity);self.access(identity)
            record=self.c.execute('SELECT database_id,properties FROM database_records WHERE note_id=?',(identity,)).fetchone()
            if not record or record['database_id']!=database_id:self.denied()
            values=json.loads(record['properties'])
            for key in fields:queue.extend(values.get(key) or [])

    def configure_database(self, database_id):
        # A schema mutation changes the meaning of every row. Refuse before
        # inspecting hidden values, so type-validation errors cannot become an
        # oracle for records denied by a nearer grant.
        self.access(database_id,write=True)
        for row in self.c.execute('SELECT r.note_id FROM database_records r JOIN notes n ON n.id=r.note_id WHERE r.database_id=? AND n.deleted IS NULL',(database_id,)):
            self.access(row[0],write=True)

    def schema_editable(self,database_id):
        try:self.configure_database(database_id);return True
        except (HTTPError,FederationError):return False

    def values(self, properties, values, database_id):
        result = copy.deepcopy(values)
        for prop in properties:
            if prop['type'] in ('relation', 'dependency'):
                result[prop['id']] = [rid for rid in values.get(prop['id']) or [] if self.reference(prop, rid, database_id)]
        return result

    def merge_values(self, properties, old, new, database_id):
        if not isinstance(new, dict):
            raise HTTPError(422, localized_message('invalidRecordProperties'))
        result = copy.deepcopy(new)
        for prop in properties:
            if prop['type'] not in ('relation', 'dependency'):
                continue
            proposed = new.get(prop['id']) or []
            if not isinstance(proposed, list):
                raise HTTPError(422, localized_message('invalidRelationshipValues'))
            if any(not self.reference(prop, rid, database_id) for rid in proposed):
                self.denied()
            hidden = [rid for rid in old.get(prop['id']) or [] if not self.reference(prop, rid, database_id)]
            if hidden:
                if prop['type'] == 'relation' and not prop.get('multiple', True) and proposed:
                    self.denied()
                result[prop['id']] = list(dict.fromkeys([*proposed, *hidden]))
        return result

    def descriptors(self, properties):
        result = copy.deepcopy(properties)
        mapping = {item['id']: item for item in properties}
        for prop in result:
            if prop['type'] == 'relation' and not self.allowed(prop['database_id']):
                prop['database_id'] = None
                if 'inverse_id' in prop:
                    prop['inverse_id'] = None
                prop['unavailable'] = True
            if prop['type'] == 'rollup':
                relation = mapping.get(prop['relation_id'], {})
                if not self.allowed(relation.get('database_id')):
                    prop['property'] = None
                    prop['unavailable'] = True
        return result

    def restore_descriptors(self, previous, proposed):
        if not isinstance(proposed,list) or any(not isinstance(item,dict) or not isinstance(item.get('id'),str) for item in proposed):
            raise HTTPError(422,localized_message('invalidDatabaseProperties'))
        old = {item['id']: item for item in previous}
        masked = {item['id']: item for item in self.descriptors(previous) if item.get('unavailable')}
        result = copy.deepcopy(proposed)
        incoming = {item.get('id'): item for item in result}
        def normalized(value):
            return {key: item for key, item in value.items() if item is not None and key != 'unavailable'}
        for pid, descriptor in masked.items():
            if pid not in incoming or normalized(incoming[pid]) != normalized(descriptor):
                self.denied()
            incoming[pid].clear()
            incoming[pid].update(copy.deepcopy(old[pid]))
        return result

    def hidden_filter(self, view, properties, database_id):
        rule=view.get('filter')
        if not isinstance(rule,dict):return False
        prop=next((item for item in properties if item.get('id')==rule.get('property')),None)
        if not prop or prop.get('type') not in ('relation','dependency'):return False
        value=rule.get('value','')
        if not isinstance(value,str) or not value:return False
        if prop['type']=='relation' and not self.allowed(prop.get('database_id')):return True
        ids=re.findall(r'(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{16}(?![A-Za-z0-9_-])',value)
        return any(not self.reference(prop,rid,database_id) for rid in ids)

    def views(self, views, properties, database_id):
        result=copy.deepcopy(views)
        for view in result:
            if self.hidden_filter(view,properties,database_id):
                view['filter']=None;view['unavailable_filter']=True
        return result

    def restore_views(self, previous, proposed, properties, database_id):
        if not isinstance(proposed,list) or any(not isinstance(view,dict) or not isinstance(view.get('id'),str) for view in proposed):
            raise HTTPError(422,localized_message('invalidDatabaseRepresentation'))
        old={view['id']:view for view in previous}
        result=copy.deepcopy(proposed)
        for view in result:
            prior=old.get(view['id'])
            if prior and self.hidden_filter(prior,properties,database_id) and view.get('filter') is None:
                view['filter']=copy.deepcopy(prior['filter'])
            view.pop('unavailable_filter',None)
            if self.hidden_filter(view,properties,database_id) and (not prior or view.get('filter')!=prior.get('filter')):
                self.denied()
        return result


def operations(service, connection, peer, intent):
    import time
    scope = DatabaseScope(service, connection, peer, intent)
    return DatabaseOperations(scope.c, scope.uid, {'user_id': scope.uid}, lambda: int(time.time()), service.m.fail, scope=scope)


def authorize(service, connection, peer, intent):
    data = validate_payload(intent.operation, intent.payload)
    core = operations(service, connection, peer, intent)
    if intent.operation == 'database_catalog':
        return core, data
    nid = data.parent_id if intent.operation == 'database_create' else data.note_id
    grant = core.scope.grant(nid, write=intent.operation not in READS)
    core.scope.owner_access(nid)
    if grant['revision'] != data.revision:
        core.scope.denied()
    return core, data


def replay(service, connection, peer, intent, previous):
    core, data = authorize(service, connection, peer, intent)
    if intent.operation == 'database_configure':
        result = core.read(data.note_id)
    elif intent.operation == 'database_record_initialize':
        from .notes import execute as execute_note
        return execute_note(service,connection,peer,intent.model_copy(update={'operation':'read','payload':{'note_id':data.note_id,'revision':data.revision}}))[0]
    elif intent.operation == 'database_title':
        note = core.access(data.note_id)
        result = {'title': note['title'], 'version': note['version']}
    else:
        result = {key: value for key, value in previous.items() if key in ('id', 'ok')}
    nid = result.get('id') or data.note_id
    core.scope.access(nid)
    result.update(core.scope.metadata(nid))
    return result


def record_context(service, connection, peer, intent, nid):
    core = operations(service, connection, peer, intent)
    note = core.access(nid)
    row = core.c.execute('SELECT * FROM database_records WHERE note_id=?', (nid,)).fetchone()
    if not row:
        return None
    database = core.c.execute('SELECT * FROM note_databases WHERE note_id=?', (row['database_id'],)).fetchone()
    core.scope.owner_access(row['database_id'])
    properties = json.loads(database['properties'])
    record = dict(row)
    record.pop('database_id', None)
    record.update(title=note['title'], properties=json.loads(row['properties']), **core.scope.metadata(nid))
    evaluator = core.evaluator(source_record=(row['database_id'], nid))
    evaluator.apply(row['database_id'], [record])
    record['properties'] = core.scope.values(properties, record['properties'], row['database_id'])
    return {'properties': core.scope.descriptors(properties), 'record': record,
            'role': core.scope.role(nid), 'revision': core.scope.grant(nid)['revision'],
            'version': database['version'], 'relation_options': evaluator.options(row['database_id'])}


def execute(service, connection, peer, intent):
    core, data = authorize(service, connection, peer, intent)
    if intent.operation == 'database_catalog':
        return {'databases': core.catalog()}, None
    nid = data.parent_id if intent.operation == 'database_create' else data.note_id
    if intent.operation == 'database_create':
        result = core.create(data.title, data.parent_id)
    elif intent.operation == 'database_read':
        result = core.read(nid)
    elif intent.operation == 'database_configure':
        result = core.configure(nid, data.properties, data.views, data.version)
    elif intent.operation == 'database_title':
        result = core.title(nid, data.title, data.version)
    elif intent.operation == 'database_record_create':
        result = core.add(nid, data.title, data.properties)
    elif intent.operation == 'database_record_update':
        result = core.update(nid, data.properties, data.version)
    elif intent.operation == 'database_formula_preview':
        result = core.preview(nid, data.expression, data.record_id)
    elif intent.operation == 'database_record_initialize':
        return initialize_record(service,connection,peer,intent,core,data)
    else:
        raise FederationError('unsupported_database_operation')
    result.update(core.scope.metadata(result.get('id', nid)))
    return result, None


def initialize_record(service, connection, peer, intent, core, data):
    import secrets
    import time
    from app.collaboration import load_doc
    from app.modules.notes import validate_blocks
    from app.modules.note_files import validate_file_links
    from app.note_history import snapshot
    from app.page_links import page_links
    from app.share_editing import replace_content
    from .notes import execute as execute_note
    page = core.access(data.note_id, True)
    if not core.c.execute('SELECT 1 FROM database_records WHERE note_id=?',(page['id'],)).fetchone():
        raise HTTPError(422,localized_message('initializationIsForDatabaseWriting'))
    if page['version'] != data.version or core.c.execute('SELECT 1 FROM collaborative_docs WHERE note_id=?',(page['id'],)).fetchone():
        raise HTTPError(409,localized_message('theDocumentHasAlreadyBeenModifiedOpenThe'))
    if page['blocks'] and json.loads(page['blocks']):
        raise HTTPError(409,localized_message('theBlockDocumentAlreadyExists'))
    validate_blocks(data.blocks, service.m.fail)
    for linked in page_links(data.blocks):
        target = core.scope.access(linked)
        if target['parent_id'] != page['id']:
            core.scope.denied()
    old_text=(page['blocks'] or '')+page['body']
    validate_file_links(core.c,page['id'],page['body'],data.blocks,core.uid,service.m.fail,old_text)
    from app.personal_data import ASSET_LINK, NOTE_ASSET_LINK, NOTE_FILE_LINK
    incoming=json.dumps(data.blocks,ensure_ascii=False)
    old_files={match[2] for match in NOTE_FILE_LINK.finditer(old_text)}
    for match in NOTE_FILE_LINK.finditer(incoming):
        if match[2] in old_files:continue
        source=core.c.execute('SELECT parent_id FROM files WHERE id=?',(match[2],)).fetchone()
        if not source:raise FederationError('invalid_asset_reference')
        core.scope.access(source[0])
    previous=set(ASSET_LINK.findall(old_text))|{match[1] for match in NOTE_ASSET_LINK.findall(old_text)}
    current=set(ASSET_LINK.findall(incoming))|{match[1] for match in NOTE_ASSET_LINK.findall(incoming)}
    if not current <= previous:
        raise FederationError('upload_media_to_shared_note')
    stamp=int(time.time());doc=load_doc(None)
    title,body,blocks=replace_content(doc,page['title'],data.blocks,service.m.fail)
    snapshot(core.c,page,stamp)
    epoch=secrets.token_urlsafe(16);state=doc.get_update()
    core.c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?)',(page['id'],state,epoch,stamp))
    core.c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',
                   (title,body,json.dumps(blocks,ensure_ascii=False),stamp,page['id']))
    read=intent.model_copy(update={'operation':'read','payload':{'note_id':page['id'],'revision':data.revision}})
    result=execute_note(service,connection,peer,read)[0]
    return result,(page['id'],state,state,epoch,page['version']+1,True)
