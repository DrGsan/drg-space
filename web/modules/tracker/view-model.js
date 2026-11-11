// These are projections of existing issues, never new task/container records.
const order=(a,b)=>(a.position||0)-(b.position||0)||a.number-b.number;
export const boardOrder=(a,b)=>{
 const entered=item=>item.status==='todo'?item.created:(item.status_entered_at||item.completed_at||item.created);
 return (entered(b)||0)-(entered(a)||0)||b.number-a.number||String(b.id).localeCompare(String(a.id));
};
export function hierarchyRows(items,matches=items){
 const byId=new Map(items.map(item=>[item.id,item])),direct=new Set(matches.map(item=>item.id)),included=new Set(direct);
 for(const item of matches){let current=item,seen=new Set([item.id]);while(current.parent_id&&byId.has(current.parent_id)&&!seen.has(current.parent_id)){seen.add(current.parent_id);current=byId.get(current.parent_id);included.add(current.id);}}
 const children=new Map(),roots=[];
 for(const item of items.filter(item=>included.has(item.id)).sort(order)){
  if(item.parent_id&&included.has(item.parent_id)&&byId.get(item.parent_id)?.group_id===item.group_id){if(!children.has(item.parent_id))children.set(item.parent_id,[]);children.get(item.parent_id).push(item);}
  else roots.push(item);
 }
 const result=[],visited=new Set();
 const visit=(item,ancestors=[])=>{if(visited.has(item.id))return;visited.add(item.id);result.push({item,ancestors,depth:ancestors.length,context:!direct.has(item.id),hasChildren:!!children.get(item.id)?.length,missingParent:!!item.parent_id&&!byId.has(item.parent_id)});for(const child of children.get(item.id)||[])visit(child,[...ancestors,item.id]);};
 roots.forEach(item=>visit(item));for(const item of items)if(included.has(item.id)&&!visited.has(item.id))visit(item);
 return result;
}
export function issueLane(item,items,mode){
 if(mode==='group')return 'group:'+(item.group_id||'none');
 const byId=items instanceof Map?items:new Map(items.map(value=>[value.id,value]));let current=item,seen=new Set();
 while(current&&!seen.has(current.id)){seen.add(current.id);if(current.kind==='epic')return 'epic:'+current.id;if(!current.parent_id)return 'epic:none';if(!byId.has(current.parent_id))return 'missing:'+current.parent_id;current=byId.get(current.parent_id);}
 return 'epic:none';
}
export function issueLanes(items,matches,groups,mode,{empty=true}={}){
 const lanes=new Map(),byId=new Map(items.map(item=>[item.id,item]));
 if(mode==='group')for(const group of groups)lanes.set('group:'+group.id,{id:'group:'+group.id,name:group.name,group_id:group.id,items:[]});
 else for(const item of items.filter(item=>item.kind==='epic').sort(order))lanes.set('epic:'+item.id,{id:'epic:'+item.id,name:item.title,epic:item,items:[]});
 for(const item of [...matches].sort(order)){
  const id=issueLane(item,byId,mode);
  if(!lanes.has(id))lanes.set(id,{id,name:null,missing:id.startsWith('missing:'),group_id:mode==='group'?item.group_id:null,items:[]});
  lanes.get(id).items.push(item);
 }
 return [...lanes.values()].filter(lane=>lane.items.length||(empty&&mode==='group'));
}
export function sortIssues(items,field,direction,locale,assignee,group){
 const collator=new Intl.Collator(locale,{numeric:true,sensitivity:'base'}),rank={todo:0,work:1,review:2,done:3,cancelled:4};
 const value=item=>field==='status'?rank[item.status]:field==='priority'?item.priority:field==='assignee'?assignee(item):field==='group'?group(item):field==='title'?item.title:field==='due'?item.due||null:item.number;
 return [...items].sort((a,b)=>{const av=value(a),bv=value(b);if(av==null||bv==null)return av==null?(bv==null?a.number-b.number:1):-1;const diff=typeof av==='number'?av-bv:collator.compare(av,bv);return (direction==='desc'?-diff:diff)||a.number-b.number;});
}
