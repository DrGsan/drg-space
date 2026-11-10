"""Shared page-link traversal and cleanup, independent of storage or permissions."""
import copy
import json
import secrets


def page_links(blocks):
    result=set()
    for block in blocks or []:
        if block.get('type')=='pageLink':result.add(block.get('props',{}).get('pageId'))
        result.update(page_links(block.get('children')))
    return result


def without_page_links(blocks, identities):
    result=[]
    for original in blocks or []:
        block=copy.deepcopy(original)
        block['children']=without_page_links(block.get('children'),identities)
        if block.get('type')=='pageLink' and block.get('props',{}).get('pageId') in identities:
            # Preserve any user blocks nested beneath the former navigation link.
            if not block['children']:continue
            block.update(type='paragraph',props={},content=[])
        result.append(block)
    return result


def prune_stored_links(c, identities, stamp, fail, parents=None):
    """Prune only explicit deleted targets. Keep shared CRDT epochs and all
    unrelated nodes; publication updates are delivered after the SQL commit.
    """
    from pycrdt import XmlElement,XmlFragment
    from app.collaboration import load_doc,project
    from app.share_editing import replace_content
    from app.note_history import snapshot
    events=[]
    for page in c.execute('SELECT * FROM notes WHERE deleted IS NULL AND blocks IS NOT NULL').fetchall():
        if parents is not None and page['id'] not in parents:continue
        blocks=json.loads(page['blocks'])
        if not page_links(blocks)&identities:continue
        blocks=without_page_links(blocks,identities) or [{'id':secrets.token_hex(12),'type':'paragraph','content':[],'children':[]}]
        snapshot(c,page,stamp)
        saved=c.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(page['id'],)).fetchone()
        doc=load_doc(saved['state'] if saved else None);before=doc.get_state()
        if saved:
            def prune(group):
                for index in range(len(group.children)-1,-1,-1):
                    container=group.children[index];children=list(container.children)
                    if len(children)>1:prune(children[1])
                    if children and children[0].tag=='pageLink' and dict(children[0].attributes).get('pageId') in identities:
                        if len(children)>1 and len(children[1].children):
                            del container.children[0];container.children.insert(0,XmlElement('paragraph',{},[]))
                        else:del group.children[index]
            root=list(doc.get('prosemirror',type=XmlFragment).children)[0]
            with doc.transaction():
                prune(root)
                if not len(root.children):root.children.append(XmlElement('blockContainer',{'id':secrets.token_hex(12)},[XmlElement('paragraph',{},[])]))
            title,body,blocks=project(doc,fail)
        else:
            title,body,_=replace_content(doc,page['title'],blocks,fail)
        if page['space_id'] or saved:
            epoch=saved['epoch'] if saved else secrets.token_urlsafe(16)
            state=doc.get_update()
            c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET state=excluded.state,updated=excluded.updated',(page['id'],state,epoch,stamp))
            events.append((page['id'],state,doc.get_update(before),epoch,page['version']+1,not bool(saved)))
        c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',(title,body,json.dumps(blocks,ensure_ascii=False),stamp,page['id']))
    return events


def prune_vault_links(value,identities,stamp,fail):
    from app.collaboration import load_doc
    from app.share_editing import replace_content
    changed=False
    for nid,item in value['notes'].items():
        if nid in identities or not page_links(item.get('blocks'))&identities:continue
        blocks=without_page_links(item['blocks'],identities) or [{'type':'paragraph','content':[]}]
        _,body,_=replace_content(load_doc(None),item['title'],blocks,fail)
        item.update(blocks=blocks,body=body,updated=stamp,version=item['version']+1);changed=True
    return changed
