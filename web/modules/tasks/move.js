import {tText} from '../../core/i18n.js';

export const siblingOrder=(a,b)=>(a.position||0)-(b.position||0)||(a.created||0)-(b.created||0)||(a.id<b.id?-1:a.id>b.id?1:0);
export async function taskMoveRevision(tasks,list){
 const rows=tasks.filter(t=>t.list_id===list).sort((a,b)=>a.id<b.id?-1:a.id>b.id?1:0).map(t=>[t.id,t.parent_id||null,t.position||0,t.version]);
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(rows)));
 return [...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,'0')).join('');
}
export function movePlan(tasks,sourceId,targetId,zone){
 const source=tasks.find(t=>t.id===sourceId),target=tasks.find(t=>t.id===targetId);
 if(!source||!target||source.id===target.id||source.list_id!==target.list_id)return null;
 const descendants=new Set([sourceId]);let changed=true;
 while(changed){changed=false;for(const t of tasks)if(descendants.has(t.parent_id)&&!descendants.has(t.id)){descendants.add(t.id);changed=true;}}
 if(descendants.has(targetId))return null;
 const parent=zone==='inside'?target.id:target.parent_id||null;
 if(parent!==source.parent_id&&parent){const p=tasks.find(t=>t.id===parent);if(!p||p.parent_id||descendants.size>1)return null;}
 const siblings=tasks.filter(t=>t.list_id===source.list_id&&(t.parent_id||null)===parent&&t.id!==sourceId).sort(siblingOrder);
 const before=zone==='before'?target.id:zone==='after'?siblings[siblings.findIndex(t=>t.id===targetId)+1]?.id||null:null;
 return {parent_id:parent,before_id:before,target:targetId,zone};
}

// Pointer-based handles work in Safari and touch Web Apps without taking
// scrolling away from the rest of a reminder row. No listeners survive render.
export class ReminderDrag {
 constructor(host,{tasks,enabled,blocked,move,open}){
  Object.assign(this,{host,tasks,enabled,blocked,move,open});this.handles=[...host.querySelectorAll('[data-move-task]')];
  this.down=e=>this.pointerDown(e);this.motion=e=>this.pointerMove(e);this.up=e=>this.pointerUp(e);this.cancel=()=>this.finish();this.key=e=>{if(e.key==='Escape'&&this.pointer){e.preventDefault();e.stopPropagation();this.finish();}};
  this.click=e=>{const h=e.target.closest('[data-move-task]');if(!h)return;if(this.suppressClick&&e.detail!==0){this.suppressClick=false;e.preventDefault();return;}open(h.dataset.moveTask);};
  host.addEventListener('click',this.click);host.addEventListener('pointerdown',this.down);window.addEventListener('pointermove',this.motion,{passive:false});window.addEventListener('pointerup',this.up);window.addEventListener('pointercancel',this.cancel);window.addEventListener('keydown',this.key,true);window.addEventListener('blur',this.cancel);
 }
 get active(){return !!this.pointer;}
 pointerDown(e){
  const handle=e.target.closest('[data-move-task]');if(!handle||!this.enabled||this.blocked()||e.button!==0||e.isPrimary===false)return;
  this.suppressClick=false;this.pointer={id:e.pointerId,x:e.clientX,y:e.clientY,latestX:e.clientX,latestY:e.clientY,source:handle.dataset.moveTask,handle,snapshot:this.tasks.slice()};
  handle.setPointerCapture(e.pointerId);
 }
 pointerMove(e){
  const p=this.pointer;if(!p||p.id!==e.pointerId)return;p.latestX=e.clientX;p.latestY=e.clientY;
  if(!this.dragging&&Math.hypot(e.clientX-p.x,e.clientY-p.y)<8)return;
  e.preventDefault();if(!this.dragging){this.dragging=true;this.suppressClick=true;this.host.classList.add('tasks-is-dragging');
   this.ghost=document.createElement('div');this.ghost.className='tasks-drag-ghost';this.ghost.textContent=this.tasks.find(t=>t.id===p.source)?.title||'';document.body.append(this.ghost);this.ghostWidth=this.ghost.offsetWidth;this.ghostHeight=this.ghost.offsetHeight;
   this.host.querySelector(`[data-task-item="${CSS.escape(p.source)}"]`)?.classList.add('tasks-drag-source');
   this.frame=requestAnimationFrame(()=>this.scroll());
  }this.preview();
 }
 preview(){
  const p=this.pointer;if(!p||!this.dragging)return;this.ghost.style.transform=`translate3d(${Math.max(8,Math.min(innerWidth-this.ghostWidth-8,p.latestX+14))}px,${Math.min(innerHeight-this.ghostHeight-8,p.latestY+16)}px,0)`;
  this.target?.classList.remove('tasks-drop-before','tasks-drop-after','tasks-drop-inside','tasks-drop-blocked');this.target=null;this.plan=null;
  const row=document.elementFromPoint(p.latestX,p.latestY)?.closest('.tasks-reminder-row');
  if(!row||!this.host.contains(row))return;
  const id=row.closest('[data-task-item]').dataset.taskItem,b=row.getBoundingClientRect(),ratio=(p.latestY-b.top)/b.height,zone=ratio<.25?'before':ratio>.75?'after':'inside';
  this.plan=movePlan(p.snapshot,p.source,id,zone);
  const visibleRows=[...row.closest('[data-task-item]').querySelectorAll('.tasks-reminder-row')];
  this.target=zone==='after'&&this.plan?visibleRows.at(-1):row;this.target.classList.add(this.plan?'tasks-drop-'+zone:'tasks-drop-blocked');
  const label=this.host.querySelector('[data-task-move-hint]');if(label)label.textContent=this.plan?tText('tasks:move'+(zone==='inside'?'Inside':zone==='before'?'Before':'After'),{title:this.tasks.find(t=>t.id===id)?.title||''}):tText('tasks:moveBlocked');
 }
 scroll(){
  if(!this.pointer||!this.dragging)return;const area=this.host.querySelector('[data-task-rows]'),b=area.getBoundingClientRect(),y=this.pointer.latestY;
  const speed=y<b.top+48?-Math.min(16,(b.top+48-y)/3):y>b.bottom-48?Math.min(16,(y-b.bottom+48)/3):0;
  if(speed){area.scrollTop+=speed;this.preview();}this.frame=requestAnimationFrame(()=>this.scroll());
 }
 pointerUp(e){const p=this.pointer;if(!p||p.id!==e.pointerId)return;const plan=this.plan,wasDragging=this.dragging;this.finish();if(wasDragging&&plan)this.move(p.source,plan,p.snapshot);}
 finish(){
  cancelAnimationFrame(this.frame);this.ghost?.remove();this.ghost=null;
  if(this.pointer){const {handle,id}=this.pointer;if(handle.hasPointerCapture(id))handle.releasePointerCapture(id);}
  this.pointer=null;this.dragging=false;this.plan=null;this.target=null;this.host.classList.remove('tasks-is-dragging');
  this.host.querySelectorAll('.tasks-drop-before,.tasks-drop-after,.tasks-drop-inside,.tasks-drop-blocked,.tasks-drag-source').forEach(el=>el.classList.remove('tasks-drop-before','tasks-drop-after','tasks-drop-inside','tasks-drop-blocked','tasks-drag-source'));
  const label=this.host.querySelector('[data-task-move-hint]');if(label)label.textContent='';
 }
 destroy(){this.finish();this.host.removeEventListener('click',this.click);this.host.removeEventListener('pointerdown',this.down);window.removeEventListener('pointermove',this.motion);window.removeEventListener('pointerup',this.up);window.removeEventListener('pointercancel',this.cancel);window.removeEventListener('keydown',this.key,true);window.removeEventListener('blur',this.cancel);}
}
