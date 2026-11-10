import {tText,tHtml,locale} from "../../core/i18n.js";
import {sectionHeader} from '../../core/section-header.js';
import {displayName} from '../profile/index.js';
import {openExpenseEntry} from './entry-editor.js';
import {$,esc,date,formBind} from '../../core/ui.js';
import {money,expenseReport} from './report.js';
import {bindExpensePhotos} from './photos.js';
import {manageGroups} from './groups.js';
import {currencies} from './catalog.js';
import './expenses.css';
const newId=()=>crypto.randomUUID().replaceAll('-','').slice(0,16);
export async function startExpense(ctx,after=()=>{},config=null){
  config ||= ctx.state.me.settings.dashboard?.instances?.find(x=>x.module_id==='expenses')?.config || {};
  let options;try{options=await ctx.api('/expenses/options');}catch(error){ctx.toast(error.message);return;}
  const names=(config.participants||[]).filter(x=>!options.legacy_participant_defaults.includes(x));
  const el=ctx.modal(`<h2>${tHtml("expenses:startTheParty")}</h2><p class="muted">${tHtml("expenses:quickNotesAreAvailableFor20HoursThen")}</p><form data-start-event><label>${tHtml("expenses:billName")}<input name="title" maxlength="150" placeholder="${tHtml("expenses:billName")}" required></label><label>${tHtml("expenses:myName")}<input name="me" maxlength="80" value="${esc(displayName(ctx.state.me))}" required></label><label>${tHtml("expenses:participantsEachOnANewLine")}<textarea name="names" rows="1" placeholder="${tHtml("expenses:participantName")}">${esc(names.join("\n"))}</textarea></label><label>${tHtml("expenses:currency")}<select name="currency">${currencies.map(id=>`<option value="${id}" ${id===options.default_currency?'selected':''}>${tHtml("expenses:currency"+id)}</option>`).join('')}</select></label><label>${tHtml("expenses:saveAfterCompletion")}<select name="retention"><option value="7">${tHtml("expenses:label7Days")}</option><option value="30" selected>${tHtml("expenses:label30Days")}</option><option value="90">${tHtml("expenses:label90Days")}</option></select></label><button class="primary" type="submit">${tHtml("expenses:startTheEvening")}</button></form>`);
  $('[name=retention]',el).value=String(config.retention_days||30);
  formBind($('[data-start-event]',el),async f=>{const participants=[{id:'me',name:f.get('me').trim()},...f.get('names').split('\n').map(x=>x.trim()).filter(Boolean).map(name=>({id:newId(),name}))];const event=await ctx.api('/expenses','POST',{title:f.get('title'),participants,retention_days:Number(f.get('retention')),currency:f.get('currency')});$('.modal-close',el).click();await after(event);});
}
export async function mountExpenses(host,ctx){
  let selected=ctx.state.expenseSelected,lastRendered;
  host.innerHTML=`<div class="page-heading dashboard-heading"><div><p class="eyebrow">${tHtml("expenses:moneyFriends")}</p><h1>${tHtml("expenses:splitBills")}<span class="hello-dot">.</span></h1></div><button class="primary" data-start-night>${tHtml("expenses:startTheEvening2")}</button></div><div class="expense-layout expense-workspace"><aside class="panel expense-list" aria-label="${tHtml("expenses:bills")}" data-events><p class="expense-list-message" role="status">${tHtml("expenses:loadingInvoices")}</p></aside><main data-event></main></div>`;
  sectionHeader(host,ctx);
  $('[data-start-night]',host).onclick=()=>startExpense(ctx,async e=>{selected=e.id;ctx.state.expenseSelected=e.id;await reload();});
  const reload=async({background=false}={})=>{
    const events=await ctx.api('/expenses');if(!host.isConnected)return;
    if(!selected||!events.some(e=>e.id===selected))selected=events[0]?.id;
    const snapshot=JSON.stringify([events,selected]);
    if(background&&snapshot===lastRendered)return;
    const focused=host.contains(document.activeElement)?document.activeElement.dataset.eventId||(document.activeElement.hasAttribute('data-retry-events')?selected:null):null;
    const listScroll=$('.expense-event-list',host)?.scrollLeft;
    $('[data-events]',host).innerHTML=`<div class="expense-list-heading"><h2>${tHtml("expenses:bills")}</h2><span class="expense-list-count" aria-label="${tHtml("expenses:numberOfAccounts", {value0:(events.length)})}">${events.length}</span></div><div class="expense-event-list">${events.map(e=>`<button type="button" class="expense-event-link ${selected===e.id?'active':''}" data-event-id="${esc(e.id)}" ${selected===e.id?'aria-current="true"':''}><strong class="expense-event-title">${esc(e.title)}</strong><span class="expense-event-meta"><span class="expense-event-status ${e.active?'ongoing':''}">${e.active?tText("expenses:currently"):tText("expenses:completed")}</span><time datetime="${new Date(e.created*1000).toISOString()}" title="${esc(date(e.created))}">${new Date(e.created*1000).toLocaleString(locale(),{dateStyle:'short',timeStyle:'short'})}</time></span><span class="expense-event-amount"><span>${tHtml("expenses:myShare")}</span><b>${money(e.my_cost??e.cost.me,e.currency)}</b></span></button>`).join('')||`<p class="expense-list-message">${tHtml("expenses:startTheEveningAndMarkPaymentsWithA")}</p>`}</div>`;
    host.querySelectorAll('[data-event-id]').forEach(b=>b.onclick=()=>{selected=b.dataset.eventId;ctx.state.expenseSelected=selected;reload().catch(e=>ctx.toast(e.message));});
    const list=$('.expense-event-list',host);list.scrollLeft=listScroll??$('.expense-event-link.active',list)?.offsetLeft??0;
    if(focused){
      const button=Array.from(host.querySelectorAll('[data-event-id]')).find(b=>b.dataset.eventId===focused);
      button?.focus({preventScroll:true});
      if(button&&list.scrollWidth>list.clientWidth){
        if(button.offsetLeft<list.scrollLeft)list.scrollLeft=button.offsetLeft;
        else if(button.offsetLeft+button.offsetWidth>list.scrollLeft+list.clientWidth)list.scrollLeft=button.offsetLeft+button.offsetWidth-list.clientWidth;
      }
    }
    const event=events.find(e=>e.id===selected);if(event)draw(event);else $('[data-event]',host).innerHTML='';
    lastRendered=snapshot;
  };
  const update=async(e,changes)=>ctx.api('/expenses/'+e.id,'PUT',{title:e.title,participants:e.participants,retention_days:e.retention_days,version:e.version,...changes});
  const draw=e=>{
    const box=$('[data-event]',host);box.innerHTML=`<section class="panel"><div class="panel-head"><div><h2>${esc(e.title)}</h2><p class="hint">${tHtml("expenses:delete", {value0:(e.active?tText("expenses:until", {value0:(date(e.ends))}):tText("expenses:eveningFinished")), value1:(date(e.purge_at))})}</p></div><button class="secondary compact" data-event-settings>${tHtml("expenses:settings")}</button><button class="secondary compact" data-share-expenses>${tHtml("expenses:share")}</button></div>${e.active?`<button type="button" class="primary" data-add-expense>${tHtml("expenses:addExpense")}</button>`:''}<div class=expense-photo-editor><button type=button class="secondary compact" data-add-event-photo>${tHtml("expenses:eveningPhoto")}</button><input data-event-photo type=file accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden><span class=hint>${tHtml("expenses:photosLimitHint")}</span><p data-photo-status role=status class=hint></p></div>${expenseReport(e,true)}</section>`;
    $('[data-add-expense]',box)?.addEventListener('click',()=>openExpenseEntry(ctx,e,{after:reload}));
    const photo=$('[data-event-photo]',box),addPhoto=$('[data-add-event-photo]',box);photo.disabled=addPhoto.disabled=e.photos.length>=50;addPhoto.onclick=()=>photo.click();
    let pending=[];
    const upload=async()=>{photo.disabled=addPhoto.disabled=true;let completed=0;try{if(pending.length+(await ctx.api('/expenses/'+e.id)).photos.length>50)throw Error(tText('expenses:expensePhotosLimit'));const count=pending.length;while(pending.length){const file=pending[0];if(file.size>10*1024*1024)throw Error(tText('expenses:thePhotoShouldNotExceed10Mb'));status.textContent=tText('expenses:uploadPhotoProgress',{index:completed+1,count});await ctx.api('/expenses/'+e.id+'/photos','POST',file);pending.shift();completed++;}await reload();}catch(error){status.textContent=error.message;photo.disabled=addPhoto.disabled=false;photo.value='';if(pending.length){const retry=document.createElement('button');retry.type='button';retry.className='text-link';retry.textContent=tText('expenses:photoUploadRetry',{count:pending.length});retry.onclick=()=>{retry.remove();upload();};status.append(retry);}ctx.toast(error.message);}};
    const status=$('[data-photo-status]',box);photo.onchange=()=>{pending=[...photo.files];upload();};
    bindExpensePhotos(box,e,{remove:async id=>{await ctx.api('/expenses/'+e.id+'/photos/'+id,'DELETE');await reload();}});


    box.querySelectorAll('[data-edit-entry]').forEach(b=>b.onclick=()=>openExpenseEntry(ctx,e,{existing:e.entries.find(x=>x.id===b.dataset.editEntry),after:reload}));
    box.querySelectorAll('[data-delete-entry]').forEach(b=>b.onclick=async()=>{if(!confirm(tText("expenses:deleteEntryFromCalculations")))return;try{await ctx.api('/expenses/'+e.id+'/entries/'+b.dataset.deleteEntry,'DELETE');await reload();}catch(err){ctx.toast(err.message);}});
    $('[data-event-settings]',box).onclick=()=>{
      const el=ctx.modal(`<h2>${tHtml("expenses:eveningSettings")}</h2><form data-event-settings-form><label>${tHtml("expenses:name")}<input name="title" maxlength="150" value="${esc(e.title)}" required></label>${e.participants.map(p=>`<label>${tHtml("expenses:member")}<input data-person="${p.id}" value="${esc(p.name)}" maxlength="80" required></label>`).join('')}<label>${tHtml("expenses:addParticipantsEachOnANewLine")}<textarea name="new_names" rows="2"></textarea></label><label>${tHtml("expenses:saveAfterCompletion")}<select name="retention">${[7,30,90].map(n=>`<option value="${n}" ${e.retention_days===n?'selected':''}>${tHtml("expenses:days", {value0:(n)})}</option>`).join('')}</select></label><button type="button" class="secondary" data-manage-pairs>${tHtml("expenses:groups")}</button><button class="primary" type="submit">${tHtml("expenses:save")}</button>${e.active?`<button class="secondary" type="button" data-finish-night>${tHtml("expenses:endEveningNow")}</button>`:''}<button type="button" class="text-link danger" data-delete-event>${tHtml("expenses:deleteEvening")}</button></form>`);
      $('[data-manage-pairs]',el).onclick=()=>{ $('.modal-close',el).click();manageGroups(ctx,e,()=>reload().catch(error=>ctx.toast(error.message)));};
      formBind($('[data-event-settings-form]',el),async f=>{const participants=e.participants.map(p=>({...p,name:el.querySelector(`[data-person="${p.id}"]`).value.trim()}));participants.push(...f.get('new_names').split('\n').map(x=>x.trim()).filter(Boolean).map(name=>({id:newId(),name})));await update(e,{title:f.get('title'),participants,retention_days:Number(f.get('retention'))});$('.modal-close',el).click();await reload();});
      $('[data-finish-night]',el)?.addEventListener('click',async()=>{try{await update(e,{finish:true});$('.modal-close',el).click();await reload();}catch(err){ctx.toast(err.message);}});
      $('[data-delete-event]',el).onclick=async()=>{if(!confirm(tText("expenses:deleteEveningEntireHistoryAndLinks")))return;try{await ctx.api('/expenses/'+e.id,'DELETE');$('.modal-close',el).click();await reload();}catch(err){ctx.toast(err.message);}};
    };
    $('[data-share-expenses]',box).onclick=async()=>{
      const el=ctx.modal(`<h2>${tHtml("expenses:linkToCalculations")}</h2><p class="muted">${tHtml("expenses:theLinkShowsNamesPaymentHistoryAndDebts")}</p><form data-share-event><div class="duration-fields"><label>${tHtml("expenses:duration")}<input name="duration" type="number" min="1" max="10000" value="2" required></label><label>${tHtml("expenses:unit")}<select name="unit"><option value="minutes">${tHtml("expenses:minutes")}</option><option value="hours">${tHtml("expenses:clocks")}</option><option value="days" selected>${tHtml("expenses:days2")}</option><option value="weeks">${tHtml("expenses:weeks")}</option><option value="months">${tHtml("expenses:months")}</option><option value="years">${tHtml("expenses:years")}</option></select></label></div><button type="submit" class="primary">${tHtml("expenses:createLink")}</button></form><div data-expense-links></div>`);
      const links=async()=>{const items=await ctx.api('/expenses/'+e.id+'/shares');if(!el.isConnected)return;$('[data-expense-links]',el).innerHTML=items.map(s=>`<div class="share-row"><div><input readonly value="${esc(s.url)}" aria-label="${tHtml("expenses:linkToCalculations")}"><span class="small muted">${tHtml("expenses:until", {value0:(date(s.expires))})}</span></div><button class="text-link danger" data-revoke-expense="${s.token}">${tHtml("expenses:revoke")}</button></div>`).join('');el.querySelectorAll('[data-revoke-expense]').forEach(b=>b.onclick=async()=>{try{await ctx.api('/expense-shares/'+b.dataset.revokeExpense,'DELETE');await links();}catch(err){ctx.toast(err.message);}});};
      formBind($('[data-share-event]',el),async f=>{await ctx.api('/expenses/'+e.id+'/shares','POST',{duration:Number(f.get('duration')),unit:f.get('unit')});await links();});links().catch(err=>ctx.toast(err.message));
    };
  };
  try{await reload();}catch(error){
    if(host.isConnected){
      $('[data-events]',host).innerHTML=`<p class="expense-list-message" role="alert">${tHtml("expenses:failedToLoadInvoices")}</p><button type="button" class="secondary compact" data-retry-events>${tHtml("expenses:retry")}</button>`;
      $('[data-retry-events]',host).onclick=()=>reload().catch(err=>ctx.toast(err.message));
    }
    ctx.toast(error.message);
  }
  const timer=setInterval(()=>{if(!host.isConnected)return clearInterval(timer);if(!document.querySelector('.expense-entry-overlay'))reload({background:true}).catch(()=>{});},60000);
}
