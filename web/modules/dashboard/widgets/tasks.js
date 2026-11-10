import {tText,tHtml,locale} from "../../../core/i18n.js";
import {esc,today,formBind,toast} from '../../../core/ui.js';
import {taskSource,sourceTasks,taskDepth,taskDescendants} from './task-source.js';
import {widgetSize} from '../sizes.js';
import {smartViews,colors,taskIcon,matchesView} from '../../tasks/format.js';
import './tasks.css';
export function mountTasks(host,ctx) {
  const {state,api}=ctx;let disposed=false,lists=[],loaded=false,visible=true,pending,timer,cancelDelete,lastRendered;
  const config=ctx.instance.config||{},compact=widgetSize(ctx.instance.size)==='1x1',$=selector=>host.querySelector(selector);
  host.innerHTML=`<p class="hint" role="status">${tHtml("dashboard:loadingReminders")}</p>`;
  const errorMessage=error=>{if(disposed)return;lastRendered=undefined;host.innerHTML=`<p class="hint" role="status">${esc(error.message)}</p><button class="text-link" data-task-retry>${tHtml("dashboard:retry")}</button>`;$('[data-task-retry]').onclick=()=>load();};
  const openTasks=(source,task)=>{
    delete state.tasksFocus;
    if(source.view==='list')state.tasksFocusList=source.list.id;
    else state.tasksFocusView=source.view;
    if(task)state.tasksSelected=task.id;
    ctx.navigate('tasks');
  };
  const confirmDelete=(task,count)=>new Promise(resolve=>{
    cancelDelete?.();
    const dialog=ctx.modal(`<h2>${tHtml("dashboard:deleteAReminderWithNestedReminders")}</h2><p>${tHtml("dashboard:theReminderAndAllNestedRemindersWillBe", {value0:(esc(task.title)), value1:(count)})}</p><div class="widget-delete-actions"><button class="secondary" data-cancel-task-delete>${tHtml("dashboard:no")}</button><button class="primary" data-confirm-task-delete>${tHtml("dashboard:yes")}</button></div>`);
    let settled=false;
    const finish=answer=>{
      if(settled)return;settled=true;observer.disconnect();cancelDelete=null;
      if(dialog.isConnected)dialog.querySelector('.modal-close').click();resolve(answer);
    };
    const observer=new MutationObserver(()=>{if(!dialog.isConnected)finish(false);});observer.observe(document.body,{childList:true});
    cancelDelete=()=>finish(false);
    dialog.querySelector('[data-cancel-task-delete]').onclick=()=>finish(false);
    dialog.querySelector('[data-confirm-task-delete]').onclick=()=>finish(true);
  });
  const render=()=>{
    if(disposed)return;
    const source=taskSource(config,lists,state.me.id),tasks=sourceTasks(state.tasks,source,today()),done=tasks.filter(task=>task.done).length,byId=new Map(tasks.map(task=>[task.id,task]));
    if(source.unavailable){host.innerHTML=`<div class="task-widget-unavailable"><p class="hint">${tHtml("dashboard:listDeletedOrInaccessible")}</p><button class="text-link" data-task-configure>${tHtml("dashboard:selectAList2")}</button></div>`;$('[data-task-configure]').onclick=ctx.configure;return;}
    if(compact){
      host.innerHTML=`<div class="task-widget-heading"><button class="text-link" data-task-open title="${esc(source.label)}">${esc(source.label)}</button></div><div class="task-summary-grid">${smartViews.map(view=>{
        const count=tasks.filter(task=>matchesView(task,{kind:'smart',id:view.id})).length;
        return `<button type="button" class="task-summary-tile" data-task-summary="${view.id}" style="--task-summary-color:${colors[view.color]};--task-count-digits:${Math.max(2,String(count).length)}" aria-label="${view.title}: ${count}"><span class="task-summary-icon">${taskIcon(view.icon)}</span><strong class="task-summary-count">${count}</strong><span class="task-summary-label">${view.title}</span></button>`;
      }).join('')}</div>`;
      $('[data-task-open]').onclick=()=>openTasks(source);
      host.querySelectorAll('[data-task-summary]').forEach(button=>button.onclick=async()=>{
        delete state.tasksFocusDay;delete state.tasksFocusList;delete state.tasksFocusView;delete state.tasksSelected;
        const focus={kind:'smart',id:button.dataset.taskSummary,scope:{view:source.view,list_id:source.list?.id||null}};
        state.tasksFocus=focus;
        try{await ctx.navigate('tasks');}finally{if(state.tasksFocus===focus)delete state.tasksFocus;}
      });
      return;
    }
    const target=source.list||source.defaultList;
    host.innerHTML=`<div class="task-widget-heading"><button class="text-link" data-task-open title="${esc(source.label)}">${esc(source.label)}</button><span class="counter">${tHtml("dashboard:completed3", {value0:(done), value1:(tasks.length)})}</span></div><div class="task-list">${tasks.map(task=>{
      const meta=[task.day&&new Date(task.day+'T12:00:00').toLocaleDateString(locale(),{day:'numeric',month:'short'}),task.time,task.priority&&['',tText("dashboard:lowPriority"),tText("dashboard:mediumPriority"),tText("dashboard:highPriority")][task.priority],...(task.tags||[]).map(tag=>'#'+tag)].filter(Boolean).join(' · ');
      return `<div class="task-row ${task.done?'done':''}" style="--task-indent:${taskDepth(task,byId)}" data-task-row="${esc(task.id)}"><input type="checkbox" aria-label="${tHtml("dashboard:completed4", {value0:(esc(task.title))})}" data-task="${esc(task.id)}" ${task.done?'checked':''}><div class="task-widget-text"><button class="text-link" data-task-details="${esc(task.id)}">${task.flagged?`<span class="task-widget-flag" aria-label="${tHtml("dashboard:withFlag")}">⚑</span> `:''}${esc(task.title)}</button>${meta?`<small class="muted" title="${esc(meta)}">${esc(meta)}</small>`:''}</div><button class="icon-btn delete-task" data-delete-task="${esc(task.id)}" aria-label="${tHtml("dashboard:deleteReminder")}">×</button></div>`;
    }).join('')||`<div class="task-empty">${source.view==='today'?tText("dashboard:noRemindersForToday"):source.view==='flagged'?tText("dashboard:noRemindersWithFlagYet"):tText("dashboard:noRemindersInThisListYet")}</div>`}</div>${target?`<form data-task-form class="task-add"><input name="title" placeholder="${tHtml("dashboard:addReminder")}" aria-label="${tHtml("dashboard:reminderName")}" maxlength="300" required><button class="icon-btn" type="submit" aria-label="${tHtml("dashboard:addReminder2")}">→</button></form>`:''}`;
    $('[data-task-open]').onclick=()=>openTasks(source);
    host.querySelectorAll('[data-task-details]').forEach(button=>button.onclick=()=>openTasks(source,tasks.find(task=>task.id===button.dataset.taskDetails)));
    formBind($('[data-task-form]'),async form=>{
      await api('/tasks','POST',{title:form.get('title'),list_id:target.id,day:source.view==='today'?today():'',flagged:source.view==='flagged'});
      await reload();
    });
    host.querySelectorAll('[data-task]').forEach(input=>input.onchange=async()=>{
      const task=tasks.find(item=>item.id===input.dataset.task);input.disabled=true;
      try{await api('/tasks/'+task.id,'PATCH',{done:input.checked,version:task.version});await reload();}
      catch(error){input.checked=!input.checked;toast(error.message);}
      finally{input.disabled=false;}
    });
    host.querySelectorAll('[data-delete-task]').forEach(button=>button.onclick=async()=>{
      button.disabled=true;
      try{
        const task=state.tasks.find(item=>item.id===button.dataset.deleteTask),descendants=taskDescendants(button.dataset.deleteTask,state.tasks);
        if(descendants.length&&!(await confirmDelete(task,descendants.length)))return;
        if(disposed)return;
        await api('/tasks/'+button.dataset.deleteTask,'DELETE');await reload();
      }catch(error){toast(error.message);}
      finally{button.disabled=false;}
    });
  };
  const sharedSource=()=>{
    const source=taskSource(config,lists,state.me.id);
    return source.view==='list'?Boolean(source.list?.shared):lists.some(list=>list.shared);
  };
  const updatePolling=()=>{clearInterval(timer);timer=sharedSource()?setInterval(()=>{if(visible&&!document.hidden)load();},30000):null;};
  const load=()=>{
    if(disposed)return Promise.resolve();if(pending)return pending;
    pending=Promise.all([api('/task-lists'),api('/tasks')]).then(([nextLists,tasks])=>{
      if(disposed)return;
      const snapshot=JSON.stringify([nextLists,tasks,today()],(key,value)=>key==='online'||key==='last_seen'?undefined:value);
      lists=nextLists;state.tasks=tasks;loaded=true;
      if(snapshot!==lastRendered){render();lastRendered=snapshot;}
      updatePolling();
    }).catch(errorMessage).finally(()=>pending=null);
    return pending;
  };
  const reload=async()=>{const tasks=await api('/tasks');if(!disposed){state.tasks=tasks;ctx.refresh();}};
  const onVisible=()=>{if(visible&&!document.hidden&&loaded&&sharedSource())load();};
  const observer=new IntersectionObserver(entries=>{const previous=visible;visible=entries[0].isIntersecting;if(visible&&!previous)onVisible();});
  observer.observe(host);document.addEventListener('visibilitychange',onVisible);load();
  return ()=>{disposed=true;cancelDelete?.();clearInterval(timer);observer.disconnect();document.removeEventListener('visibilitychange',onVisible);};
}
