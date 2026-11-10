"""Move canonical block subtrees between two pages in one transaction."""
from app.i18n import message as localized_message
import copy
import json
import re
import secrets

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pycrdt import Map, XmlElement, XmlFragment, XmlText

from app.collaboration import MAX_STATE, load_doc, project
from app.http import Request, Router
from app.modules.notion import can_manage, page_role, require_role
from app.modules.notes import validate_blocks
from app.note_locks import lock_status
from app.share_editing import replace_content


class BlockMove(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    target_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    block_ids: list[str] = Field(min_length=1, max_length=2000)
    source_version: int = Field(ge=1)
    target_version: int = Field(ge=1)
    target_blocks: list[dict] | None = Field(default=None, max_length=2000)

    @field_validator('block_ids')
    @classmethod
    def unique_block_ids(cls, value):
        if len(set(value)) != len(value) or any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', item) for item in value):
            raise ValueError(localized_message('selectBlocksWithUniqueAndValidIdentifiers'))
        return value


def create_note_blocks_router(db, ready, owned, now, fail, publication_update):
    router = Router()

    def identifiers(blocks):
        result = set()
        for block in blocks:
            if block.get('id') is not None:
                result.add(block['id'])
            result.update(identifiers(block.get('children', [])))
        return result

    def linked_pages_ok(c, blocks, uid, scope):
        for block in blocks:
            if block['type'] == 'pageLink':
                target = owned(c, block.get('props', {}).get('pageId', ''), uid)
                if target['space_id'] != scope:
                    fail(422, localized_message('linkToSubpageMustPointToTheSame'))
            linked_pages_ok(c, block.get('children', []), uid, scope)

    def document(c, page, imported=None):
        saved = c.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (page['id'],)).fetchone() if page['space_id'] else None
        if saved:
            doc = load_doc(saved['state'])
            title, body, blocks = project(doc, fail)
            # Never replace a newer canonical document with a stale JSON projection.
            if (title, body, blocks) != (page['title'], page['body'], json.loads(page['blocks']) if page['blocks'] is not None else None):
                fail(409, localized_message('theSharedPageHasBeenModifiedOpenIt'))
        else:
            doc = load_doc(None)
            blocks = json.loads(page['blocks']) if page['blocks'] is not None else imported
        validate_blocks(blocks, fail)
        if blocks is None:
            fail(409, localized_message('firstOpenAndSaveThePageInThe'))
        if page['space_id'] and not saved:
            # A document without an epoch has no old CRDT identities to preserve.
            try:
                if blocks:
                    _, _, blocks = replace_content(doc, page['title'], blocks, fail)
                else:
                    with doc.transaction():
                        doc.get('prosemirror', type=XmlFragment).children.append(XmlElement('blockGroup', {}, []))
                        doc.get('meta', type=Map)['title'] = page['title']
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                fail(422, localized_message('invalidPageContentCopyingIsCancelled'))
        return copy.deepcopy(blocks), doc, saved

    def move_shared(source_doc, target_doc, selected, empty_id):
        source_root = list(source_doc.get('prosemirror', type=XmlFragment).children)[0]
        target_root = list(target_doc.get('prosemirror', type=XmlFragment).children)[0]
        moved, texts = [], []

        def clone(node):
            if isinstance(node, XmlText):
                duplicate = XmlText()
                texts.append((duplicate, node.diff(), dict(node.attributes)))
                return duplicate
            if not isinstance(node, XmlElement):
                fail(422, localized_message('invalidContentOfTheCopiedBlock'))
            return XmlElement(node.tag, dict(node.attributes), [clone(child) for child in node.children])

        def remove(group):
            indexes = []
            for index, container in enumerate(list(group.children)):
                if dict(container.attributes).get('id') in selected:
                    moved.append(clone(container))
                    indexes.append(index)
                else:
                    children = list(container.children)
                    if len(children) == 2:
                        remove(children[1])
            for index in reversed(indexes):
                del group.children[index]

        # Keep both root groups and every untouched container/text identity. Late
        # updates from other clients must still address the same live Yjs nodes.
        with source_doc.transaction():
            remove(source_root)
            if not len(source_root.children):
                source_root.children.append(XmlElement('blockContainer', {'id': empty_id}, [XmlElement('paragraph', {}, [])]))
        with target_doc.transaction():
            for container in moved:
                target_root.children.append(container)
            for text, chunks, attrs in texts:
                for key, value in attrs.items():
                    text.attributes[key] = value
                # Inserting after attachment with explicit marks preserves each
                # Unicode segment without assuming Python/Yrs offset lengths.
                for content, marks in chunks:
                    if not isinstance(content, str):
                        fail(422, localized_message('invalidTextContent'))
                    text.insert(len(text), content, marks or {})

    def snapshot(c, page, stamp):
        from app.note_history import snapshot as checkpoint
        checkpoint(c,page,stamp)

    def comment_threads(c, source_id, target_id, moved_ids):
        rows = c.execute('SELECT id,parent_id,block_id,mentions FROM note_comments WHERE note_id=?', (source_id,)).fetchall()
        selected = {row['id'] for row in rows if row['parent_id'] is None and row['block_id'] in moved_ids}
        while True:
            replies = {row['id'] for row in rows if row['parent_id'] in selected}
            added = replies - selected
            if not added:
                break
            selected.update(added)
        if selected and c.execute('SELECT COUNT(*) FROM note_comments WHERE note_id=?', (target_id,)).fetchone()[0] + len(selected) > 1000:
            fail(422, localized_message('theDestinationPageWillHaveMoreThan1000'))
        recipients = {uid for row in rows if row['id'] in selected for uid in json.loads(row['mentions'])}
        return selected, recipients

    def move_comments(c, source, target, comments, recipients):
        for identity in comments:
            c.execute('UPDATE note_comments SET note_id=? WHERE id=? AND note_id=?', (target['id'], identity, source['id']))
        # Keep existing mention inbox links attached to their relocated threads.
        # IDs, read states and delivery timestamps remain unchanged.
        for uid in recipients:
            for row in c.execute("SELECT id,payload FROM notifications WHERE user_id=? AND kind='mention'", (uid,)).fetchall():
                data = json.loads(row['payload'])
                if data.get('note_id') == source['id'] and data.get('comment_id') in comments:
                    data.update(note_id=target['id'], title=target['title'])
                    c.execute('UPDATE notifications SET payload=? WHERE id=?', (json.dumps(data, ensure_ascii=False), row['id']))

    def payload(c, page, session):
        from app.modules.note_assets import asset_payload
        result = dict(page)
        result.pop('user_id', None)
        result.pop('trash_batch', None)
        result['blocks'] = json.loads(page['blocks'])
        result.update(asset_payload(c, page))
        result.update(lock_status(c, page, session, now))
        record = c.execute('SELECT database_id FROM database_records WHERE note_id=?', (page['id'],)).fetchone()
        result.update(kind='page', database_id=record[0] if record else None, role=page_role(c, page, session['user_id']), manage=can_manage(c, page, session['user_id']))
        result['favorite'] = int(bool(c.execute('SELECT 1 FROM note_favorites WHERE note_id=? AND user_id=?', (page['id'], session['user_id'])).fetchone()))
        return result

    @router.post('/api/notes/{nid}/blocks/move')
    async def move_blocks(nid: str, data: BlockMove, request: Request):
        session = ready(request)
        if nid == data.target_id:
            fail(422, localized_message('selectAnotherPageToTransfer'))
        events = []
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            source = owned(c, nid, session['user_id'])
            target = owned(c, data.target_id, session['user_id'])
            for page, version in ((source, data.source_version), (target, data.target_version)):
                require_role(c, page, session['user_id'], fail, session=session, now=now)
                if page['version'] != version:
                    fail(409, localized_message('oneOfThePagesHasBeenModifiedUpdate'))
                if c.execute('SELECT 1 FROM note_databases WHERE note_id=?', (page['id'],)).fetchone():
                    fail(422, localized_message('blocksCanOnlyBeCopiedBetweenRegularPages'))
            if source['space_id'] != target['space_id']:
                fail(422, localized_message('copyBlocksBetweenPagesWithinTheSameWorkspace'))
            from app.note_access import access_index
            index=access_index(c)
            source_scope,source_policy=index.scope(source);target_scope,target_policy=index.scope(target)
            if ((source_policy and source_policy['mode']=='restricted') or (target_policy and target_policy['mode']=='restricted')) and source_scope[-1]['id']!=target_scope[-1]['id']:
                fail(422,localized_message('pagesHaveDifferentAccessRestrictionsCopyingBlocksBetween'))
            if target['blocks'] is not None and 'target_blocks' in data.model_fields_set:
                fail(422, localized_message('theContentOfTheDestinationPageHasAlready'))
            if target['blocks'] is None and data.target_blocks is None:
                fail(422, localized_message('provideTheBlockRepresentationOfTheDestinationPage'))
            source_blocks, source_doc, source_saved = document(c, source)
            target_blocks, target_doc, target_saved = document(c, target, data.target_blocks)
            selected = set(data.block_ids)
            if not selected.issubset(identifiers(source_blocks)):
                fail(409, localized_message('theSelectedBlockIsNoLongerOnThe'))
            moved = []

            def remove_selected(items):
                remaining = []
                for block in items:
                    if block.get('id') in selected:
                        # A selected ancestor carries all children, even if they were also selected.
                        moved.append(block)
                    else:
                        if block.get('children'):
                            block['children'] = remove_selected(block['children'])
                        remaining.append(block)
                return remaining

            source_blocks = remove_selected(source_blocks)
            if identifiers(moved) & identifiers(target_blocks):
                fail(409, localized_message('thereIsAlreadyABlockWithTheSame'))
            comments, recipients = comment_threads(c, source['id'], target['id'], identifiers(moved))
            target_blocks += moved
            if not source_blocks:
                source_blocks = [{'id': secrets.token_hex(12), 'type': 'paragraph', 'props': {}, 'content': [], 'children': []}]
            for page, blocks in ((source, source_blocks), (target, target_blocks)):
                validate_blocks(blocks, fail)
                linked_pages_ok(c, blocks, session['user_id'], page['space_id'])
            before_states = {source['id']: source_doc.get_state(), target['id']: target_doc.get_state()}
            if source['space_id']:
                move_shared(source_doc, target_doc, selected, source_blocks[0]['id'])
            stamp = now()
            prepared = []
            for page, blocks, doc, saved in ((source, source_blocks, source_doc, source_saved), (target, target_blocks, target_doc, target_saved)):
                try:
                    if page['space_id']:
                        title, body, canonical = project(doc, fail)
                        if len(doc.get_update()) > MAX_STATE:
                            fail(413, localized_message('theSharedDocumentIsTooLarge'))
                        linked_pages_ok(c, canonical, session['user_id'], page['space_id'])
                    else:
                        title, body, canonical = replace_content(doc, page['title'], blocks, fail)
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    fail(422, localized_message('invalidPageContentCopyingIsCancelled'))
                # Private documents keep their exact rich JSON; the CRDT supplies only Markdown.
                stored_blocks = canonical if page['space_id'] else blocks
                prepared.append((page, title, body, stored_blocks, doc, saved, before_states[page['id']]))
            for page, title, body, blocks, doc, saved, before in prepared:
                snapshot(c, page, stamp)
                if page['space_id']:
                    state, update = doc.get_update(), doc.get_update(before)
                    epoch = saved['epoch'] if saved else secrets.token_urlsafe(16)
                    c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET state=excluded.state,updated=excluded.updated', (page['id'], state, epoch, stamp))
                    events.append((page['id'], state, update, epoch, page['version'] + 1, not bool(saved)))
                c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?', (title, body, json.dumps(blocks, ensure_ascii=False), stamp, page['id']))
            move_comments(c, source, target, comments, recipients)
            result = {name: payload(c, owned(c, page['id'], session['user_id']), session) for name, page in (('source', source), ('target', target))}
        for event in events:
            await publication_update(*event)
        return result

    return router
