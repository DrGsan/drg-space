"""Cached transfer reports distinguish missing content from write capabilities."""
import json
from app.notion_sync.blocks import convert
from app.notion_sync.state import dump

REVISION=1
GROUPS=('content','appearance','write')
WRITE={'notionSyncedBlockReadonly','notionDatabaseReadonly','notionComputedReadonly'}
APPEARANCE={'notionExternalEmbed','notionPdfAsFile','notionHeadingLevel','notionPageOutsideSync','notionCoverUnavailable','notionIconUnavailable','notionCoverRetryStopped','notionIconRetryStopped'}

def group(issue):
    reason=issue['reason']
    return 'write' if reason in WRITE else 'appearance' if reason in APPEARANCE else 'content'

def record(summary,source,issues):
    identity=source['id']
    counts=summary.setdefault('issue_counts',{})
    counts[identity]={kind:sum(group(issue)==kind for issue in issues) for kind in GROUPS}
    if not any(counts[identity].values()):counts.pop(identity)
    summary['issues']=([item for item in summary.get('issues',[]) if item.get('notion_id')!=identity]+[{'page':source['title'],'notion_id':identity,**issue} for issue in issues])[:500]
    summary['group_counts']={kind:sum(value[kind] for value in counts.values()) for kind in GROUPS}
    summary['issue_count']=sum(summary['group_counts'].values())
    summary['report_revision']=REVISION

def ensure(c,root,payload):
    """Rebuild an old report once from encrypted snapshots, never from HTTP.

    Updates thereafter replace just the changed page's contribution. The page
    counts retain totals even when the displayed list reaches its 500-item cap.
    """
    summary=payload.get('summary')
    if not summary or summary.get('report_revision')==REVISION:return
    # Serialize the first cache fill with normal root writers and reload their
    # current payload. A status response must never restore stale queue/error
    # metadata over a concurrent source update.
    if c.backend.kind!='sqlite' or not c.raw.in_transaction:c.execute('BEGIN IMMEDIATE')
    fresh=c.execute('SELECT payload FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
    if not fresh:return
    payload.clear();payload.update(json.loads(fresh[0]));summary=payload.get('summary')
    if not summary or summary.get('report_revision')==REVISION:return
    sources={}
    for row in c.execute('SELECT notion_id,payload FROM notion_pages WHERE root_id=?',(root['id'],)):
        value=json.loads(row['payload']).get('source')
        if value:sources[row['notion_id']]=value
    for row in c.execute('SELECT notion_id,payload FROM notion_staging WHERE root_id=?',(root['id'],)):
        sources[row['notion_id']]=json.loads(row['payload'])
    # Incomplete checkpoints cannot justify dropping earlier warnings.
    if len(sources)<summary['pages']:return
    summary.update(issues=[],issue_counts={},group_counts=dict.fromkeys(GROUPS,0),issue_count=0,report_revision=REVISION)
    resolve=lambda identity:'https://www.notion.so/'+identity if identity in sources else None
    for source in sources.values():
        _,issues,_=convert(source['blocks'],resolve,source_id=source['id'])
        record(summary,source,source.get('issues',[])+issues)
    c.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(payload),root['id']))

def expose(summary,decorations):
    result={key:value for key,value in summary.items() if key not in ('issue_counts','report_revision')}
    issues=summary['issues']+decorations
    totals=dict(summary.get('group_counts') or {kind:sum(group(issue)==kind for issue in summary['issues']) for kind in GROUPS})
    if not summary.get('group_counts'):
        totals['content']+=max(0,summary.get('issue_count',0)-sum(totals.values()))
    for issue in decorations:totals[group(issue)]+=1
    result.update(issues=issues,issue_count=sum(totals.values()),group_counts=totals,
                  issue_groups=[{'kind':kind,'count':totals[kind],'issues':[issue for issue in issues if group(issue)==kind]} for kind in GROUPS if totals[kind]])
    return result
