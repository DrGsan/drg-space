import {tText,tHtml,locale} from "../../core/i18n.js";
import {personName,personLabel} from '../../core/people.js';
import {mobileApp} from '../../core/mobile.js';
import {$,esc,today} from '../../core/ui.js';
import {matchesSource} from '../dashboard/widgets/task-source.js';
import {colors,listIcons,smartViews,taskIcon,taskFields,editableTask,patchFields,canManage,listColor,safeTaskUrl,matchesView,titleForView,taskMeta} from './format.js';
import './workspace.css';
import {ReminderDrag,siblingOrder,taskMoveRevision} from './move.js';
let active;
export const tasksController={destroy(){active?.destroy();active=null;},flush(){return active?.flush()??Promise.resolve(true);}};
export function mountTaskWorkspace(host,ctx){active?.destroy();active=new TaskWorkspace(host,ctx);active.mount();}
const listFields=['name','icon','color','retain_completed'];
const editableList=list=>Object.fromEntries(listFields.map(key=>[key,list[key]]));
const titleDay=day=>new Date(day+'T12:00:00').toLocaleDateString(locale(),{weekday:'long',day:'numeric',month:'long'});
// Presence is updated independently and is not displayed in this workspace.
// It must not rebuild reminder rows or an unchanged properties panel.
const reminderSnapshot=(tasks,lists,deletedLists,friends,day)=>JSON.stringify([tasks,lists,deletedLists,friends,day],(key,value)=>key==='online'||key==='last_seen'?undefined:value);
class TaskWorkspace{
 constructor(host,ctx){
  this.host=host;this.ctx=ctx;this.lists=[];this.deletedLists=[];this.friends=[];this.tasks=ctx.state.tasks||[];this.expanded=new Set();this.collapsed=new Set();this.query='';this.showCompleted=false;this.sideOpen=false;this.disposed=false;this.loadSequence=0;this.editSequence=0;this.listEditSequence=0;this.cleanups=[];
  this.view=ctx.state.tasksFocus|| (ctx.state.tasksFocusDay?{kind:'day',day:ctx.state.tasksFocusDay}:ctx.state.tasksFocusList?{kind:'list',id:ctx.state.tasksFocusList}:ctx.state.tasksFocusView?{kind:'smart',id:ctx.state.tasksFocusView}:(mobileApp()?{kind:'smart',id:'today'}:{kind:'list',id:null}));
  this.initialTask=ctx.state.tasksSelected;
  delete ctx.state.tasksFocus;delete ctx.state.tasksFocusDay;delete ctx.state.tasksFocusList;delete ctx.state.tasksFocusView;delete ctx.state.tasksSelected;
 }
 mount(){
  this.host.innerHTML=`<div class="tasks-shell"><aside class="tasks-sidebar" aria-label="${tHtml("tasks:reminderLists")}"><div class="tasks-nav-head"><button type="button" class="tasks-main-menu" data-tasks-main-menu id="tasks-main-menu" aria-label="${mobileApp()?tText("tasks:toTasks"):tText("tasks:mainMenu")}">${taskIcon('back')}<span>${mobileApp()?tText("tasks:tasks"):tText("tasks:mainMenu")}</span></button>${this.ctx.notesInboxButton||''}</div><label class="tasks-search">${taskIcon('search')}<input type="search" placeholder="${tHtml("tasks:findReminders")}" maxlength="200" aria-label="${tHtml("tasks:findReminders")}" data-tasks-search></label><nav class="tasks-smart-views" aria-label="${tHtml("tasks:smartLists")}"></nav><div class="tasks-sidebar-label"><span>${tHtml("tasks:myLists")}</span><button type="button" class="tasks-icon-button" data-create-list aria-label="${tHtml("tasks:createList")}" title="${tHtml("tasks:createList")}">${taskIcon('plus')}</button></div><nav class="tasks-user-lists" aria-label="${tHtml("tasks:myReminderLists")}"></nav><section class="tasks-tags-section"><h2>${tHtml("tasks:tags")}</h2><div data-tasks-tags></div></section><button type="button" class="tasks-new-list" data-create-list>${taskIcon('plus')}<span>${tHtml("tasks:addList")}</span></button></aside><div class="tasks-sidebar-backdrop" data-close-sidebar hidden></div><main class="tasks-center"><header class="tasks-toolbar"><button type="button" class="tasks-icon-button tasks-menu-toggle" data-toggle-tasks-sidebar aria-label="${tHtml("tasks:showReminderLists")}" aria-expanded="false">${taskIcon('menu')}</button><span data-task-save-status role="status" aria-live="polite"></span><div class="tasks-toolbar-actions"><button type="button" class="tasks-icon-button" data-refresh-tasks aria-label="${tHtml("tasks:refreshReminders")}" title="${tHtml("tasks:refreshReminders")}">${taskIcon('refresh')}</button><button type="button" class="tasks-icon-button" data-list-settings aria-label="${tHtml("tasks:listSettings")}" title="${tHtml("tasks:listSettings")}" hidden>${taskIcon('dots')}</button></div></header><div class="tasks-center-heading"><div><h1 data-task-view-title>${tHtml("tasks:reminders")}</h1><p data-task-view-description></p></div><strong data-task-view-count></strong></div><nav class="mobile-task-views" aria-label="${tHtml("tasks:tasksPeriod")}">${[['today',tText("tasks:today")],['scheduled',tText("tasks:upcoming")],['completed',tText("tasks:done")]].map(([id,label])=>`<button type="button" data-mobile-task-view="${id}">${label}</button>`).join('')}</nav><div class="tasks-list-options"><span data-task-move-hint role="status" aria-live="polite"></span><button type="button" class="tasks-text-button" data-show-completed>${tHtml("tasks:showCompleted")}</button><span data-task-view-help></span></div><div class="tasks-feedback" role="status" data-tasks-feedback hidden></div><div class="tasks-scroll-area" data-task-rows><p class="tasks-loading">${tHtml("tasks:loadingReminders")}</p></div><div class="tasks-add-area" data-tasks-add></div></main><aside class="tasks-properties" aria-label="${tHtml("tasks:reminderProperties")}" hidden></aside><div class="tasks-properties-backdrop" data-close-properties hidden></div></div>`;
  this.shell=$('.tasks-shell',this.host);this.center=$('.tasks-center',this.host);this.properties=$('.tasks-properties',this.host);
  $('[data-tasks-main-menu]',this.host).onclick=()=>mobileApp()?this.sidebar(false):this.ctx.navigate('home');
  $('[data-tasks-search]',this.host).oninput=e=>{if(this.query!==e.target.value)this.collapsed.clear();this.query=e.target.value;this.renderSidebar();this.renderCenter();};
  this.host.querySelectorAll('[data-create-list]').forEach(button=>button.onclick=()=>this.openCreateList());
  $('[data-toggle-tasks-sidebar]',this.host).onclick=()=>this.sidebar(!this.sideOpen);
  $('[data-close-sidebar]',this.host).onclick=()=>this.sidebar(false);
  $('[data-close-properties]',this.host).onclick=()=>this.closePanel();
  $('[data-refresh-tasks]',this.host).onclick=async()=>{if(await this.flush())await this.refresh().catch(e=>this.error(e));};
  $('[data-list-settings]',this.host).onclick=()=>this.openListSettings(this.currentList());
  this.host.querySelectorAll('[data-mobile-task-view]').forEach(button=>button.onclick=()=>this.changeView({kind:'smart',id:button.dataset.mobileTaskView}));
  $('[data-show-completed]',this.host).onclick=()=>{this.showCompleted=!this.showCompleted;this.renderCenter();};
  this.host.onclick=e=>{const tag=e.target.closest('[data-task-tag]');if(tag)this.changeView({kind:'tag',tag:tag.dataset.taskTag});};
  const key=e=>{if(e.key==='Escape'){if(this.sideOpen){this.sidebar(false);$('[data-toggle-tasks-sidebar]',this.host).focus();}else if(this.panel){e.preventDefault();this.closePanel();}}else if((e.metaKey||e.ctrlKey)&&e.key==='Enter'&&this.taskDraft){e.preventDefault();this.flush();}};
  this.host.addEventListener('keydown',key);this.cleanups.push(()=>this.host.removeEventListener('keydown',key));
  const visible=()=>{const editing=this.host.contains(document.activeElement)&&document.activeElement?.matches('input,textarea,select');if(!document.hidden&&!editing&&!this.dirty()&&!this.pendingSave&&!this.pendingListSave&&!this.actionPending&&!this.drag?.active&&this.panel?.kind!=='create-list')this.refresh({background:true}).catch(()=>{});};
  document.addEventListener('visibilitychange',visible);window.addEventListener('online',visible);this.cleanups.push(()=>document.removeEventListener('visibilitychange',visible),()=>window.removeEventListener('online',visible));
  document.addEventListener('drg-task-lists-change',visible);this.cleanups.push(()=>document.removeEventListener('drg-task-lists-change',visible));
  const deletionTimer=setInterval(()=>this.updateDeletionTimers(),1000);this.cleanups.push(()=>clearInterval(deletionTimer));
  this.poll=setInterval(visible,20000);this.cleanups.push(()=>clearInterval(this.poll));
  this.refresh({initial:true}).catch(e=>{this.error(e);$('[data-task-rows]',this.host).innerHTML=`<div class="tasks-empty"><h2>${tHtml("tasks:failedToLoadReminders")}</h2><p>${tHtml("tasks:clickRefreshRemindersAtTheTop")}</p></div>`;});
 }
 destroy(){this.drag?.destroy();this.disposed=true;clearTimeout(this.saveTimer);clearTimeout(this.listSaveTimer);this.cleanups.forEach(cleanup=>cleanup());this.ctx.state.dirty=false;}
 dirty(){return !!((this.taskDraft&&Object.keys(patchFields(this.taskDraft,this.taskBase,taskFields)).length)||(this.listDraft&&Object.keys(patchFields(this.listDraft,this.listBase,listFields)).length));}
 dirtyState(){this.ctx.state.dirty=this.dirty();}
 currentList(){return this.view.kind==='list'?this.lists.find(list=>list.id===this.view.id):null;}
 listForTask(task){return this.lists.find(list=>list.id===task.list_id);}
 async refresh({initial=false,preservePanel=false,background=false}={}){
  const sequence=++this.loadSequence;
  this.loads=(this.loads||0)+1;
  const [tasks,lists,friends]=await Promise.all([this.ctx.api('/tasks'),this.ctx.api('/task-lists'),this.ctx.api('/task-lists/friends')]).finally(()=>{this.loads--;});
  if(this.disposed||sequence!==this.loadSequence||!this.host.isConnected)return;
  // Compare the same active/trash partition on both sides. The server returns
  // both sets; comparing it with active lists alone causes a repaint every poll.
  const activeLists=lists.filter(list=>!list.deletion),deletedLists=lists.filter(list=>list.deletion);
  const unchanged=background&&reminderSnapshot(tasks,activeLists,deletedLists,friends,today())===reminderSnapshot(this.tasks,this.lists,this.deletedLists,this.friends,this.renderedDay);
  this.tasks=tasks;this.ctx.state.tasks=tasks;this.deletedLists=deletedLists;this.lists=activeLists;this.friends=friends;
  if(unchanged)return;
  if(this.view.kind==='list'&&!this.lists.some(list=>list.id===this.view.id))this.view={kind:'list',id:lists.find(list=>list.is_default)?.id||lists[0]?.id||null};
  if(this.taskBase&&!tasks.some(task=>task.id===this.taskBase.id)&&!this.dirty()){this.panel=null;this.taskBase=null;this.taskDraft=null;}
  this.renderSidebar();this.renderCenter();
  if(initial&&this.initialTask){const task=this.tasks.find(task=>task.id===this.initialTask);this.initialTask=null;if(task){if(this.view.kind==='list')this.view={kind:'list',id:task.list_id};this.selectTask(task.id,true);return;}}
  const editingProperty=this.properties.contains(document.activeElement)&&document.activeElement?.matches('input,textarea,select');
  if(!preservePanel&&!editingProperty&&this.panel?.kind!=='create-list'&&!this.dirty()&&!this.pendingSave&&!this.pendingListSave){if(this.panel?.kind==='task'){const task=tasks.find(t=>t.id===this.panel.id);if(task){this.taskBase=structuredClone(task);this.taskDraft=editableTask(task);}}else if(this.panel?.kind==='list'){const list=lists.find(l=>l.id===this.panel.id);if(list){this.listBase=structuredClone(list);this.listDraft=editableList(list);}}this.renderProperties();}
 }
 async flush(){
  clearTimeout(this.saveTimer);clearTimeout(this.listSaveTimer);
  if(this.actionPending){const ok=await this.actionPending;if(!ok)return false;}
  // Edits may arrive while an earlier request is pending. Keep navigation here
  // until the newest draft is saved too, using each returned server version.
  while(!this.disposed&&(this.pendingSave||this.pendingListSave||this.dirty())){
   if(this.pendingSave){if(!(await this.pendingSave))return false;continue;}
   if(this.pendingListSave){if(!(await this.pendingListSave))return false;continue;}
   if(this.taskDraft&&Object.keys(patchFields(this.taskDraft,this.taskBase,taskFields)).length&&!(await this.saveTask()))return false;
   if(this.listDraft&&Object.keys(patchFields(this.listDraft,this.listBase,listFields)).length&&!(await this.saveList()))return false;
  }
  return true;
 }
 status(message,error=false){if(this.disposed)return;const el=$('[data-task-save-status]',this.host);el.textContent=message;el.classList.toggle('is-error',error);const local=$('[data-property-save-status]',this.properties);if(local){local.textContent=message;local.classList.toggle('is-error',error);}}
 error(error){if(this.disposed)return;this.status(error.message||tText("tasks:failedToSaveChanges"),true);const feedback=$('[data-tasks-feedback]',this.host);feedback.hidden=false;feedback.innerHTML=`<span>${esc(error.message||tText("tasks:connectionError"))}</span>${error.status===409?`<button type="button" class="tasks-text-button" data-reload-conflict>${tHtml("tasks:downloadLatestVersion")}</button>`:''}`;if(error.status===409)$('[data-reload-conflict]',feedback).onclick=async()=>{if(!confirm(tText("tasks:downloadLatestDataUnsavedChangesInTheForm")))return;this.taskDraft=null;this.listDraft=null;this.taskBase=null;this.listBase=null;this.panel=null;this.dirtyState();feedback.hidden=true;await this.refresh().catch(e=>this.error(e));};}
 clearFeedback(){const el=$('[data-tasks-feedback]',this.host);if(el)el.hidden=true;}
 async act(fn,{skipFlush=false}={}){
  if(this.actionPending)return false;
  if(skipFlush){clearTimeout(this.saveTimer);clearTimeout(this.listSaveTimer);await this.pendingSave;await this.pendingListSave;}
  else if(!(await this.flush()))return false;
  this.loadSequence++;this.busy=true;this.shell.classList.add('is-saving');
  this.actionPending=(async()=>{try{await fn();return true;}catch(e){this.error(e);return false;}finally{this.busy=false;this.shell.classList.remove('is-saving');this.actionPending=null;}})();
  return this.actionPending;
 }
 async saveTask(){
  if(this.pendingSave)return this.pendingSave;
  const base=this.taskBase;if(!base||!this.taskDraft)return true;
  const patch=patchFields(this.taskDraft,base,taskFields);if(!Object.keys(patch).length)return true;
  const sequence=this.editSequence;this.status(tText("tasks:saving"));this.loadSequence++;
  this.pendingSave=(async()=>{
   try{
    const updated=await this.ctx.api('/tasks/'+encodeURIComponent(base.id),'PATCH',{...patch,version:base.version});
    if(this.disposed)return true;
    this.tasks=this.tasks.map(task=>task.id===updated.id?updated:task);this.ctx.state.tasks=this.tasks;
    this.taskBase=structuredClone(updated);
    if(sequence===this.editSequence)this.taskDraft=editableTask(updated);
    this.clearFeedback();this.status(tText("tasks:saved"));this.dirtyState();this.renderSidebar();this.renderCenter();
    if('list_id' in patch||'parent_id' in patch)await this.refresh({preservePanel:true});
    return true;
   }catch(e){this.error(e);this.dirtyState();return false;}
   finally{this.pendingSave=null;}
  })();
  const ok=await this.pendingSave;if(ok&&this.taskDraft&&Object.keys(patchFields(this.taskDraft,this.taskBase,taskFields)).length)return this.saveTask();return ok;
 }
 async saveList(){
  if(this.pendingListSave)return this.pendingListSave;
  const base=this.listBase;if(!base||!this.listDraft)return true;
  const patch=patchFields(this.listDraft,base,listFields);if(!Object.keys(patch).length)return true;
  const sequence=this.listEditSequence;this.status(tText("tasks:saving"));this.loadSequence++;
  this.pendingListSave=(async()=>{try{
   const updated=await this.ctx.api('/task-lists/'+encodeURIComponent(base.id),'PATCH',{...patch,version:base.version});if(this.disposed)return true;
   this.lists=this.lists.map(list=>list.id===updated.id?updated:list);this.listBase=structuredClone(updated);if(sequence===this.listEditSequence)this.listDraft=editableList(updated);
   this.clearFeedback();this.status(tText("tasks:saved"));this.dirtyState();this.renderSidebar();this.renderCenter();return true;
  }catch(e){this.error(e);this.dirtyState();return false;}finally{this.pendingListSave=null;}})();
  const ok=await this.pendingListSave;if(ok&&this.listDraft&&Object.keys(patchFields(this.listDraft,this.listBase,listFields)).length)return this.saveList();return ok;
 }
 sidebar(open){this.sideOpen=open;this.shell.classList.toggle('sidebar-open',open);$('[data-close-sidebar]',this.host).hidden=!open;$('[data-toggle-tasks-sidebar]',this.host).setAttribute('aria-expanded',String(open));}
 async changeView(view){if(!(await this.flush()))return;this.view=view;this.query='';this.collapsed.clear();$('[data-tasks-search]',this.host).value='';this.showCompleted=false;this.addParent=null;this.clearPanel();this.sidebar(false);this.renderSidebar();this.renderCenter();}
 updateDeletionTimers(){this.host.querySelectorAll('[data-list-countdown]').forEach(el=>{const seconds=Math.max(0,Math.ceil(Number(el.dataset.listCountdown)-Date.now()/1000));el.textContent=tText("tasks:timeToRestore",{value0:String(Math.floor(seconds/3600)).padStart(2,'0')+':'+String(Math.floor(seconds%3600/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0')});el.closest('.tasks-deleted-list').querySelector('button').disabled=seconds===0;});}
 renderSidebar(){
  const count=view=>this.tasks.filter(task=>matchesView(task,view)).length;
  $('.tasks-smart-views',this.host).innerHTML=smartViews.map(view=>`<button type="button" class="tasks-smart-view ${this.view.kind==='smart'&&this.view.id===view.id?'is-active':''}" data-smart-view="${view.id}" aria-pressed="${this.view.kind==='smart'&&this.view.id===view.id}" style="--tasks-list-color:${colors[view.color]}"><span class="tasks-smart-icon">${taskIcon(view.icon)}</span><strong>${count({kind:'smart',id:view.id})}</strong><span>${view.title}</span></button>`).join('');
  this.host.querySelectorAll('[data-smart-view]').forEach(button=>button.onclick=()=>this.changeView({kind:'smart',id:button.dataset.smartView}));
  $('.tasks-user-lists',this.host).innerHTML=this.lists.map(list=>`<button type="button" class="tasks-list-button ${this.view.kind==='list'&&this.view.id===list.id?'is-active':''}" data-task-list="${esc(list.id)}" aria-pressed="${this.view.kind==='list'&&this.view.id===list.id}" title="${esc(list.name)}" style="--tasks-list-color:${listColor(list)}"><span class="tasks-list-symbol">${taskIcon(list.icon)}</span><span class="tasks-list-name">${esc(list.name)}</span>${list.shared?`<span class="tasks-shared-symbol" title="${tHtml("tasks:sharedList")}">${taskIcon('users')}</span>`:''}<small>${this.tasks.filter(task=>task.list_id===list.id&&!task.done).length}</small></button>`).join('')||`<p class="tasks-small-note">${tHtml("tasks:noListsYet")}</p>`;
  this.host.querySelectorAll('[data-task-list]').forEach(button=>button.onclick=()=>this.changeView({kind:'list',id:button.dataset.taskList}));
  const listHost=$('.tasks-user-lists',this.host);
  if(this.deletedLists.length){listHost.insertAdjacentHTML('beforeend',`<section class="tasks-deleted-lists"><h3>${tHtml("tasks:recentlyDeletedLists")}</h3>${this.deletedLists.map(list=>`<div class="tasks-deleted-list"><strong>${esc(list.name)}</strong><span>${tHtml("tasks:deletionDeadline",{value0:esc(new Date(list.deletion.delete_at*1000).toLocaleString(locale(),{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}))})}</span><span data-list-countdown="${list.deletion.delete_at}"></span><button type="button" class="tasks-text-button" data-restore-list="${esc(list.id)}">${tHtml("tasks:restoreList")}</button></div>`).join('')}</section>`);
  this.host.querySelectorAll('[data-restore-list]').forEach(button=>button.onclick=()=>this.act(async()=>{const list=await this.ctx.api('/task-lists/'+encodeURIComponent(button.dataset.restoreList)+'/restore','POST',{});this.view={kind:'list',id:list.id};await this.refresh();}));}
  this.updateDeletionTimers();
  const tags=[...new Set(this.tasks.flatMap(task=>task.tags||[]))].sort((a,b)=>a.localeCompare(b,locale()));
  $('[data-tasks-tags]',this.host).innerHTML=tags.map(tag=>`<button type="button" class="tasks-tag ${this.view.kind==='tag'&&this.view.tag===tag?'is-active':''}" data-task-tag="${esc(tag)}">#${esc(tag)}</button>`).join('')||`<p class="tasks-small-note">${tHtml("tasks:addATagInTheReminderProperties")}</p>`;
 }
 descendants(id){const found=new Set(),visit=parent=>{for(const task of this.tasks)if(task.parent_id===parent&&!found.has(task.id)){found.add(task.id);visit(task.id);}};visit(id);return [...found];}
 filtered(){
  const query=this.query.trim().toLocaleLowerCase(locale());
  if(this.view.scope?.view==='list'&&!this.lists.some(item=>item.id===this.view.scope.list_id))return {matching:[],selected:[],roots:[],byId:new Map(),included:new Set(),autoExpanded:new Set()};
  const matching=this.tasks.filter(task=>(!this.view.scope||matchesSource(task,this.view.scope,today()))&&(query?`${task.title} ${task.notes||''} ${(task.tags||[]).join(' ')}`.toLocaleLowerCase(locale()).includes(query):matchesView(task,this.view,this.showCompleted)));
  const included=new Set(matching.map(task=>task.id)),ancestors=new Set(),byId=new Map(this.tasks.map(task=>[task.id,task]));
  for(const task of matching){let parent=byId.get(task.parent_id),seen=new Set();while(parent&&!seen.has(parent.id)){seen.add(parent.id);included.add(parent.id);ancestors.add(parent.id);parent=byId.get(parent.parent_id);}if(!query)for(const id of this.descendants(task.id)){const child=byId.get(id);if((!this.view.scope||matchesSource(child,this.view.scope,today()))&&(!child.done||this.showCompleted||this.view.id==='completed'))included.add(id);}}
  // An explicit expansion in search reveals the parent's other children as
  // context, even when their titles do not match the query themselves.
  if(query)for(const id of this.expanded)if(included.has(id)&&!this.collapsed.has(id))for(const childId of this.descendants(id)){
   const child=byId.get(childId);if((!this.view.scope||matchesSource(child,this.view.scope,today()))&&(!child.done||this.showCompleted||this.view.id==='completed'))included.add(childId);
  }
  const selected=this.tasks.filter(task=>included.has(task.id)),compare=this.view.kind==='list'&&!query?siblingOrder:(a,b)=>Number(a.done)-Number(b.done)||(a.day||'9999').localeCompare(b.day||'9999')||(a.created||0)-(b.created||0)||a.id.localeCompare(b.id);
  const filteredView=!!query||this.view.kind==='day'||this.view.kind==='tag'||this.view.kind==='smart'&&this.view.id!=='all';
  return {matching,selected: selected.sort(compare),roots:selected.filter(task=>!included.has(task.parent_id)).sort(compare),byId,included,autoExpanded:filteredView?ancestors:new Set()};
 }
 renderCenter(){
  if(this.disposed)return;
  this.drag?.destroy();
  this.renderedDay=today();
  const childInput=document.activeElement?.closest('[data-add-child]')?.querySelector('input'),childFocus=childInput&&this.center.contains(childInput)?{parent:childInput.closest('[data-add-child]').dataset.addChild,value:childInput.value,start:childInput.selectionStart,end:childInput.selectionEnd}:null;
  const list=this.currentList(),title=this.query.trim()?tText("tasks:searchResults"):titleForView(this.view,this.lists),data=this.filtered();
  $('[data-task-view-title]',this.host).textContent=title;$('[data-task-view-title]',this.host).style.color=listColor(list);
  $('[data-task-view-count]',this.host).textContent=data.matching.length;
  $('[data-task-view-description]',this.host).textContent=this.query.trim()?tText("tasks:forQuery", {value0:(this.query.trim())}):list?.shared?tText("tasks:sharedList"):this.view.kind==='day'?titleDay(this.view.day):this.view.kind==='smart'&&this.view.id==='today'?tText("tasks:todayAndOverdue"):this.view.kind==='smart'&&this.view.id==='scheduled'?tText("tasks:remindersWithDate"):this.view.kind==='smart'&&this.view.id==='completed'?tText("tasks:completedReminders"):tText("tasks:oneByOne");
  if(this.view.scope){
   const label=this.view.scope.view==='list'?this.lists.find(item=>item.id===this.view.scope.list_id)?.name||tText("tasks:listUnavailable"):this.view.scope.view==='today'?tText("tasks:sourceToday"):this.view.scope.view==='flagged'?tText("tasks:sourceWithFlag"):tText("tasks:allLists");
   $('[data-task-view-description]',this.host).textContent=label+' · '+$('[data-task-view-description]',this.host).textContent;
  }
  this.host.querySelectorAll('[data-mobile-task-view]').forEach(button=>button.setAttribute('aria-pressed',String(this.view.kind==='smart'&&this.view.id===button.dataset.mobileTaskView)));
  const manage=$('[data-list-settings]',this.host);manage.hidden=!list;manage.setAttribute('aria-label',list&&canManage(list,this.ctx.state.me)?tText("tasks:listSettings"):tText("tasks:listParticipants"));
  const completed=$('[data-show-completed]',this.host);completed.hidden=this.view.id==='completed'||!!this.query.trim();completed.textContent=this.showCompleted?tText("tasks:hideCompleted"):tText("tasks:showCompleted");completed.setAttribute('aria-pressed',String(this.showCompleted));
  $('[data-task-view-help]',this.host).textContent=list?list.retain_completed?tText("tasks:completedIsSaved"):tText("tasks:completedIsDeletedTheNextDay"):'';
  const row=(task,depth=0,seen=new Set())=>{
   if(seen.has(task.id))return '';const nextSeen=new Set([...seen,task.id]),children=data.selected.filter(child=>child.parent_id===task.id),allChildren=this.tasks.filter(child=>child.parent_id===task.id),expanded=this.addParent===task.id||!this.collapsed.has(task.id)&&(this.expanded.has(task.id)||data.autoExpanded.has(task.id));
   const matching=data.matching.some(item=>item.id===task.id),priority=Number(task.priority)||0;
   return `<li class="tasks-tree-item" data-task-item="${esc(task.id)}"><div class="tasks-reminder-row ${task.done?'is-done':''} ${this.panel?.kind==='task'&&this.panel.id===task.id?'is-selected':''} ${matching?'':'is-context'}" style="--tasks-row-depth:${Math.min(depth,5)}"><button type="button" class="tasks-drag-handle" data-move-task="${esc(task.id)}" aria-label="${tHtml("tasks:moveReminder",{title:esc(task.title)})}" title="${tHtml("tasks:moveReminder",{title:esc(task.title)})}">${taskIcon('grip')}</button><span class="tasks-expand-slot">${allChildren.length?`<button type="button" class="tasks-expand ${expanded?'is-expanded':''}" data-expand-task="${esc(task.id)}" aria-expanded="${expanded}" aria-label="${tHtml("tasks:nestedReminders", {value0:(expanded?tText("tasks:collapse"):tText("tasks:expand")), value1:(esc(task.title))})}">${taskIcon('chevron')}</button>`:''}</span><input class="tasks-complete" type="checkbox" data-task-done="${esc(task.id)}" aria-label="${tHtml("tasks:completed2", {value0:(esc(task.title))})}" title="${allChildren.length?tText("tasks:completingThisReminderWillCompleteAllNestedReminders"):tText("tasks:markAsComplete")}" ${task.done?'checked':''}><div class="tasks-row-content"><button type="button" class="tasks-row-title" data-select-task="${esc(task.id)}" ${allChildren.length?`data-toggle-task-children="${esc(task.id)}" aria-expanded="${expanded}" aria-label="${tHtml("tasks:nestedReminders",{value0:expanded?tText("tasks:collapse"):tText("tasks:expand"),value1:esc(task.title)})}"`:`aria-pressed="${this.panel?.kind==='task'&&this.panel.id===task.id}"`}>${priority?`<span class="tasks-priority" title="${tHtml("tasks:priority", {value0:(['',tText("tasks:low"),tText("tasks:medium"),tText("tasks:high")][priority])})}">${'!'.repeat(priority)}</span>`:''}<span>${esc(task.title)}</span>${allChildren.length?`<small>${allChildren.filter(child=>child.done).length}/${allChildren.length}</small>`:''}</button>${taskMeta(task,this.listForTask(task),this.view.kind!=='list'||!!this.query.trim())}</div><button type="button" class="tasks-row-flag ${task.flagged?'is-flagged':''}" data-toggle-flag="${esc(task.id)}" aria-label="${tHtml("tasks:flag", {value0:(task.flagged?tText("tasks:remove"):tText("tasks:set")), value1:(esc(task.title))})}" aria-pressed="${!!task.flagged}" title="${tHtml("tasks:checkbox2")}">${taskIcon('flag')}</button><button type="button" class="tasks-row-info" data-select-task="${esc(task.id)}" aria-label="${tHtml("tasks:reminderProperties2", {value0:(esc(task.title))})}" title="${tHtml("tasks:reminderProperties")}">${taskIcon('info')}</button></div>${expanded?`<ul class="tasks-child-list">${children.map(child=>row(child,depth+1,nextSeen)).join('')}${this.addParent===task.id?`<li><form class="tasks-child-add" data-add-child="${esc(task.id)}" style="--tasks-row-depth:${Math.min(depth+1,5)}"><input type="text" maxlength="300" required aria-label="${tHtml("tasks:nameOfNestedReminder")}" placeholder="${tHtml("tasks:newNestedReminder")}"><button type="submit" class="tasks-icon-button" aria-label="${tHtml("tasks:addNestedReminder")}">${taskIcon('plus')}</button></form></li>`:''}</ul>`:''}</li>`;
  };
  let groups=[];
  if(this.view.kind==='smart'&&this.view.id==='all'||this.query.trim())groups=this.lists.map(item=>({title:item.name,rows:data.roots.filter(task=>task.list_id===item.id)})).filter(group=>group.rows.length);
  else if(this.view.kind==='smart'&&this.view.id==='scheduled'){
   const days=[...new Set(data.roots.map(task=>task.day||data.selected.find(child=>child.parent_id===task.id)?.day||''))].sort();groups=days.map(day=>({title:day?titleDay(day):tText("tasks:noDate"),rows:data.roots.filter(task=>(task.day||data.selected.find(child=>child.parent_id===task.id)?.day||'')===day)}));
  }else groups=[{title:'',rows:data.roots}];
  $('[data-task-rows]',this.host).innerHTML=data.roots.length?groups.map(group=>`<section class="tasks-row-group">${group.title?`<h2>${esc(group.title)}</h2>`:''}<ul class="tasks-reminder-list">${group.rows.map(task=>row(task)).join('')}</ul></section>`).join(''):`<div class="tasks-empty">${taskIcon(this.view.id==='completed'?'check':'list')}<h2>${this.query.trim()?tText("tasks:noResults"):this.view.id==='completed'?tText("tasks:noCompletedReminders"):this.view.id==='today'?tText("tasks:allForToday"):this.view.id==='flagged'?tText("tasks:noRemindersWithFlag"):tText("tasks:listIsCurrentlyEmpty")}</h2><p>${this.query.trim()?tText("tasks:tryADifferentNameNoteOrTag"):this.view.id==='completed'?tText("tasks:completedRemindersWillAppearHere"):tText("tasks:addAReminderBelowTheDateCanBe")}</p></div>`;
  this.host.querySelectorAll('[data-select-task]').forEach(button=>button.onclick=()=>button.hasAttribute('data-toggle-task-children')?this.toggleChildren(button.dataset.selectTask,button):this.selectTask(button.dataset.selectTask));
  this.host.querySelectorAll('[data-expand-task]').forEach(button=>button.onclick=()=>this.toggleChildren(button.dataset.expandTask,button));
  this.host.querySelectorAll('[data-task-done]').forEach(input=>input.onchange=()=>this.toggleTask(input));
  this.host.querySelectorAll('[data-toggle-flag]').forEach(button=>button.onclick=()=>{const task=this.tasks.find(item=>item.id===button.dataset.toggleFlag);this.mutateTask(task,{flagged:!task.flagged});});
  this.host.querySelectorAll('[data-add-child]').forEach(form=>form.onsubmit=async e=>{e.preventDefault();const input=$('input',form),parent=this.tasks.find(task=>task.id===form.dataset.addChild);if(!input.value.trim())return;await this.act(async()=>{const created=await this.ctx.api('/tasks','POST',{title:input.value.trim(),list_id:parent.list_id,parent_id:parent.id,day:''});this.addParent=null;this.expanded.add(parent.id);this.collapsed.delete(parent.id);await this.refresh({preservePanel:true});this.selectTask(created.id,true);});});
  this.drag=new ReminderDrag(this.shell,{tasks:this.tasks,enabled:this.view.kind==='list'&&!this.query.trim(),blocked:()=>this.busy||this.loads||this.dirty()||this.pendingSave||this.pendingListSave,move:(id,plan,snapshot)=>this.moveTask(id,plan,snapshot),open:id=>this.openMove(id)});
  this.renderAdd();
  if(childFocus){const input=this.host.querySelector(`[data-add-child="${CSS.escape(childFocus.parent)}"] input`);if(input){input.value=childFocus.value;input.focus({preventScroll:true});input.setSelectionRange(childFocus.start,childFocus.end);}}
 }
 toggleChildren(id,button){
  if(button.getAttribute('aria-expanded')==='true'){this.expanded.delete(id);this.collapsed.add(id);}else{this.expanded.add(id);this.collapsed.delete(id);}
  const attribute=button.hasAttribute('data-toggle-task-children')?'data-toggle-task-children':'data-expand-task';
  this.renderCenter();this.host.querySelector(`[${attribute}="${CSS.escape(id)}"]`)?.focus({preventScroll:true});
 }
 async openMove(id){
  if(!(await this.flush()))return;const task=this.tasks.find(t=>t.id===id);if(!task)return;
  this.clearPanel();this.panel={kind:'move',id};this.moveDraft={parent_id:task.parent_id||null,before_id:null};this.renderProperties();$('[data-move-parent]',this.properties)?.focus();
 }
 renderMoveProperties(header){
  const task=this.tasks.find(t=>t.id===this.panel.id);if(!task){this.clearPanel();return;}
  const children=this.descendants(task.id),parents=children.length?[]:this.tasks.filter(t=>t.list_id===task.list_id&&!t.parent_id&&t.id!==task.id);
  if(task.parent_id&&!parents.some(p=>p.id===task.parent_id)){const existing=this.tasks.find(t=>t.id===task.parent_id);if(existing)parents.push(existing);}
  const siblings=this.tasks.filter(t=>t.list_id===task.list_id&&(t.parent_id||null)===this.moveDraft.parent_id&&t.id!==task.id).sort(siblingOrder);
  this.properties.innerHTML=header(tHtml('tasks:move'))+`<form class="tasks-task-properties" data-move-form><strong>${esc(task.title)}</strong><label>${tHtml('tasks:moveLevel')}<select data-move-parent><option value="">${tHtml('tasks:moveTopLevel')}</option>${parents.map(p=>`<option value="${esc(p.id)}" ${p.id===this.moveDraft.parent_id?'selected':''}>${esc(p.title)}</option>`).join('')}</select></label><label>${tHtml('tasks:movePosition')}<select data-move-before><option value="">${tHtml('tasks:moveToEnd')}</option>${siblings.map(p=>`<option value="${esc(p.id)}" ${p.id===this.moveDraft.before_id?'selected':''}>${tHtml('tasks:moveBefore',{title:esc(p.title)})}</option>`).join('')}</select></label><p class="hint">${tHtml(children.length?'tasks:moveWithChildren':'tasks:moveHint')}</p><button type="submit" class="primary">${tHtml('tasks:move')}</button></form>`;
  $('[data-move-parent]',this.properties).onchange=e=>{this.moveDraft={parent_id:e.target.value||null,before_id:null};this.renderProperties();};
  $('[data-move-before]',this.properties).onchange=e=>this.moveDraft.before_id=e.target.value||null;
  $('[data-move-form]',this.properties).onsubmit=async e=>{e.preventDefault();if(await this.moveTask(task.id,this.moveDraft,this.tasks.slice())){this.clearPanel();this.renderProperties();}};
 }
 async moveTask(id,plan,snapshot){
  const task=snapshot.find(t=>t.id===id);if(!task)return false;
  return this.act(async()=>{
   const revision=await taskMoveRevision(snapshot,task.list_id);
   await this.ctx.api('/tasks/'+encodeURIComponent(id)+'/move','POST',{revision,parent_id:plan.parent_id,before_id:plan.before_id});
   if(plan.parent_id){this.expanded.add(plan.parent_id);this.collapsed.delete(plan.parent_id);}
   this.clearFeedback();await this.refresh({preservePanel:true});this.status(tText('tasks:moved'));
  });
 }
 renderAdd(){
  const host=$('[data-tasks-add]',this.host),list=this.currentList()||(this.view.scope?.view==='list'?this.lists.find(item=>item.id===this.view.scope.list_id):null),defaultId=this.lists.find(item=>item.is_default)?.id||this.lists[0]?.id;
  if(this.view.scope?.view==='list'&&!this.lists.some(item=>item.id===this.view.scope.list_id)){host.innerHTML='';return;}
  const selected=list?.id||(this.view.scope?.view==='list'?this.view.scope.list_id:null)||this.addListId||defaultId;
  const active=document.activeElement,wasFocused=host.contains(active),selection=active?.matches('[data-add-task-title]')?[active.selectionStart,active.selectionEnd]:null;
  const previous=$('[data-add-task-title]',host)?.value||this.addDraft||'';
  host.innerHTML=`<form class="tasks-quick-add" data-quick-task><span>${taskIcon('plus')}</span><input data-add-task-title name="title" type="text" maxlength="300" required placeholder="${tHtml("tasks:newReminder")}" aria-label="${tHtml("tasks:reminderName")}" value="${esc(previous)}">${!list&&this.lists.length>1?`<select name="list_id" aria-label="${tHtml("tasks:listOfNewReminder")}">${this.lists.map(item=>`<option value="${esc(item.id)}" ${item.id===selected?'selected':''}>${esc(item.name)}</option>`).join('')}</select>`:''}<button type="submit" class="tasks-add-submit" aria-label="${tHtml("tasks:addReminder")}" title="${tHtml("tasks:addReminder")}">${taskIcon('plus')}</button></form>`;
  const form=$('[data-quick-task]',host),input=$('[data-add-task-title]',host);
  input.oninput=()=>{this.addDraft=input.value;};
  $('select',form)?.addEventListener('change',e=>{this.addListId=e.target.value;});
  form.onsubmit=async e=>{e.preventDefault();if(!input.value.trim())return;await this.act(async()=>{
   const day=this.view.kind==='day'?this.view.day:((this.view.kind==='smart'&&this.view.id==='today')||this.view.scope?.view==='today'||this.view.scope&&this.view.id==='scheduled')?today():'';
   const result=await this.ctx.api('/tasks','POST',{title:input.value.trim(),list_id:$('select',form)?.value||selected,day,...(this.view.kind==='tag'?{tags:[this.view.tag]}:{}),...(this.view.scope&&(this.view.scope.view==='flagged'||this.view.id==='flagged')?{flagged:true}:{}),...(this.view.scope&&this.view.id==='important'?{priority:3}:{})});
   this.addDraft='';input.value='';if(this.view.id==='completed')this.view={kind:'list',id:result.list_id};
   await this.refresh({preservePanel:true});this.selectTask(result.id,true);$('[data-add-task-title]',this.host)?.focus();
  });};
  if(wasFocused){const target=selection?input:$('select',form);target?.focus({preventScroll:true});if(selection)input.setSelectionRange(...selection);}
 }
 async toggleTask(input){const task=this.tasks.find(item=>item.id===input.dataset.taskDone),done=input.checked;input.disabled=true;const ok=await this.mutateTask(task,{done});if(!ok&&input.isConnected){input.checked=!done;input.disabled=false;}}
 async mutateTask(task,patch){return this.act(async()=>{
  const latest=this.tasks.find(item=>item.id===task.id);if(!latest)throw new Error(tText("tasks:reminderIsNoLongerAvailableRefreshTheList"));
  const updated=await this.ctx.api('/tasks/'+encodeURIComponent(task.id),'PATCH',{...patch,version:latest.version});
  if(this.taskBase?.id===task.id){this.taskBase=structuredClone(updated);this.taskDraft=editableTask(updated);}
  await this.refresh();this.status(tText("tasks:saved"));
 });}
 clearPanel(){this.panel=null;this.taskBase=null;this.taskDraft=null;this.listBase=null;this.listDraft=null;this.deleteConfirm=false;this.dirtyState();this.renderProperties();}
 async closePanel(){if(!(await this.flush()))return;const focus=this.panel?.kind==='move'?this.panel.id:null;this.clearPanel();this.renderCenter();if(focus)this.host.querySelector(`[data-move-task="${CSS.escape(focus)}"]`)?.focus({preventScroll:true});}
 async selectTask(id,skipFlush=false){
  if(!skipFlush&&!(await this.flush()))return;const task=this.tasks.find(item=>item.id===id);if(!task)return;
  this.clearPanel();this.panel={kind:'task',id};this.taskBase=structuredClone(task);this.taskDraft=editableTask(task);this.sidebar(false);this.renderCenter();this.renderProperties();
 }
 async openCreateList(){if(!(await this.flush()))return;this.clearPanel();this.panel={kind:'create-list'};this.sidebar(false);this.renderProperties();$('[name=name]',this.properties)?.focus();}
 async openListSettings(list){if(!list||!(await this.flush()))return;this.clearPanel();this.panel={kind:'list',id:list.id};this.listBase=structuredClone(list);this.listDraft=editableList(list);this.sidebar(false);this.renderProperties();}
 renderProperties(){
  if(this.disposed)return;
  const panel=this.panel;this.properties.hidden=!panel;this.shell.classList.toggle('has-properties',!!panel);$('[data-close-properties]',this.host).hidden=!panel;
  if(!panel){this.properties.innerHTML='';return;}
  const header=title=>`<header class="tasks-properties-head"><h2>${title}</h2><button type="button" class="tasks-icon-button" data-close-task-properties aria-label="${tHtml("tasks:closeProperties")}">${taskIcon('close')}</button></header>`;
  if(panel.kind==='move')this.renderMoveProperties(header);
  else if(panel.kind==='task')this.renderTaskProperties(header);
  else if(panel.kind==='create-list')this.renderCreateProperties(header);
  else this.renderListProperties(header);
  $('[data-close-task-properties]',this.properties).onclick=()=>this.closePanel();
 }
 renderTaskProperties(header){
  const task=this.taskBase,draft=this.taskDraft;if(!task||!draft)return;const descendants=this.descendants(task.id),parent=task.parent_id?this.tasks.find(item=>item.id===task.parent_id):null,link=safeTaskUrl(draft.url);
  this.properties.innerHTML=`${header(tText("tasks:details"))}<form class="tasks-task-properties" data-task-properties><label class="tasks-property-title">${tHtml("tasks:name")}<input name="title" aria-label="${tHtml("tasks:nameOfSelectedReminder")}" maxlength="300" required value="${esc(draft.title)}"></label>${parent?`<button type="button" class="tasks-parent-link" data-open-parent>${tHtml("tasks:parentReminder2", {value0:(esc(parent.title))})}</button>`:''}<label>${tHtml("tasks:note")}<textarea name="notes" maxlength="5000" rows="4" aria-label="${tHtml("tasks:reminderNote")}" placeholder="${tHtml("tasks:addDetails")}">${esc(draft.notes)}</textarea></label><label>URL<input name="url" type="url" maxlength="2000" aria-label="${tHtml("tasks:reminderUrl")}" placeholder="https://" value="${esc(draft.url)}"></label>${link?`<a class="tasks-property-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${tHtml("tasks:openLink")}</a>`:''}<div class="tasks-property-section"><label class="tasks-check-label"><input type="checkbox" data-enable-task-day ${draft.day?'checked':''}>${tHtml("tasks:onASpecificDay")}</label><input name="day" type="date" aria-label="${tHtml("tasks:reminderDate")}" value="${esc(draft.day)}" ${draft.day?'':'hidden disabled'}><label class="tasks-check-label"><input type="checkbox" data-enable-task-time ${draft.time?'checked':''} ${draft.day?'':'disabled'}>${tHtml("tasks:atASpecificTime")}</label><input name="time" type="time" aria-label="${tHtml("tasks:reminderTime")}" value="${esc(draft.time)}" ${draft.time&&draft.day?'':'hidden disabled'}></div><div class="tasks-property-section"><label class="tasks-check-label"><input type="checkbox" name="flagged" ${draft.flagged?'checked':''}>${tHtml("tasks:checkbox2")}</label><label>${tHtml("tasks:priority2")}<select name="priority" aria-label="${tHtml("tasks:reminderPriority")}">${[tText("tasks:no"),tText("tasks:low"),tText("tasks:medium"),tText("tasks:high")].map((label,value)=>`<option value="${value}" ${Number(draft.priority)===value?'selected':''}>${label}</option>`).join('')}</select></label></div><label>${tHtml("tasks:tags")}<input name="tags" maxlength="1000" aria-label="${tHtml("tasks:reminderTags")}" placeholder="${tHtml("tasks:homeImportant")}" value="${esc((draft.tags||[]).map(tag=>'#'+tag).join(', '))}"></label><p class="tasks-small-note">${tHtml("tasks:separateTagsWithCommasUpTo20Tags")}</p><label>${tHtml("tasks:list")}<select name="list_id" aria-label="${tHtml("tasks:reminderList")}">${this.lists.map(list=>`<option value="${esc(list.id)}" ${list.id===draft.list_id?'selected':''}>${esc(list.name)}</option>`).join('')}</select></label><label>${tHtml("tasks:parentReminder")}<select name="parent_id" aria-label="${tHtml("tasks:parentReminder")}"><option value="">${tHtml("tasks:noParent")}</option>${this.tasks.filter(item=>item.list_id===draft.list_id&&!item.parent_id&&item.id!==task.id&&!descendants.includes(item.id)&&!this.tasks.some(child=>child.parent_id===task.id)).map(item=>`<option value="${esc(item.id)}" ${item.id===draft.parent_id?'selected':''}>${esc(item.title)}</option>`).join('')}</select></label><div class="tasks-property-status" data-property-save-status role="status">${tHtml("tasks:saved")}</div><div class="tasks-property-actions"><button type="button" class="tasks-text-button" data-save-task-properties>${tHtml("tasks:saveChanges")}</button><button type="button" class="tasks-text-button" data-reset-task-draft>${tHtml("tasks:cancelUnsaved")}</button><button type="button" class="tasks-text-button" data-add-subtask ${task.parent_id?'hidden':''}>${tHtml("tasks:nestedReminder", {value0:(taskIcon('plus'))})}</button><button type="button" class="tasks-text-button is-danger" data-delete-task>${tHtml("tasks:deleteReminder")}</button></div>${this.deleteConfirm?`<div class="tasks-inline-confirm"><p>${tHtml("tasks:deleteReminderAndAllNestedRemindersThisAction", {value0:(descendants.length)})}</p><button type="button" class="tasks-danger-button" data-confirm-delete-task>${tHtml("tasks:deleteAll")}</button><button type="button" class="tasks-text-button" data-cancel-delete>${tHtml("tasks:cancel")}</button></div>`:''}</form>`;
  const form=$('[data-task-properties]',this.properties);
  const input=e=>{
   const name=e.target.name;if(!name)return;
   if(name==='tags'){this.taskDraft.tags=[...new Set(e.target.value.split(',').map(tag=>tag.trim().replace(/^#+/,'')).filter(Boolean))];}
   else if(name==='flagged')this.taskDraft.flagged=e.target.checked;
   else if(name==='priority')this.taskDraft.priority=Number(e.target.value);
   else if(name==='parent_id')this.taskDraft.parent_id=e.target.value||null;
   else this.taskDraft[name]=e.target.value;
   if(name==='day'){$('[data-enable-task-time]',form).disabled=!e.target.value;if(!e.target.value){this.taskDraft.time='';$('input[name=time]',form).value='';$('input[name=time]',form).hidden=true;$('input[name=time]',form).disabled=true;$('[data-enable-task-time]',form).checked=false;}}
   if(name==='list_id'){this.taskDraft.parent_id=null;$('[name=parent_id]',form).innerHTML=`<option value="">${tHtml("tasks:noParent")}</option>${this.tasks.filter(item=>item.list_id===this.taskDraft.list_id&&!item.parent_id&&item.id!==task.id&&!descendants.includes(item.id)&&!this.tasks.some(child=>child.parent_id===task.id)).map(item=>`<option value="${esc(item.id)}">${esc(item.title)}</option>`).join('')}`;}
   this.changedTask();
  };
  form.oninput=input;form.onchange=input;form.onsubmit=e=>{e.preventDefault();this.flush();};
  $('[data-enable-task-day]',form).onchange=e=>{const date=$('[name=day]',form),timeToggle=$('[data-enable-task-time]',form);date.hidden=!e.target.checked;date.disabled=!e.target.checked;this.taskDraft.day=e.target.checked?date.value||today():'';date.value=this.taskDraft.day;timeToggle.disabled=!e.target.checked;if(!e.target.checked){this.taskDraft.time='';timeToggle.checked=false;$('[name=time]',form).value='';$('[name=time]',form).hidden=true;$('[name=time]',form).disabled=true;}this.changedTask();if(e.target.checked)date.focus();};
  $('[data-enable-task-time]',form).onchange=e=>{const time=$('[name=time]',form);time.hidden=!e.target.checked;time.disabled=!e.target.checked;this.taskDraft.time=e.target.checked?time.value||'09:00':'';time.value=this.taskDraft.time;this.changedTask();if(e.target.checked)time.focus();};
  $('[data-save-task-properties]',form).onclick=()=>this.flush();
  $('[data-reset-task-draft]',form).onclick=async()=>{clearTimeout(this.saveTimer);if(this.pendingSave)await this.pendingSave;this.taskDraft=editableTask(this.taskBase);this.clearFeedback();this.dirtyState();this.status(tText("tasks:saved"));this.renderProperties();};
  $('[data-open-parent]',form)?.addEventListener('click',()=>this.selectTask(parent.id));
  $('[data-add-subtask]',form).onclick=async()=>{if(!(await this.flush()))return;this.addParent=task.id;this.expanded.add(task.id);this.collapsed.delete(task.id);this.renderCenter();$('[data-add-child] input',this.host)?.focus();};
  $('[data-delete-task]',form).onclick=()=>{if(descendants.length){this.deleteConfirm=true;this.renderProperties();}else this.deleteTask(task);};
  $('[data-confirm-delete-task]',form)?.addEventListener('click',()=>this.deleteTask(task));
  $('[data-cancel-delete]',form)?.addEventListener('click',()=>{this.deleteConfirm=false;this.renderProperties();});
 }
 changedTask(){this.editSequence++;this.dirtyState();this.status(tText("tasks:notSaved"));clearTimeout(this.saveTimer);this.saveTimer=setTimeout(()=>this.saveTask(),450);}
 async deleteTask(task){return this.act(async()=>{await this.ctx.api('/tasks/'+encodeURIComponent(task.id),'DELETE');this.clearPanel();await this.refresh();this.status(tText("tasks:reminderDeleted"));},{skipFlush:true});}
 listFieldsMarkup(value,disabled=false){return `<label>${tHtml("tasks:listName")}<input name="name" aria-label="${tHtml("tasks:listName")}" maxlength="100" required value="${esc(value.name||'')}" ${disabled?'disabled':''}></label><div class="tasks-list-style"><label>${tHtml("tasks:icon")}<select name="icon" aria-label="${tHtml("tasks:listIcon")}" ${disabled?'disabled':''}>${Object.entries(listIcons).map(([id,title])=>`<option value="${id}" ${value.icon===id?'selected':''}>${title}</option>`).join('')}</select></label><label>${tHtml("tasks:color")}<select name="color" aria-label="${tHtml("tasks:listColor")}" ${disabled?'disabled':''}>${[['blue',tText("tasks:blue")],['sage',tText("tasks:green")],['amber',tText("tasks:yellow")],['lilac',tText("tasks:purple")],['rose',tText("tasks:pink")],['slate',tText("tasks:gray")]].map(([id,title])=>`<option value="${id}" ${value.color===id?'selected':''}>${title}</option>`).join('')}</select></label></div><label class="tasks-check-label"><input type="checkbox" name="retain_completed" ${value.retain_completed?'checked':''} ${disabled?'disabled':''}>${tHtml("tasks:doNotDeleteCompletedReminders")}</label><p class="tasks-small-note">${value.retain_completed?tText("tasks:completedRemindersRemainInThisList"):tText("tasks:completedRemindersAreDeletedOnTheNextCalendar")}</p>`;}
 renderCreateProperties(header){
  this.properties.innerHTML=`${header(tText("tasks:newList"))}<form class="tasks-list-properties" data-create-list-form>${this.listFieldsMarkup({name:'',icon:'list',color:'blue',retain_completed:false})}<button type="submit" class="tasks-primary-button">${tHtml("tasks:createList")}</button></form>`;
  const form=$('[data-create-list-form]',this.properties);form.onsubmit=async e=>{e.preventDefault();if(!form.reportValidity())return;await this.act(async()=>{const data=new FormData(form);const list=await this.ctx.api('/task-lists','POST',{name:data.get('name'),icon:data.get('icon'),color:data.get('color'),retain_completed:!!data.get('retain_completed')});this.view={kind:'list',id:list.id};this.clearPanel();await this.refresh();});};
 }
 renderListProperties(header){
  const list=this.lists.find(item=>item.id===this.panel.id);if(!list){this.clearPanel();return;}const owner=canManage(list,this.ctx.state.me),draft=this.listDraft||editableList(list),members=list.members||[],invitations=list.invitations||[],available=this.friends.filter(friend=>!members.some(member=>member.id===friend.id&&member.active!==false)&&!invitations.some(item=>item.recipient_id===friend.id));
  this.properties.innerHTML=`${header(owner?tText("tasks:listSettings"):tText("tasks:listParticipants"))}<form class="tasks-list-properties" data-list-properties>${this.listFieldsMarkup(draft,!owner)}${owner?`<button type="submit" class="tasks-text-button">${tHtml("tasks:saveSettings")}</button>`:''}<div data-property-save-status class="tasks-property-status" role="status">${tHtml("tasks:saved")}</div></form><section class="tasks-list-sharing"><h3>${tHtml("tasks:members")}</h3><p class="tasks-small-note">${tHtml("tasks:participantsCanAddAndEditReminders")}</p><div class="tasks-list-members">${members.map(member=>`<div><span><strong>${personLabel(member)}</strong><small>${member.owner?tText("tasks:owner"):member.active===false?tText("tasks:accessIsRestricted"):esc(member.login)}</small></span>${owner&&!member.owner?`<button type="button" class="tasks-icon-button" data-remove-list-member="${esc(member.id)}" aria-label="${tHtml("tasks:revokeAccess", {value0:(esc(member.login))})}" title="${tHtml("tasks:revokeAccess2")}">${taskIcon('close')}</button>`:''}</div>`).join('')}</div>${invitations.map(item=>`<div class="tasks-pending-invitation"><span>${esc(personName(item.recipient))} · ${tHtml('tasks:invitationPending')}</span><button type="button" class="tasks-text-button" data-cancel-list-invitation="${esc(item.id)}">${tHtml('tasks:cancelInvitation')}</button></div>`).join('')}${owner?`<form class="tasks-share-form" data-share-task-list><label>${tHtml("tasks:shareWithAFriend")}<select name="user_id" aria-label="${tHtml("tasks:friendForSharedList")}" ${available.length?'':'disabled'}><option value="">${tHtml("tasks:selectAFriend")}</option>${available.map(friend=>`<option value="${esc(friend.id)}">${esc(personName(friend))} · ${esc(friend.login)}</option>`).join('')}</select></label><button type="submit" class="tasks-text-button" ${available.length?'':'disabled'}>${tHtml("tasks:inviteParticipant")}</button>${available.length?'':`<p class="tasks-small-note">${tHtml("tasks:addAFriendInThePeopleGroupsSection")}</p>`}</form>`:''}</section>${owner&&!list.is_default?`<div class="tasks-list-delete"><button type="button" class="tasks-text-button is-danger" data-delete-task-list>${tHtml("tasks:deleteList")}</button>${this.deleteConfirm?`<div class="tasks-inline-confirm"><p>${tHtml("tasks:deleteListWithRecovery", {value0:esc(list.name)})}</p><button type="button" class="tasks-danger-button" data-confirm-delete-list>${tHtml("tasks:deleteListAndReminders")}</button><button type="button" class="tasks-text-button" data-cancel-delete>${tHtml("tasks:cancel")}</button></div>`:''}</div>`:''}`;
  const form=$('[data-list-properties]',this.properties);
  if(owner){const change=e=>{const name=e.target.name;if(!name)return;this.listDraft[name]=name==='retain_completed'?e.target.checked:e.target.value;this.listEditSequence++;this.dirtyState();this.status(tText("tasks:notSaved"));clearTimeout(this.listSaveTimer);this.listSaveTimer=setTimeout(()=>this.saveList(),450);};form.oninput=change;form.onchange=change;form.onsubmit=e=>{e.preventDefault();this.flush();};
   $('[data-share-task-list]',this.properties).onsubmit=async e=>{e.preventDefault();const uid=$('[name=user_id]',e.currentTarget).value;if(!uid)return;await this.act(async()=>{await this.ctx.api('/task-lists/'+encodeURIComponent(list.id)+'/members','POST',{user_id:uid});await this.refresh();this.status(tText("tasks:invitationSent"));});};
   this.properties.querySelectorAll('[data-cancel-list-invitation]').forEach(button=>button.onclick=()=>this.act(async()=>{await this.ctx.api('/task-lists/'+encodeURIComponent(list.id)+'/invitations/'+encodeURIComponent(button.dataset.cancelListInvitation),'DELETE');await this.refresh();}));
   this.properties.querySelectorAll('[data-remove-list-member]').forEach(button=>button.onclick=()=>this.act(async()=>{await this.ctx.api('/task-lists/'+encodeURIComponent(list.id)+'/members/'+encodeURIComponent(button.dataset.removeListMember),'DELETE');await this.refresh();this.status(tText("tasks:accessIsRestricted"));}));
   $('[data-delete-task-list]',this.properties)?.addEventListener('click',()=>{this.deleteConfirm=true;this.renderProperties();});
   $('[data-confirm-delete-list]',this.properties)?.addEventListener('click',()=>this.act(async()=>{await this.ctx.api('/task-lists/'+encodeURIComponent(list.id),'DELETE');this.clearPanel();this.view={kind:'list',id:null};await this.refresh();}));
   $('[data-cancel-delete]',this.properties)?.addEventListener('click',()=>{this.deleteConfirm=false;this.renderProperties();});
  }
 }
}
