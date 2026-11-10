"""Canonical ownership of decorations retained by a recoverable source copy.

Only the current manifest of a non-detached link reserves its own page assets.
No provider request or full source-catalog scan belongs in retention.
"""
import json
import re


def manifest_ids(value):
    result={}
    for kind in ('icon','cover'):
        item=value.get('decoration:'+kind) or {}
        identity=item.get('id')
        if isinstance(identity,str) and re.fullmatch(r'[a-f0-9]{32}',identity):result[kind]=identity
    return result


def owned(c,uid,nid,manifest):
    """Check SQL ownership before any physical-object probe, at most one query."""
    expected=manifest_ids(manifest)
    if not expected:return {}
    ids=list(set(expected.values()))
    rows=c.execute('SELECT a.id,a.kind FROM note_assets a JOIN media m ON m.id=a.id JOIN notes n ON n.id=a.note_id WHERE a.note_id=? AND n.user_id=? AND m.user_id=? AND n.space_id IS NULL AND a.id IN ('+','.join('?' for _ in ids)+')',(nid,uid,uid,*ids))
    return {row['kind']:row['id'] for row in rows if expected.get(row['kind'])==row['id']}


def retained(c,candidates):
    """Narrow the encrypted reads to candidate-owned linked pages, in batches."""
    protected=set();manifests={};ids=sorted(candidates)
    for start in range(0,len(ids),200):
        batch=ids[start:start+200]
        rows=c.execute("SELECT a.id,a.kind,p.id AS page_id,p.payload FROM note_assets a JOIN media m ON m.id=a.id JOIN notes n ON n.id=a.note_id JOIN notion_pages p ON p.note_id=n.id AND p.user_id=n.user_id JOIN notion_roots r ON r.id=p.root_id AND r.user_id=n.user_id WHERE r.status!='detached' AND n.space_id IS NULL AND m.user_id=n.user_id AND a.id IN ("+','.join('?' for _ in batch)+')',tuple(batch))
        for row in rows:
            if row['page_id'] not in manifests:
                manifests[row['page_id']]=manifest_ids(json.loads(row['payload']).get('assets',{}))
            if manifests[row['page_id']].get(row['kind'])==row['id']:protected.add(row['id'])
    return protected
