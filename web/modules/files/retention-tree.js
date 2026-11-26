import {locale,tHtml,tText} from '../../core/i18n.js';
import {$,date,esc} from '../../core/ui.js';
import {icon} from './icons.js';

const schedulesLabel=count=>tHtml('files:retentionSchedules',{count});
function scheduleLabel(schedule) {
 if(!schedule)return tHtml('files:retentionNone');
 return schedule.delete_at ? esc(date(schedule.delete_at)) : tHtml('files:retentionWaiting');
}

export function mountRetentionTree(host,rootId,onCancel,onDate) {
 let nodes,children,schedules,totals,selectedId;
 const expanded=new Set([rootId]);
 const collator=new Intl.Collator(locale(),{numeric:true,sensitivity:'base'});

 function select(id) {
  const old=host.querySelector('[aria-pressed=true]');
  if(old)old.setAttribute('aria-pressed','false');
  selectedId=id;
  const button=findButton('data-retention-select',id);
  button?.setAttribute('aria-pressed','true');
  renderSelection();
 }
 function findButton(attribute,id) {
  return host.querySelector('['+attribute+'="'+CSS.escape(id)+'"]');
 }
 function renderSelection() {
  const footer=$('[data-retention-selection]',host),node=nodes.get(selectedId);
  footer.hidden=!node;
  if(!node){footer.replaceChildren();return;}
  const path=[];
  for(let item=node;item;item=nodes.get(item.parent_id))path.push(item.name);
  const schedule=schedules.get(node.id),deadline=schedule?.delete_at;
  const detail=schedule?(deadline?tHtml('files:shareDeleteAt',{date:esc(date(deadline))}):tHtml(node.kind==='folder'?'files:shareFolderWaiting':'files:shareRetentionBlocked')):'';
  footer.innerHTML=`<div class="retention-selected-title"><strong>${tHtml('files:retentionSelected')}</strong> ${esc(path.reverse().join(' / '))}</div>
   ${detail?`<small>${detail}</small>`:''}
   <div class="retention-actions">${schedule?`<button type="button" class="text-link" data-retention-date="${esc(node.id)}">${tHtml('files:shareChangeDate')}</button>`:''}
   ${node.id===rootId&&node.kind==='folder'?'':`<button type="button" class="secondary compact" data-retention-off="${esc(node.id)}" ${totals.get(node.id)?'':'disabled'}>${tHtml('files:shareCancelDeletion')}</button>`}</div>`;
 }
 function rowMarkup(node,depth) {
  const folder=node.kind==='folder',hasChildren=children.has(node.id);
  const schedule=schedules.get(node.id);
  const state=folder
   ? schedulesLabel(totals.get(node.id))+(schedule?`<span>${scheduleLabel(schedule)}</span>`:'')
   : scheduleLabel(schedule);
  const toggle=hasChildren?`<button type="button" class="retention-toggle" data-retention-toggle="${esc(node.id)}" aria-expanded="${expanded.has(node.id)}" aria-label="${tHtml(expanded.has(node.id)?'files:retentionCollapse':'files:retentionExpand',{name:esc(node.name)})}">${icon('chevron')}</button>`:'<span></span>';
  return `<li class="retention-row" style="--retention-depth:${depth}">${toggle}
   <button type="button" class="retention-item" data-retention-select="${esc(node.id)}" aria-pressed="${node.id===selectedId}">
    ${icon(folder?'folder':'file')}<span class="retention-name">${esc(node.name)}</span><small>${state}</small>
   </button></li>`;
 }
 function renderRows() {
  const pending=[{node:nodes.get(rootId),depth:0}],rows=[];
  while(pending.length){
   const {node,depth}=pending.pop();
   rows.push(rowMarkup(node,depth));
   if(!expanded.has(node.id))continue;
   const nested=children.get(node.id)||[];
   for(let i=nested.length-1;i>=0;i--)pending.push({node:nested[i],depth:depth+1});
  }
  $('[data-retention-rows]',host).innerHTML=rows.join('');
 }
 host.addEventListener('click',async event=>{
  const button=event.target.closest('button');
  if(!button||button.disabled)return;
  if(button.hasAttribute('data-retention-toggle')){
   const id=button.dataset.retentionToggle;
   if(expanded.has(id)){
    expanded.delete(id);
    for(let item=nodes.get(selectedId);item;item=nodes.get(item.parent_id)){
     if(item.id===id){selectedId=id;break;}
    }
   }else expanded.add(id);
   renderRows();renderSelection();
   findButton('data-retention-toggle',id)?.focus({preventScroll:true});
  }else if(button.hasAttribute('data-retention-select')){
   select(button.dataset.retentionSelect);
  }else if(button.hasAttribute('data-retention-date')){
   onDate(schedules.get(button.dataset.retentionDate));
  }else if(button.hasAttribute('data-retention-off')){
   button.disabled=true;
   try{await onCancel(nodes.get(button.dataset.retentionOff),button);}
   finally{if(button.isConnected)button.disabled=false;}
  }
 });
 return ({nodes:items,files})=>{
  nodes=new Map(items.map(node=>[node.id,node]));
  children=new Map();
  schedules=new Map(files.map(schedule=>[schedule.id,schedule]));
  totals=new Map();
  for(const node of items){
   if(node.id===rootId)continue;
   if(!children.has(node.parent_id))children.set(node.parent_id,[]);
   children.get(node.parent_id).push(node);
  }
  for(const group of children.values())group.sort((a,b)=>(b.kind==='folder')-(a.kind==='folder')||collator.compare(a.name,b.name));
  const root=nodes.get(rootId);
  if(!root){host.replaceChildren();return;}
  const ordered=[root];
  for(let i=0;i<ordered.length;i++)for(const node of children.get(ordered[i].id)||[])ordered.push(node);
  for(let i=ordered.length-1;i>=0;i--){
   const node=ordered[i],total=(totals.get(node.id)||0)+Number(schedules.has(node.id));
   totals.set(node.id,total);
   if(node.id!==rootId)totals.set(node.parent_id,(totals.get(node.parent_id)||0)+total);
  }
  if(!nodes.has(selectedId))selectedId=root.kind==='file'?rootId:null;
  host.innerHTML=`<h3>${tHtml('files:shareRetentionTitle')}</h3><div class="retention-heading"><span>${schedulesLabel(totals.get(rootId))}</span>
   ${root.kind==='folder'?`<button type="button" class="secondary compact" data-retention-off="${esc(rootId)}" ${totals.get(rootId)?'':'disabled'}>${tHtml('files:retentionCancelFolder')}</button>`:''}</div>
   <ul class="retention-tree" data-retention-rows aria-label="${tHtml('files:shareRetentionTitle')}"></ul><div class="retention-selection" data-retention-selection></div>`;
  renderRows();renderSelection();
 };
}
