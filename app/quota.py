"""Transactional quota accounting for content on both database engines.

Only changed rows are measured on normal writes. Rare cascading deletions
recount the affected owner's content. Authentication and inbox events are not
charged, so a full account can still sign in and remove data.
"""
from app.i18n import message as localized_message
import json
import re
from functools import lru_cache
import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType
from app.http import HTTPError

DEFAULT_LIMIT=1024**3
FIELDS={
 'notion_roots':('payload',),'notion_pages':('payload',),'notion_staging':('payload',),
 'note_assets':('payload',),'users':('settings',),'notes':('title','body','blocks','icon','cover'),
 'note_revisions':('title','body','blocks'),'tasks':('title','day'),
 'spaces':('name',),'note_databases':('properties','views'),'database_records':('properties',),
 'note_comments':('body','mentions'),'collaborative_docs':('state',),'messages':('body',),
 'media':('mime','size'),'files':('name','mime','size'),'chat_recordings':('payload',),
 'authenticators':('label','issuer','secret'),'expense_events':('title','payload'),
 'expense_entries':('payload',),
 'task_lists':('payload',),'task_details':('payload',),
 'tracker_projects':('payload',),'tracker_groups':('payload',),'tracker_issues':('payload',),
 'tracker_comments':('body',),'tracker_history':('payload',),
}
DIRECT={'users':'id','spaces':'owner_id','messages':'sender_id','task_lists':'owner_id','tracker_projects':'owner_id'}
PARENTS={'chat_recordings':('files','file_id','id'),'note_assets':('notes','note_id','id'),'note_revisions':('notes','note_id','id'),'note_databases':('notes','note_id','id'),
 'database_records':('notes','note_id','id'),'collaborative_docs':('notes','note_id','id'),
 'expense_entries':('expense_events','event_id','id'),'task_details':('tasks','task_id','id')}

def owner(c,table,row):
    if table.startswith('tracker_'):
        if table=='tracker_projects':return row['owner_id']
        if 'project_id' in row:
            project=c.execute('SELECT owner_id FROM tracker_projects WHERE id=?',(row['project_id'],)).fetchone()
        else:
            project=c.execute('SELECT p.owner_id FROM tracker_projects p JOIN tracker_issues i ON i.project_id=p.id WHERE i.id=?',(row['issue_id'],)).fetchone()
        return project[0] if project else None
    field=DIRECT.get(table,'user_id')
    if field in row:return row[field]
    parent,fk,pk=PARENTS[table]
    found=c.execute(f'SELECT user_id FROM {parent} WHERE {pk}=?',(row[fk],)).fetchone()
    return found[0] if found else None

def charge(table,row):
    total=0
    for field in FIELDS[table]:
        value=row.get(field)
        if field=='size':total+=value or 0
        elif table=='users' and field=='settings':
            data=json.loads(value or '{}');total+=len(json.dumps({k:v for k,v in data.items() if not k.startswith('_')},ensure_ascii=False).encode())
        elif isinstance(value,str):total+=len(value.encode())
        elif isinstance(value,bytes):total+=len(value)
    return total+(0 if table=='users' else 64)

@lru_cache(maxsize=2048)
def mutation_plan(query):
    tokens=sqlglot.tokenize(query,read='sqlite')
    positions=[t for t in tokens if t.token_type==TokenType.PLACEHOLDER and t.text=='?']
    numbered=query
    for i,t in reversed(list(enumerate(positions))):numbered=numbered[:t.start]+':p'+str(i)+numbered[t.end+1:]
    tree=sqlglot.parse_one(numbered,read='sqlite')
    if not isinstance(tree,(exp.Insert,exp.Update,exp.Delete)):return None
    target=tree.this;table=target.this.name if isinstance(target,exp.Schema) else target.name
    if table not in FIELDS:return None
    from app.database import COLUMNS
    pk=[col['name'] for col in COLUMNS[table] if col['pk']]
    if len(pk)!=1:raise RuntimeError('Charged table requires a single primary key.')
    insert=None
    if isinstance(tree,exp.Insert):
        fields=[f.name for f in target.expressions] if isinstance(target,exp.Schema) else [col['name'] for col in COLUMNS[table]]
        if not isinstance(tree.expression,exp.Values) or len(tree.expression.expressions)!=1:raise RuntimeError('Unsupported quota insert.')
        expressions=tree.expression.expressions[0].expressions
        if pk[0] in fields:
            val=expressions[fields.index(pk[0])]
            insert=('param',int(val.name[1:])) if isinstance(val,exp.Placeholder) else ('literal',val.this) if isinstance(val,exp.Literal) else None
    args=[]
    where=tree.args.get('where')
    def replace(m):args.append(int(m[1]));return '?'
    where=re.sub(r':p(\d+)',replace,where.sql(dialect='sqlite')) if where else ''
    return {'table':table,'pk':pk[0],'kind':type(tree).__name__,'where':where,'args':args,'insert':insert}

def before(c,query,parameters):
    if not re.match(r'^\s*(INSERT|UPDATE|DELETE)\b',query,re.I):return None
    plan=mutation_plan(query)
    if not plan:return None
    rows=[]
    if plan['kind']=='Insert' and plan['insert']:
        loc=plan['insert'];identity=parameters[loc[1]] if loc[0]=='param' else loc[1]
        rows=[dict(r) for r in c.execute(f"SELECT * FROM {plan['table']} WHERE {plan['pk']}=?",(identity,))]
    if plan['kind']!='Insert':rows=[dict(r) for r in c.execute(f"SELECT * FROM {plan['table']} {plan['where']}",tuple(parameters[i] for i in plan['args']))]
    totals={}
    for row in rows:
        uid=owner(c,plan['table'],row)
        if uid:totals[uid]=totals.get(uid,0)+charge(plan['table'],row)
    if plan['kind']=='Delete' and plan['table'] in ('users','spaces','notes'):
        # Cascades can remove another member's comments or shared records.
        # These infrequent administrative/tree operations recount all owners.
        c.quota_recount.update(row[0] for row in c.execute('SELECT id FROM users'))
    return plan,rows,totals,parameters

def after(c,info,cursor):
    if not info:return
    plan,old,totals,parameters=info;table=plan['table'];pk=plan['pk']
    ids=[r[pk] for r in old]
    if plan['kind']=='Insert':
        loc=plan['insert'];value=parameters[loc[1]] if loc and loc[0]=='param' else loc[1] if loc else cursor.lastrowid
        if value is not None:ids.append(value)
    new={}
    for identity in dict.fromkeys(ids):
        row=c.execute(f'SELECT * FROM {table} WHERE {pk}=?',(identity,)).fetchone()
        if not row:continue
        row=dict(row);uid=owner(c,table,row)
        if uid:new[uid]=new.get(uid,0)+charge(table,row)
    for uid in set(totals)|set(new):c.quota_deltas[uid]=c.quota_deltas.get(uid,0)+new.get(uid,0)-totals.get(uid,0)
    if plan['kind']=='Delete':c.quota_recount.update(totals)

# A complete recount is also used to validate restored quota ledgers.
def usage(c,uid):
    total=0
    for table in FIELDS:
        if table in ('tracker_groups','tracker_issues','tracker_history'):
            rows=c.execute(f'SELECT t.* FROM {table} t JOIN tracker_projects p ON p.id=t.project_id WHERE p.owner_id=?',(uid,))
        elif table=='tracker_comments':
            rows=c.execute('SELECT t.* FROM tracker_comments t JOIN tracker_issues i ON i.id=t.issue_id JOIN tracker_projects p ON p.id=i.project_id WHERE p.owner_id=?',(uid,))
        elif table in PARENTS:
            parent,fk,pk=PARENTS[table]
            rows=c.execute(f'SELECT t.* FROM {table} t JOIN {parent} p ON t.{fk}=p.{pk} WHERE p.user_id=?',(uid,))
        else:rows=c.execute(f'SELECT * FROM {table} WHERE {DIRECT.get(table,"user_id")}=?',(uid,))
        total+=sum(charge(table,dict(row)) for row in rows)
    from app.secret_vault import usage as vault_usage
    return total+vault_usage(c,uid)

def finish(c):
    for uid in sorted(set(c.quota_deltas)|c.quota_recount):
        if not c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():continue
        c.execute('INSERT OR IGNORE INTO user_limits(user_id) VALUES(?)',(uid,))
        if c.backend.kind=='postgres':c.raw.execute('SELECT user_id FROM user_limits WHERE user_id=%s FOR UPDATE',(uid,))
        limit,used=c.execute('SELECT limit_bytes,used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()
        updated=usage(c,uid) if uid in c.quota_recount else max(0,used+c.quota_deltas.get(uid,0))
        if limit is not None and updated>limit and updated>used and uid not in getattr(c,'quota_transfer_owners',set()):
            raise HTTPError(413,localized_message('notEnoughSpaceInTheAccountDeleteUnnecessary'))
        c.execute('UPDATE user_limits SET used_bytes=? WHERE user_id=?',(updated,uid))

def summary(c,uid):
    row=c.execute('SELECT limit_bytes,used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()
    return {'limit_bytes':row[0] if row else DEFAULT_LIMIT,'used_bytes':row[1] if row else usage(c,uid)}
