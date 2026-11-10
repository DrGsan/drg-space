import {tText,tHtml} from "../../core/i18n.js";
import {$,esc} from '../../core/ui.js';
export function configureTasks(host,instance,modal,ctx){
  const config={view:'list',list_id:null,...instance.config};let lists=null;
  host.innerHTML=`<label>${tHtml("dashboard:reminderSource")}<select name="task_source" aria-label="${tHtml("dashboard:reminderSource")}"><option value="default">${tHtml("dashboard:myRemindersList")}</option><option value="list">${tHtml("dashboard:selectedList")}</option><option value="today">${tHtml("dashboard:today")}</option><option value="all">${tHtml("dashboard:allReminders")}</option><option value="flagged">${tHtml("dashboard:withFlag")}</option></select></label><div data-task-list-options><label>${tHtml("dashboard:list")}<select name="task_list" aria-label="${tHtml("dashboard:list")}"></select></label></div><p class="hint" data-task-source-status role="status"></p><p class="hint">${tHtml("dashboard:thisSourceIsSavedForThisWidgetLists")}</p>`;
  const source=$('[name=task_source]',host),select=$('[name=task_list]',host),status=$('[data-task-source-status]',host);
  source.value=config.view==='list'?(config.list_id?'list':'default'):config.view;
  const selected=config.list_id;
  select.innerHTML=`<option value="${esc(selected||'')}">${selected?tText("dashboard:loadingSelectedList"):tText("dashboard:selectAList")}</option>`;
  const update=()=>{$('[data-task-list-options]',host).hidden=source.value!=='list';};source.onchange=update;update();
  const load=async()=>{
    status.textContent=tText("dashboard:loadingLists");
    try{
      const items=(await ctx.api('/task-lists')).filter(item=>!item.deletion);if(!modal.isConnected)return;
      lists=items;const previous=select.value;
      const defaultList=items.find(item=>item.is_default&&item.can_manage);
      if(defaultList)source.querySelector('[value=default]').textContent=tText("dashboard:myMainList", {value0:(defaultList.name)});
      select.innerHTML=`<option value="">${tHtml("dashboard:selectAList")}</option>${items.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}${item.shared?(" "+tText("dashboard:shared")+""):''}</option>`).join('')}`;
      if(previous&&!items.some(item=>item.id===previous))select.insertAdjacentHTML('beforeend',`<option value="${esc(previous)}">${tHtml("dashboard:listUnavailable")}</option>`);
      select.value=previous;status.textContent=previous&&!items.some(item=>item.id===previous)?tText("dashboard:theSelectedListHasBeenDeletedOrAccess"):'';
    }catch(error){if(modal.isConnected){status.textContent=error.message;const retry=document.createElement('button');retry.type='button';retry.className='text-link';retry.textContent=tText("dashboard:retry");retry.onclick=load;status.append(' ',retry);}}
  };
  load();
  return {read(){
    if(source.value==='default')return {view:'list',list_id:null};
    if(source.value==='list'){
      if(!lists)throw new Error(tText("dashboard:waitForTheListsToLoadOrSelect"));
      if(!lists.some(item=>item.id===select.value))throw new Error(tText("dashboard:selectAnAvailableList"));
      return {view:'list',list_id:select.value};
    }
    return {view:source.value,list_id:null};
  }};
}
