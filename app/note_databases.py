"""Shared note-database business operations for local and scoped remote callers."""
from app.i18n import message as localized_message
import json
import secrets

from app.modules.social import note_access, space_access


class DatabaseOperations:
    def __init__(self, connection, uid, session, now, fail, *, scope=None):
        self.c, self.uid, self.session = connection, uid, session
        self.now, self.fail, self.scope = now, fail, scope

    def access(self, nid, edit=False):
        if self.scope:
            return self.scope.access(nid, write=edit)
        from app.modules.notion import require_role
        note = note_access(self.c, nid, self.uid, self.fail)
        if edit:
            require_role(self.c, note, self.uid, self.fail, session=self.session, now=self.now)
        return note

    def database(self, nid, edit=False):
        note = self.access(nid, edit)
        if not edit and not self.scope:
            from app.notion_sync.presentation import canonical
            identity=canonical(self.c,nid,self.uid)
            if identity!=nid:note=self.access(identity);nid=identity
        database = self.c.execute('SELECT * FROM note_databases WHERE note_id=?', (nid,)).fetchone()
        if not database:
            self.fail(404, localized_message('databaseNotFound'))
        return note, database

    def evaluator(self, *, source_record=None):
        from app.database_features import Evaluator
        return Evaluator(self.c, self.uid, self.now(), scope=self.scope, source_record=source_record)

    def payload(self, note, database):
        from app.modules.notion import record_preview, page_role
        from app.modules.note_assets import asset_payload
        from app.note_access import visible_ids
        allowed = visible_ids(self.c, self.uid) if not self.scope else None
        records = []
        for row in self.c.execute('SELECT r.*,n.title,n.icon,n.cover,n.updated,n.blocks FROM database_records r JOIN notes n ON n.id=r.note_id WHERE r.database_id=? AND n.deleted IS NULL ORDER BY n.position,n.created', (note['id'],)):
            if self.scope:
                if not self.scope.allowed(row['note_id']):
                    continue
            elif row['note_id'] not in allowed:
                continue
            item = dict(row)
            item.pop('blocks', None)
            item['properties'] = json.loads(row['properties'])
            item['preview_url'] = record_preview(self.c, row)
            item.update(asset_payload(self.c, {'id': row['note_id'], 'icon': row['icon'], 'cover': row['cover']}))
            if self.scope:
                item.update(self.scope.metadata(row['note_id']))
            records.append(item)
        evaluator = self.evaluator()
        records = evaluator.apply(note['id'], records)
        properties = json.loads(database['properties'])
        views = json.loads(database['views'])
        if self.scope:
            properties = self.scope.descriptors(properties)
            views = self.scope.views(views,json.loads(database['properties']),note['id'])
            for item in records:
                item['properties'] = self.scope.values(json.loads(database['properties']), item['properties'], note['id'])
        result = {'relation_options': evaluator.options(note['id']), 'note_id': note['id'], 'title': note['title'],
                'note_version': note['version'], 'properties': properties, 'views': views,
                'version': database['version'], 'records': records,
                'role': self.scope.role(note['id']) if self.scope else page_role(self.c, note, self.uid)}
        if not self.scope:
            from app.notion_sync.presentation import page
            shown=page(self.c,note,self.uid)
            result.update(title=shown['title'],note_version=shown['version'])
        if self.scope:result['schema_editable']=self.scope.schema_editable(note['id'])
        return result

    def read(self, nid):
        return self.payload(*self.database(nid))

    def catalog(self):
        from app.note_access import visible_ids
        allowed = visible_ids(self.c, self.uid) if not self.scope else None
        result = []
        for row in self.c.execute('SELECT n.id,n.title,n.space_id,d.properties FROM note_databases d JOIN notes n ON n.id=d.note_id WHERE n.deleted IS NULL'):
            if self.scope:
                if not self.scope.allowed(row['id']):
                    continue
            elif row['id'] not in allowed:
                continue
            item = dict(row)
            if not self.scope:
                from app.notion_sync.presentation import index
                parent=index(self.c,self.uid)['containers'].get(row['id'])
                if parent:item['title']=parent['title']
            properties = json.loads(row['properties'])
            item['properties'] = self.scope.descriptors(properties) if self.scope else properties
            if self.scope:
                item.update(self.scope.metadata(row['id']))
            result.append(item)
        return result

    def preview(self, nid, expression, rid=None):
        from app.formulas import evaluate, FormulaError
        from app.http import HTTPError
        self.database(nid)
        evaluator = self.evaluator()
        if rid is None:
            rid = next(iter(evaluator.database(nid)[1]), None)
        elif self.scope:
            self.scope.access(rid)
        try:
            return {'value': evaluate(expression, lambda name: evaluator.value(nid, rid, name)), 'error': None}
        except (FormulaError, HTTPError, ValueError, TypeError, KeyError) as error:
            return {'value': None, 'error': 'Вычисляемое свойство недоступно.' if self.scope else str(error.detail) if isinstance(error, HTTPError) else str(error)}

    def create(self, title, parent_id=None, space_id=None):
        from app.modules.notion import DEFAULT_PROPERTIES, DEFAULT_VIEWS
        if not title.strip():
            self.fail(422, localized_message('enterTheName'))
        if parent_id:
            parent = self.access(parent_id, True)
            if space_id and space_id != parent['space_id']:
                self.fail(422, localized_message('differentWorkspaces'))
            space_id = parent['space_id']
        if space_id and not parent_id:
            space_access(self.c, space_id, self.uid, self.fail)
        nid = secrets.token_urlsafe(12)
        self.c.execute('INSERT INTO notes(id,user_id,title,body,blocks,parent_id,space_id,icon,full_width,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (nid, self.uid, title.strip(), '', '[]', parent_id, space_id, '▦', 1, self.now(), self.now()))
        self.c.execute('INSERT INTO note_databases(note_id,properties,views) VALUES(?,?,?)',
                       (nid, json.dumps(DEFAULT_PROPERTIES), json.dumps(DEFAULT_VIEWS)))
        return {'id': nid}

    def title(self, nid, title, version):
        if not title.strip():
            self.fail(422, localized_message('enterTheName'))
        note, _ = self.database(nid, True)
        if note['version'] != version:
            self.fail(409, localized_message('nameHasBeenChangedUpdateTheDatabaseBefore'))
        self.c.execute('UPDATE notes SET title=?,updated=?,version=version+1 WHERE id=?', (title.strip(), self.now(), nid))
        return {'title': title.strip(), 'version': version + 1}

    def configure(self, nid, properties, views, version):
        from app.modules.notion import validate_config, validate_values
        from app.database_features import configure_links, validate_links, synchronize, dependencies
        note, database = self.database(nid, True)
        if database['version'] != version:
            self.fail(409, localized_message('settingsHaveBeenChangedUpdateTheDatabase'))
        previous = json.loads(database['properties'])
        if self.scope:
            properties = self.scope.restore_descriptors(previous, properties)
            views = self.scope.restore_views(json.loads(database['views']),views,properties,nid)
        validate_config(properties, views, self.fail)
        if self.scope and properties != previous:
            self.scope.configure_database(nid)
        by_id = {item['id']: item for item in previous}
        if not self.scope or properties != previous:
            configure_links(self.c, note, properties, previous, self.uid, self.session, self.now, self.fail, scope=self.scope)
            keys = {item['id'] for item in properties if item['type'] not in ('formula', 'rollup')}
            for row in self.c.execute('SELECT * FROM database_records WHERE database_id=?', (nid,)).fetchall():
                old = json.loads(row['properties'])
                values = {key: value for key, value in old.items() if key in keys}
                validate_values(values, properties, self.fail)
                validate_links(self.c, note, properties, values, self.uid, self.fail, scope=self.scope, old=old)
                old_links = {key: value for key, value in values.items()
                             if by_id.get(key, {}).get('inverse_id') == next((p.get('inverse_id') for p in properties if p['id'] == key), None)}
                synchronize(self.c, note, row['note_id'], properties, old_links, values, self.uid,
                            self.session, self.now, self.fail, scope=self.scope)
                if values != old:
                    self.access(row['note_id'], True)
                    self.c.execute('UPDATE database_records SET properties=?,version=version+1 WHERE note_id=?', (json.dumps(values), row['note_id']))
        self.c.execute('UPDATE note_databases SET properties=?,views=?,version=version+1 WHERE note_id=?',
                       (json.dumps(properties), json.dumps(views), nid))
        dependencies(self.c, note, properties, views, self.uid, self.session, self.now, self.fail, scope=self.scope)
        return self.read(nid)

    def add(self, nid, title, values):
        from app.modules.notion import validate_values
        from app.database_features import validate_links, synchronize, dependencies
        if not title.strip():
            self.fail(422, localized_message('enterTheName'))
        note, database = self.database(nid, True)
        properties = json.loads(database['properties'])
        validate_values(values, properties, self.fail)
        validate_links(self.c, note, properties, values, self.uid, self.fail, scope=self.scope)
        if self.c.execute('SELECT COUNT(*) FROM database_records WHERE database_id=?', (nid,)).fetchone()[0] >= 2000:
            self.fail(422, localized_message('databaseLimitIs2000Records'))
        rid = secrets.token_urlsafe(12)
        self.c.execute('INSERT INTO notes(id,user_id,title,body,blocks,parent_id,space_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                       (rid, self.uid, title.strip(), '', '[]', nid, note['space_id'], self.now(), self.now()))
        self.c.execute('INSERT INTO database_records(note_id,database_id,properties) VALUES(?,?,?)', (rid, nid, json.dumps(values)))
        synchronize(self.c, note, rid, properties, {}, values, self.uid, self.session, self.now, self.fail, scope=self.scope)
        dependencies(self.c, note, properties, json.loads(database['views']), self.uid, self.session, self.now, self.fail, scope=self.scope)
        return {'id': rid}

    def update(self, rid, values, version):
        from app.modules.notion import validate_values
        from app.database_features import validate_links, synchronize, dependencies
        self.access(rid, True)
        row = self.c.execute('SELECT * FROM database_records WHERE note_id=?', (rid,)).fetchone()
        if not row:
            self.fail(404, localized_message('recordNotFound'))
        if self.scope:
            note = self.scope.owner_access(row['database_id'])
            database = self.c.execute('SELECT * FROM note_databases WHERE note_id=?', (row['database_id'],)).fetchone()
        else:
            note, database = self.database(row['database_id'], True)
        if row['version'] != version:
            self.fail(409, localized_message('recordHasBeenChangedUpdateTheDatabase'))
        properties, old = json.loads(database['properties']), json.loads(row['properties'])
        validate_values(values, properties, self.fail)
        if self.scope:
            values = self.scope.merge_values(properties, old, values, note['id'])
        validate_values(values, properties, self.fail)
        validate_links(self.c, note, properties, values, self.uid, self.fail, scope=self.scope, old=old)
        synchronize(self.c, note, rid, properties, old, values, self.uid, self.session, self.now, self.fail, scope=self.scope)
        self.c.execute('UPDATE database_records SET properties=?,version=version+1 WHERE note_id=?', (json.dumps(values), rid))
        self.c.execute('UPDATE notes SET updated=? WHERE id=?', (self.now(), rid))
        dependencies(self.c, note, properties, json.loads(database['views']), self.uid, self.session, self.now, self.fail, scope=self.scope)
        return {'ok': True}
