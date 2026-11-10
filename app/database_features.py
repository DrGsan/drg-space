"""Role-aware relations, rollups, formulas and acyclic timeline dependencies."""
from app.i18n import message as localized_message
import json
import secrets
from datetime import date,timedelta
from app.http import HTTPError
from app.formulas import FormulaError,evaluate,references
from app.modules.social import note_access

AGGREGATES={'count','count_values','sum','average','min','max','unique','show_original','percent_checked','earliest','latest'}
COMPUTED={'formula','rollup'}

def reject(code,message):raise HTTPError(code,message)

def access(c,nid,uid,fail,scope=None):
    return scope.access(nid) if scope else note_access(c,nid,uid,fail)

def writable(c,node,uid,session,now,fail,scope=None):
    if scope:scope.access(node['id'],write=True)
    from app.modules.notion import require_role
    require_role(c,node,uid,fail,session=session,now=now)

def validate_properties(properties,fail):
    names=[p['name'] for p in properties]
    if len(names)!=len(set(names)):fail(422,localized_message('propertyNamesMustBeUniqueForFormulas'))
    mapping={p['id']:p for p in properties}
    for p in properties:
        if p['type']=='formula':
            try:refs=references(p.get('expression',''))
            except FormulaError as e:fail(422,str(e))
            if any(ref not in mapping and ref not in names and ref not in ('title','Название') for ref in refs):fail(422,localized_message('theFormulaRefersToAnUnknownProperty'))
        if p['type']=='relation':
            if not isinstance(p.get('database_id'),str) or len(p['database_id'])!=16:fail(422,localized_message('selectTheLinkedBase'))
            if 'inverse_id' in p and p['inverse_id'] is not None and (not isinstance(p['inverse_id'],str) or len(p['inverse_id'])>40):fail(422,localized_message('invalidFeedback'))
            if type(p.get('multiple',True)) is not bool or type(p.get('two_way',False)) is not bool:fail(422,localized_message('invalidConnectionMode'))
        if p['type']=='rollup':
            relation=mapping.get(p.get('relation_id'))
            if not relation or relation['type']!='relation' or not isinstance(p.get('property'),str) or p.get('aggregate') not in AGGREGATES:fail(422,localized_message('rollupSelectTheLinkPropertyAndAggregate'))
    visiting=set();done=set()
    def walk(pid):
        if pid in visiting:fail(422,localized_message('cyclicFormulaDependencies'))
        if pid in done:return
        visiting.add(pid);p=mapping[pid]
        if p['type']=='formula':
            for ref in references(p['expression']):
                other=next((x['id'] for x in properties if ref in (x['id'],x['name'])),None)
                if other:walk(other)
        visiting.remove(pid);done.add(pid)
    for pid in mapping:walk(pid)

def configure_links(c,n,properties,old_properties,uid,session,now,fail,*,scope=None):
    from app.modules.notion import require_role
    by_id={p['id']:p for p in properties};old_by_id={p['id']:p for p in old_properties}
    for old in old_properties:
        if old.get('inverse_id') and (old['id'] not in by_id or by_id[old['id']].get('database_id')!=old.get('database_id') or not by_id[old['id']].get('two_way',False)):
            target=c.execute('SELECT * FROM note_databases WHERE note_id=?',(old['database_id'],)).fetchone()
            if target:
                target_note=access(c,target['note_id'],uid,fail,scope);writable(c,target_note,uid,session,now,fail,scope)
                if scope:scope.configure_database(target['note_id'])
                target_props=json.loads(target['properties']);target_props=[p for p in target_props if p['id']!=old['inverse_id']]
                c.execute('UPDATE note_databases SET properties=?,version=version+1 WHERE note_id=?',(json.dumps(target_props),target['note_id']))
                for row in c.execute('SELECT * FROM database_records WHERE database_id=?',(target['note_id'],)).fetchall():
                    values=json.loads(row['properties'])
                    if old['inverse_id'] in values:
                        writable(c,access(c,row['note_id'],uid,fail,scope),uid,session,now,fail,scope)
                        values.pop(old['inverse_id']);c.execute('UPDATE database_records SET properties=?,version=version+1 WHERE note_id=?',(json.dumps(values),row['note_id']))
                if old['id'] in by_id:by_id[old['id']].pop('inverse_id',None)
    for p in properties:
        if p['type']!='relation':continue
        if scope and not scope.allowed(p['database_id']) and p==old_by_id.get(p['id']):continue
        target_note=access(c,p['database_id'],uid,fail,scope)
        if target_note['space_id']!=n['space_id']:fail(422,localized_message('relatedDatabasesMustBeInTheSamePersonal'))
        target=c.execute('SELECT * FROM note_databases WHERE note_id=?',(p['database_id'],)).fetchone()
        if not target:fail(422,localized_message('theLinkedPageIsNotABase'))
        previous=old_by_id.get(p['id'],{})
        if previous.get('inverse_id') and previous.get('database_id')==p['database_id'] and p.get('two_way'):p['inverse_id']=previous['inverse_id']
        if p.get('two_way'):
            if p['database_id']==n['id']:fail(422,localized_message('useAOneWayLinkToLinkThe'))
            writable(c,target_note,uid,session,now,fail,scope)
            target_props=json.loads(target['properties']);inverse=next((x for x in target_props if x['id']==p.get('inverse_id')),None)
            if inverse:
                if inverse.get('database_id')!=n['id'] or inverse.get('inverse_id')!=p['id']:fail(422,localized_message('theFeedbackDoesNotMatchTheOriginal'))
            else:
                if len(target_props)>=30:fail(422,localized_message('theLinkedBaseDoesNotHaveSpaceFor'))
                if scope:scope.configure_database(target_note['id'])
                inverse_id='rel_'+secrets.token_hex(6);name=n['title'][:70]
                if any(x['name']==name for x in target_props):name+=' '+inverse_id[-4:]
                inverse={'id':inverse_id,'name':name,'type':'relation','database_id':n['id'],'multiple':True,'two_way':True,'inverse_id':p['id']}
                p['inverse_id']=inverse_id;target_props.append(inverse)
                c.execute('UPDATE note_databases SET properties=?,version=version+1 WHERE note_id=?',(json.dumps(target_props),target_note['id']))
        else:p.pop('inverse_id',None)
    for p in properties:
        if p['type']!='rollup':continue
        relation=by_id[p['relation_id']]
        if scope and not scope.allowed(relation['database_id']):
            if p==old_by_id.get(p['id']):continue
            scope.access(relation['database_id'])
        target=c.execute('SELECT properties FROM note_databases WHERE note_id=?',(relation['database_id'],)).fetchone()
        target_props=json.loads(target[0])
        if p['property']!='title' and not any(x['id']==p['property'] for x in target_props):fail(422,localized_message('unknownPropertyOfTheLinkedBase'))

def validate_links(c,n,properties,values,uid,fail,*,scope=None,old=None):
    for p in properties:
        if p['type'] not in ('relation','dependency'):continue
        for identity in values.get(p['id']) or []:
            if scope and not scope.reference(p,identity,n['id']):
                if identity in (old or {}).get(p['id'],[]):continue
                scope.denied()
            if scope and p['type']=='dependency' and identity not in (old or {}).get(p['id'],[]):
                scope.dependency_chain(identity,n['id'])
            target=access(c,identity,uid,fail,scope)
            row=c.execute('SELECT database_id FROM database_records WHERE note_id=?',(identity,)).fetchone()
            expected=p['database_id'] if p['type']=='relation' else n['id']
            if not row or row[0]!=expected or target['space_id']!=n['space_id']:fail(422,localized_message('theLinkMustPointToARecordIn'))

def synchronize(c,n,rid,properties,old,new,uid,session,now,fail,*,scope=None):
    from app.modules.notion import require_role
    for p in properties:
        if p['type']!='relation' or not p.get('inverse_id'):continue
        was=set(old.get(p['id']) or []);current=set(new.get(p['id']) or [])
        for target_id in was^current:
            target=access(c,target_id,uid,fail,scope);writable(c,target,uid,session,now,fail,scope)
            record=c.execute('SELECT properties FROM database_records WHERE note_id=?',(target_id,)).fetchone()
            values=json.loads(record[0]);back=values.get(p['inverse_id']) or []
            if target_id in current:
                if rid not in back:
                    if len(back)>=100:fail(422,localized_message('theReverseFeedbackAllowsAMaximumOf100'))
                    back.append(rid)
            else:back=[x for x in back if x!=rid]
            values[p['inverse_id']]=back
            c.execute('UPDATE database_records SET properties=?,version=version+1 WHERE note_id=?',(json.dumps(values),target_id));c.execute('UPDATE notes SET updated=? WHERE id=?',(now(),target_id))

def dependencies(c,n,properties,views,uid,session,now,fail,*,scope=None):
    from app.modules.notion import require_role
    records={r['note_id']:dict(r) for r in c.execute('SELECT r.* FROM database_records r JOIN notes n ON n.id=r.note_id WHERE database_id=? AND n.deleted IS NULL',(n['id'],))}
    if scope:records={rid:row for rid,row in records.items() if scope.allowed(rid)}
    values={key:json.loads(r['properties']) for key,r in records.items()}
    props=[p for p in properties if p['type']=='dependency']
    graph={rid:set() for rid in records}
    for rid,v in values.items():
        for p in props:
            for parent in v.get(p['id']) or []:
                if parent not in records:continue
                if parent==rid:fail(422,localized_message('theTaskCannotDependOnItself'))
                graph[rid].add(parent)
    degree={rid:len(parents) for rid,parents in graph.items()};followers={rid:set() for rid in records}
    for rid,parents in graph.items():
        for parent in parents:followers[parent].add(rid)
    queue=[rid for rid,num in degree.items() if not num];order=[]
    for rid in queue:
        order.append(rid)
        for follower in followers[rid]:
            degree[follower]-=1
            if degree[follower]==0:queue.append(follower)
    if len(order)!=len(records):fail(422,localized_message('dependenciesFormACycleRemoveTheFeedback'))
    for view in views:
        if view.get('type')!='timeline' or not view.get('auto_shift') or not view.get('dependency_by') or not view.get('date_by'):continue
        for rid in order:
            current=values[rid].get(view['date_by'])
            if not current or not current.get('start'):continue
            bounds=[]
            for parent in values[rid].get(view['dependency_by']) or []:
                other=values.get(parent,{}).get(view['date_by'])
                if other and other.get('start'):bounds.append(date.fromisoformat(other.get('end') or other['start']))
            if not bounds:continue
            try:
                earliest=max(bounds)+timedelta(days=1+view.get('lag_days',0));start=date.fromisoformat(current['start']);end=date.fromisoformat(current.get('end') or current['start'])
                if start>=earliest:continue
                node=access(c,rid,uid,fail,scope);writable(c,node,uid,session,now,fail,scope)
                shifted={'start':earliest.isoformat(),'end':(earliest+(end-start)).isoformat()}
            except (ValueError,OverflowError):fail(422,localized_message('theDependencyOffsetExceedsTheValidLimits'))
            values[rid][view['date_by']]=shifted
            c.execute('UPDATE database_records SET properties=?,version=version+1 WHERE note_id=?',(json.dumps(values[rid]),rid));c.execute('UPDATE notes SET updated=? WHERE id=?',(now(),rid))

class Evaluator:
    def __init__(self,c,uid,stamp,*,scope=None,source_record=None):
        self.c,self.uid,self.stamp=c,uid,stamp;self.databases={};self.cache={};self.stack=set();self.budget=[1000000]
        self.scope,self.source_record=scope,source_record
    def database(self,nid):
        if nid not in self.databases:
            if self.scope and self.source_record and nid==self.source_record[0]:self.scope.access(self.source_record[1])
            else:access(self.c,nid,self.uid,reject,self.scope)
            d=self.c.execute('SELECT properties FROM note_databases WHERE note_id=?',(nid,)).fetchone()
            if not d:raise FormulaError('Связанная база недоступна.')
            props=json.loads(d[0]);rows={r['note_id']:dict(r) for r in self.c.execute('SELECT r.*,n.title,n.icon FROM database_records r JOIN notes n ON n.id=r.note_id WHERE database_id=? AND n.deleted IS NULL',(nid,))}
            from app.note_access import visible_ids
            allowed=visible_ids(self.c,self.uid) if not self.scope else {rid for rid in rows if self.scope.allowed(rid)}
            if self.scope and self.source_record and nid==self.source_record[0]:allowed={self.source_record[1]}
            rows={rid:row for rid,row in rows.items() if rid in allowed}
            for row in rows.values():
                row['properties']=json.loads(row['properties'])
                if self.scope:row['properties']=self.scope.values(props,row['properties'],nid)
            self.databases[nid]=(props,rows)
        return self.databases[nid]
    def value(self,nid,rid,pid):
        key=(nid,rid,pid)
        if key in self.cache:return self.cache[key]
        if key in self.stack or len(self.stack)>24:raise FormulaError('Цикл вычисляемых свойств.')
        self.stack.add(key)
        try:
            props,rows=self.database(nid);row=rows.get(rid)
            if not row:raise FormulaError('Связанная запись недоступна.')
            if pid in ('title','Название'):return row['title']
            p=next((x for x in props if pid in (x['id'],x['name'])),None)
            if not p:raise FormulaError('Неизвестное свойство: '+str(pid))
            if p['type']=='formula':result=evaluate(p['expression'],lambda name:self.value(nid,rid,name),date.fromtimestamp(self.stamp),self.budget)
            elif p['type']=='rollup':
                relation=next(x for x in props if x['id']==p['relation_id']);target=relation['database_id'];_,targets=self.database(target)
                linked=[x for x in row['properties'].get(relation['id'],[]) if x in targets]
                vals=[self.value(target,x,p['property']) for x in linked];operation=p['aggregate'];present=[v for v in vals if v is not None and v!='' and v!=[]]
                numbers=[v for v in present if type(v) in (int,float)]
                if operation=='count':result=len(linked)
                elif operation=='count_values':result=len(present)
                elif operation=='sum':result=sum(numbers)
                elif operation=='average':result=sum(numbers)/len(numbers) if numbers else 0
                elif operation=='min':result=min(numbers) if numbers else None
                elif operation=='max':result=max(numbers) if numbers else None
                elif operation=='percent_checked':result=100*sum(v is True for v in vals)/len(vals) if vals else 0
                elif operation in ('earliest','latest'):
                    dates=[v.get('start') if isinstance(v,dict) else v for v in present];result=(min(dates) if operation=='earliest' else max(dates)) if dates else None
                elif operation=='unique':result=list({json.dumps(v,sort_keys=True):v for v in vals}.values())
                else:result=vals
            else:result=row['properties'].get(p['id'],0 if p['type']=='number' else False if p['type']=='checkbox' else [] if p['type'] in ('relation','dependency','multi_select') else None)
            self.cache[key]=result;return result
        finally:self.stack.remove(key)
    def apply(self,nid,records):
        properties,_=self.database(nid)
        for row in records:
            errors={}
            for p in properties:
                if p['type'] in COMPUTED:
                    try:row['properties'][p['id']]=self.value(nid,row['note_id'],p['id'])
                    except (FormulaError,HTTPError,ValueError,TypeError,KeyError) as e:
                        row['properties'][p['id']]=None;errors[p['id']]='Вычисляемое свойство недоступно.' if self.scope else str(e.detail) if isinstance(e,HTTPError) else str(e)
            row['computed_errors']=errors
        return records
    def options(self,nid):
        props,records=self.database(nid);result={}
        for p in props:
            if p['type'] not in ('relation','dependency'):continue
            target=p['database_id'] if p['type']=='relation' else nid
            try:
                _,rows=self.database(target)
                result[p['id']]={'database_id':target if not self.scope or self.scope.allowed(target) else None,'records':[{'id':r['note_id'],'title':r['title'],'icon':r['icon']} for r in rows.values()]}
            except (FormulaError,HTTPError):result[p['id']]={'database_id':None if self.scope else target,'records':[],'unavailable':True}
        return result
