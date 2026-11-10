import {tText} from "../../../core/i18n.js";
// A source belongs to this widget instance; calendar navigation never changes it.
export const taskDefaults = {view:'list',list_id:null};
export function taskSource(config,lists,uid) {
  lists=lists.filter(item=>!item.deletion);
  const view=config?.view||'list';
  const defaultList=lists.find(item=>item.id==='default_'+uid)||lists.find(item=>item.is_default&&item.can_manage);
  const list=view==='list'?(config?.list_id?lists.find(item=>item.id===config.list_id):defaultList):null;
  return {view,list,defaultList,unavailable:view==='list'&&!list,
    label:view==='today'?tText("dashboard:today"):view==='all'?tText("dashboard:allReminders"):view==='flagged'?tText("dashboard:withFlag"):list?.name||tText("dashboard:listUnavailable")};
}
export function sourceTasks(tasks,source,day) {
  if(source.unavailable)return [];
  const selected=tasks.filter(task=>matchesSource(task,source,day))
    .slice().sort((a,b)=>Number(a.done)-Number(b.done)||Number(b.flagged)-Number(a.flagged)||Number(b.priority||0)-Number(a.priority||0)||(a.day||'9999').localeCompare(b.day||'9999')||(a.created||0)-(b.created||0));
  const ids=new Set(selected.map(task=>task.id)),children=new Map(),roots=[];
  for(const task of selected){
    if(task.parent_id&&ids.has(task.parent_id)){
      if(!children.has(task.parent_id))children.set(task.parent_id,[]);
      children.get(task.parent_id).push(task);
    }else roots.push(task);
  }
  const result=[],seen=new Set();
  const visit=task=>{if(seen.has(task.id))return;seen.add(task.id);result.push(task);for(const child of children.get(task.id)||[])visit(child);};
  for(const task of roots)visit(task);
  for(const task of selected)visit(task);
  return result;
}
export function matchesSource(task,source,day) {
  if(source.unavailable)return false;
  return source.view==='list'?task.list_id===(source.list?.id||source.list_id):source.view==='today'?task.day===day:source.view==='flagged'?!!task.flagged:true;
}
export function taskDepth(task,tasks) {
  const byId=tasks instanceof Map?tasks:new Map(tasks.map(item=>[item.id,item])),seen=new Set();let current=task,depth=0;
  while(current.parent_id&&byId.has(current.parent_id)&&!seen.has(current.id)){
    seen.add(current.id);current=byId.get(current.parent_id);depth++;
  }
  return Math.min(depth,4);
}
export function taskDescendants(id,tasks){
  const children=new Map(),seen=new Set([id]),pending=[id],result=[];
  for(const task of tasks){
    if(!task.parent_id)continue;
    if(!children.has(task.parent_id))children.set(task.parent_id,[]);
    children.get(task.parent_id).push(task.id);
  }
  while(pending.length){
    for(const child of children.get(pending.pop())||[]){
      if(seen.has(child))continue;seen.add(child);result.push(child);pending.push(child);
    }
  }
  return result;
}
