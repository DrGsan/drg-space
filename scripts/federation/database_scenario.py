"""Two real installations: typed databases, current grants and legacy record conversion."""
import json


def run(owner, guest, parent):
    from scripts.federation.prototype_client import checked, require
    operation = lambda name, payload, **kw: guest.operation(owner, name, payload, **kw)
    db = operation('database_create', {'parent_id': parent['note_id'], 'revision': parent['revision'], 'title': 'Remote database'})['id']
    properties = [{'id': 'price', 'name': 'Price', 'type': 'number'},
                  {'id': 'total', 'name': 'Total', 'type': 'formula', 'expression': 'prop("Price") * 2'},
                  {'id': 'status', 'name': 'Status', 'type': 'select', 'options': ['Todo', 'Done']},
                  {'id': 'date', 'name': 'Date', 'type': 'date'},
                  {'id': 'before', 'name': 'Before', 'type': 'dependency'}]
    views = [{'id': kind, 'name': kind, 'type': kind,
              **({'group_by': 'status'} if kind == 'board' else {}),
              **({'date_by': 'date'} if kind in ('calendar', 'timeline') else {}),
              'filter': {'property': 'status', 'operator': 'not_equals', 'value': 'Done'},
              'sort': {'property': 'price', 'direction': 'desc'}}
             for kind in ('table', 'list', 'board', 'calendar', 'gallery', 'timeline')]
    configured = operation('database_configure', {'note_id': db, 'revision': 1, 'version': 1, 'properties': properties, 'views': views})
    require(len(configured['views']) == 6, 'Shared database lost its existing views.')
    rid = operation('database_record_create', {'note_id': db, 'revision': 1, 'title': 'Remote record', 'properties': {'price': 3}})['id']
    data = operation('database_read', {'note_id': db, 'revision': 1})
    require(data['records'][0]['properties']['total'] == 6, 'Shared formula did not compute.')
    operation('database_record_update', {'note_id': rid, 'revision': 1, 'version': 1, 'properties': {'price': 4}})
    operation('database_record_update', {'note_id': rid, 'revision': 1, 'version': 1, 'properties': {'price': 99}}, status=409)
    require(operation('database_formula_preview', {'note_id': db, 'revision': 1, 'record_id': rid, 'expression': 'prop("Price") * 2'})['value'] == 8,
            'Shared formula preview diverged.')
    space = checked(owner.http.get('/api/notes/' + db))['space_id']
    private = checked(owner.http.post('/api/databases', json={'title': 'PRIVATE UNGRANTED DATABASE', 'space_id': space}))['id']
    checked(owner.http.put('/api/databases/' + private, json={'version': 1, 'properties': [{'id': 'secret', 'name': 'Secret', 'type': 'number'}], 'views': [{'id': 'table', 'name': 'Table', 'type': 'table'}]}))
    secret = checked(owner.http.post('/api/databases/' + private + '/records', json={'title': 'PRIVATE UNGRANTED RECORD', 'properties': {'secret': 918273645}}))['id']
    # This relation is created by the owner. Sharing its source must not grant
    # access to the neighboring private database, its records or computed data.
    current = checked(owner.http.get('/api/databases/' + db))
    linked = properties + [{'id': 'private', 'name': 'Private relation', 'type': 'relation', 'database_id': private},
                           {'id': 'private_total', 'name': 'Private rollup', 'type': 'rollup', 'relation_id': 'private', 'property': 'secret', 'aggregate': 'sum'}]
    checked(owner.http.put('/api/databases/' + db, json={'version': current['version'], 'properties': linked, 'views': views}))
    current = checked(owner.http.get('/api/databases/' + db))
    row = next(item for item in current['records'] if item['note_id'] == rid)
    checked(owner.http.patch('/api/database-records/' + rid, json={'version': row['version'], 'properties': {'price': 4, 'private': [secret]}}))
    data = operation('database_read', {'note_id': db, 'revision': 1})
    require(all(value not in json.dumps(data) for value in (private, secret, '918273645', 'PRIVATE UNGRANTED RECORD', 'PRIVATE UNGRANTED DATABASE')),
            'A relation or rollup disclosed a neighboring private database.')
    row = next(item for item in data['records'] if item['note_id'] == rid)
    operation('database_record_update', {'note_id': rid, 'revision': 1, 'version': row['version'], 'properties': {'price': 5, 'private': []}})
    owner_row = next(item for item in checked(owner.http.get('/api/databases/' + db))['records'] if item['note_id'] == rid)
    require(owner_row['properties']['private'] == [secret], 'An unrelated edit silently removed a hidden relation.')
    operation('database_configure', {'note_id': db, 'revision': 1, 'version': data['version'], 'properties': data['properties'], 'views': views})
    owner.grant(guest, db, 'viewer')
    operation('database_record_create', {'note_id': db, 'revision': 1, 'title': 'Forbidden', 'properties': {}}, status=403)
    owner.grant(guest, rid, 'editor')
    owner.grant(guest, db, 'none')
    individual = operation('read', {'note_id': rid, 'revision': 1})
    require(individual['parent_id'] is None and individual['database_id'] is None and db not in json.dumps(individual),
            'An individually shared record disclosed its hidden parent database.')
    require(individual['record_database']['record']['properties']['total'] == 10,
            'An individually shared record lost its own formula context.')
    raw = '## Existing legacy record\n\n- First\n- Second\n'
    legacy = checked(owner.http.post('/api/notes', json={'parent_id': db, 'title': 'Unopened legacy record', 'body': raw}))['id']
    viewer = owner.grant(guest, legacy, 'viewer')
    read = operation('read', {'note_id': legacy, 'revision': viewer['revision']})
    require(read.get('legacy') and read['body'] == raw and not read.get('state'), 'Viewer could not read the original legacy record.')
    require(checked(owner.http.get('/api/notes/' + legacy))['blocks'] is None, 'Viewer read mutated a legacy record.')
    blocks = [{'id': 'heading', 'type': 'heading', 'props': {'level': 2}, 'content': 'Existing legacy record', 'children': []},
              {'id': 'first', 'type': 'bulletListItem', 'props': {}, 'content': 'First', 'children': []},
              {'id': 'second', 'type': 'bulletListItem', 'props': {}, 'content': 'Second', 'children': []}]
    operation('database_record_initialize', {'note_id': legacy, 'revision': viewer['revision'], 'version': read['version'], 'blocks': blocks}, status=403)
    editor = owner.grant(guest, legacy, 'editor')
    data = operation('database_record_initialize', {'note_id': legacy, 'revision': editor['revision'], 'version': read['version'], 'blocks': blocks})
    require(data.get('state') and [item['type'] for item in data['blocks']] == ['heading', 'bulletListItem', 'bulletListItem'],
            'Versioned legacy conversion did not preserve rich block structure.')
    history = checked(owner.http.get('/api/notes/' + legacy + '/history'))
    require(any(checked(owner.http.get('/api/notes/' + legacy + '/history/' + item['id']))['body'] == raw for item in history),
            'Legacy conversion did not preserve the exact original Markdown in history.')
    print('PASS: ' + owner.name + ' database API: six views, typed records, versions, formulas, hidden relation/rollup isolation, retained hidden links, individual record grants, viewer-safe legacy read and versioned rich conversion with exact history.')
