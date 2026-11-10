import {tText,tHtml} from '../../core/i18n.js';
import {$,esc,formBind} from '../../core/ui.js';
import {kopecks,allocate,allocateWeighted} from './allocation.js';
import {categoryName,categoryArt,expenseCategories,categoryGroups} from './catalog.js';
import {money} from './report.js';
import {groupTargets,selectTarget,defaultTargets,manageGroups,includePayer} from './groups.js';
import './entry-editor.css';
const quickCategories=['bar','restaurant','groceries','activities','taxi','other'];

const text=(key,args)=>tText('expenses:'+key,args),html=(key,args)=>tHtml('expenses:'+key,args);
const image=id=>`<img src="${categoryArt(id)}" alt="" width="72" height="72" loading="lazy">`;
export function openExpenseEntry(ctx,event,{existing=null,after=()=>{}}={}){
  let bill=event,kind=existing?.kind==='repayment'?'repayment':existing?.kind==='debt'?(Object.hasOwn(existing.allocations,'me')?'borrow':'lend'):'expense';
  let category=existing?.category||'restaurant',selected=new Set(existing?Object.keys(existing.allocations):defaultTargets(event));
  let payer=existing?.payer||'me',peer=existing?Object.keys(existing.allocations)[0]:event.participants.find(p=>p.id!==payer&&p.id!=='unknown')?.id;
  let mode=existing?'custom':'equal',amount=existing?String(existing.amount/100):'',note=existing?.note||'',manual=existing?Object.fromEntries(Object.entries(existing.allocations).map(([id,value])=>[id,String(value/100)])):{},weights={};
  let repaidFor=existing?.repaid_for||event.groups?.find(g=>g.members.includes(payer))?.id||payer;
  let busy=false,dirty=false,closed=false;
  const overlay=ctx.modal(`<div data-expense-editor></div>`);overlay.classList.add('expense-entry-overlay');
  const host=$('[data-expense-editor]',overlay),close=()=>$('.modal-close',overlay).click();
  overlay.addEventListener('beforeclose',e=>{if(busy||dirty&&!confirm(text('discardEntry')))e.preventDefault();});
  overlay.addEventListener('close',()=>closed=true);
  const amountText=n=>money(n,bill.currency),name=id=>[...groupTargets(bill),...bill.participants].find(p=>p.id===id)?.name||'';
  const recordKind=()=>kind==='expense'?'expense':kind==='repayment'?'repayment':'debt';
  const calculation=()=>{
    const value=kopecks(amount);if(value<=0)throw Error(text('amountRequired'));
    if(kind!=='expense'){if(!peer||peer===payer)throw Error(text('sameParticipant'));return {[peer]:value};}
    const defaults=Object.fromEntries((bill.groups||[]).map(g=>[g.id,g.members.length]));return mode==='custom'?allocate(value,[...selected],manual):allocateWeighted(value,[...selected],mode==='weights'?{...defaults,...weights}:defaults);
  };
  function preview(){
    const status=$('[data-expense-summary]',host),error=$('[data-expense-error]',host),save=$('[data-expense-save]',host);
    try{
      const values=calculation(),total=kopecks(amount);save.disabled=busy;error.textContent='';
      status.textContent=kind==='expense'?text('moneySummary',{total:amountText(total),count:[...selected].reduce((n,id)=>n+(bill.groups?.find(g=>g.id===id)?.members.length||1),0)}):text('transferSummary',{from:name(payer),to:name(peer),total:amountText(total)});
      host.querySelectorAll('[data-expense-share]').forEach(el=>el.textContent=selected.has(el.dataset.expenseShare)?amountText(values[el.dataset.expenseShare]||0):el.dataset.expenseShare===payer?text('includedInPair'):'—');
    }catch(err){save.disabled=true;status.textContent='';error.textContent=amount?err.message:'';}
  }
  function pickCategory(){
    const picker=ctx.modal(`<h2>${html('chooseCategory')}</h2>${categoryGroups.map(group=>`<section class="expense-category-group"><h3>${html(group+'Group')}</h3><div class="expense-category-grid">${expenseCategories.filter(x=>x.group===group).map(x=>`<button type="button" class="expense-category-tile ${category===x.id?'selected':''}" aria-pressed="${category===x.id}" data-pick-category="${x.id}">${image(x.id)}<span>${esc(categoryName(x.id))}</span></button>`).join('')}</div></section>`).join('')}`);
    picker.classList.add('expense-category-overlay');picker.querySelectorAll('[data-pick-category]').forEach(b=>b.onclick=()=>{category=b.dataset.pickCategory;dirty=true;$('.modal-close',picker).click();render();});
  }
  function render(){
    if(kind==='expense')includePayer(selected,payer,bill);
    const active=document.activeElement, scroll=host.querySelector('.expense-entry-scroll')?.scrollTop||0;
    const focus=host.contains(active)?['name','data-expense-person','data-expense-manual','data-weight','data-quick-category','data-more-categories','data-current-category','data-add-person'].filter(key=>active.hasAttribute(key)).map(key=>[key,active.getAttribute(key)]):[];
    if(active?.hasAttribute('data-weight'))focus.push(['data-delta',active.getAttribute('data-delta')]);
    const action=existing?'saveEntry':kind==='expense'?'addExpense':'recordTransfer';
    const peerLabel=kind==='repayment'?'receiverLabel':'debtorLabel';
    const people=bill.participants,targets=[...groupTargets(bill),...people];
    host.innerHTML=`<header class="expense-entry-heading"><div><h2>${html(action)}</h2><p class="hint">${esc(bill.title)}</p></div></header><form data-expense-entry><div class="expense-entry-scroll"><label class="expense-kind-label">${html('recordType')}<select name="kind">${['expense','lend','borrow','repayment'].map(id=>`<option value="${id}" ${kind===id?'selected':''}>${html(id==='expense'?'expenseType':id)}</option>`).join('')}</select></label>${kind==='expense'?`<div><div class="expense-category-caption"><p class="expense-field-caption">${html('categoryLabel')}</p><button type="button" class="text-link" data-more-categories>${html('allCategoriesLink')}</button></div><div class="expense-quick-categories">${quickCategories.map(id=>`<button type="button" class="expense-category-tile ${category===id?'selected':''}" data-quick-category="${id}" aria-pressed="${category===id}">${image(id)}<span>${esc(categoryName(id))}</span></button>`).join('')}</div>${!quickCategories.includes(category)?`<button type="button" class="expense-selected-category compact" data-current-category>${image(category)}<strong>${esc(categoryName(category))}</strong></button>`:''}</div>`:`<div class="expense-selected-category">${image(kind)}<strong>${html(kind)}</strong></div>`}<div class="expense-entry-columns"><div><label>${html('amountLabel')}<div class="expense-entry-amount"><input name="amount" inputmode="decimal" autocomplete="off" value="${esc(amount)}" aria-label="${html('amountLabel')}" required><span>${esc(bill.currency||'RUB')}</span></div></label><label>${html(kind==='lend'||kind==='borrow'?'creditorLabel':'payerLabel')}<select name="payer">${people.map(p=>`<option value="${p.id}" ${payer===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><label>${html('optionalComment')}<input name="note" maxlength="400" value="${esc(note)}"></label></div><div>${kind==='expense'?`<label>${html('splitLabel')}<select name="split">${[['equal','equalSplit'],['custom','manualSplit'],['weights','weightedSplit']].map(([value,key])=>`<option value="${value}" ${mode===value?'selected':''}>${html(key)}</option>`).join('')}</select></label><p class="expense-field-caption">${html('chargeTargets')}</p><div class="expense-entry-people">${targets.map(p=>`<div class="expense-entry-person ${p.isGroup?'is-group':''}"><label><input type="checkbox" data-expense-person="${p.id}" ${selected.has(p.id)||p.id===payer?'checked':''} ${p.id===payer?'disabled aria-disabled="true"':''}>${esc(p.name)}${p.id===payer?`<small class=expense-payer-mark>${html('payerMark')}</small>`:''}</label>${mode==='custom'&&selected.has(p.id)?`<div><input data-expense-manual="${p.id}" inputmode="decimal" value="${esc(manual[p.id]??'')}" placeholder="${html('autoShareShort')}" aria-label="${html('share2',{value0:esc(p.name)})}" ${selected.has(p.id)?'':'disabled'}><small data-expense-share="${p.id}"></small></div>`:mode==='weights'&&selected.has(p.id)?`<div class="expense-weight"><button type="button" data-weight="${p.id}" data-delta="-1" aria-label="${html('decreaseShare',{name:esc(p.name)})}" ${!selected.has(p.id)||(weights[p.id]??p.members?.length??1)<=1?'disabled':''}>−</button><span>${weights[p.id]??p.members?.length??1}×</span><button type="button" data-weight="${p.id}" data-delta="1" aria-label="${html('increaseShare',{name:esc(p.name)})}" ${!selected.has(p.id)||(weights[p.id]??p.members?.length??1)>=20?'disabled':''}>+</button><small data-expense-share="${p.id}"></small></div>`:`<output data-expense-share="${p.id}"></output>`}</div>`).join('')}</div>${mode==='custom'?`<p class="hint">${html('autoRemainder')}</p>`:''}<button type="button" class="text-link" data-add-person>${html('addParticipant')}</button><button type=button class=text-link data-manage-pairs>${html('manageGroups')}</button>`:`<label>${html(peerLabel)}<select name="peer">${people.filter(p=>p.id!==payer).map(p=>`<option value="${p.id}" ${peer===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>${kind==='repayment'?`<label>${html('repaidFor')}<select name="repaid_for">${targets.filter(p=>p.id===payer||p.members?.includes(payer)||bill.groups?.some(g=>g.members.includes(payer)&&g.members.includes(p.id))).map(p=>`<option value="${p.id}" ${repaidFor===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>`:''}`}</div></div><p data-expense-error class="expense-entry-error" role="alert"></p><button type="button" class="text-link" data-refresh-bill hidden>${html('refreshBill')}</button></div><footer class="expense-entry-footer"><p data-expense-summary role="status"></p><button type="submit" class="primary" data-expense-save>${html(action)}</button></footer></form>`;
    $('[name=amount]',host).oninput=e=>{amount=e.target.value;dirty=true;preview();};
    $('[name=note]',host).oninput=e=>{note=e.target.value;dirty=true;};
    $('[name=payer]',host).onchange=e=>{payer=e.target.value;repaidFor=bill.groups?.find(g=>g.members.includes(payer))?.id||payer;if(peer===payer)peer=people.find(p=>p.id!==payer)?.id;dirty=true;render();};
    $('[name=kind]',host).onchange=e=>{kind=e.target.value;dirty=true;if(kind==='borrow'){peer='me';payer=people.find(p=>p.id!=='me'&&p.id!=='unknown')?.id||'me';}else{payer='me';peer=people.find(p=>p.id!=='me'&&p.id!=='unknown')?.id;}render();};
    $('[name=peer]',host)?.addEventListener('change',e=>{peer=e.target.value;dirty=true;preview();});
    $('[name=split]',host)?.addEventListener('change',e=>{mode=e.target.value;dirty=true;render();});
    host.querySelectorAll('[data-expense-person]').forEach(input=>input.onchange=()=>{selectTarget(selected,input.dataset.expensePerson,input.checked,bill);dirty=true;render();});
    host.querySelectorAll('[data-expense-manual]').forEach(input=>input.oninput=()=>{manual[input.dataset.expenseManual]=input.value;dirty=true;preview();});
    host.querySelectorAll('[data-weight]').forEach(b=>b.onclick=()=>{weights[b.dataset.weight]=Math.max(1,Math.min(20,(weights[b.dataset.weight]??bill.groups?.find(g=>g.id===b.dataset.weight)?.members.length??1)+Number(b.dataset.delta)));dirty=true;render();});
    host.querySelectorAll('[data-quick-category]').forEach(b=>b.onclick=()=>{category=b.dataset.quickCategory;dirty=true;render();});
    $('[data-more-categories]',host)?.addEventListener('click',pickCategory);$('[data-current-category]',host)?.addEventListener('click',pickCategory);
    $('[name=repaid_for]',host)?.addEventListener('change',e=>{repaidFor=e.target.value;dirty=true;});
    $('[data-manage-pairs]',host)?.addEventListener('click',()=>manageGroups(ctx,bill,updated=>{bill=updated;for(const group of bill.groups||[]){if(group.members.every(id=>selected.has(id))){group.members.forEach(id=>selected.delete(id));selected.add(group.id);}}const valid=new Set([...bill.participants,...groupTargets(bill)].map(p=>p.id));selected=new Set([...selected].filter(id=>valid.has(id)));render();}));
    $('[data-add-person]',host)?.addEventListener('click',()=>{
      const dialog=ctx.modal(`<h2>${html('addParticipant')}</h2><form data-new-person><label>${html('participantName')}<input name="name" required maxlength="80"></label><button type="submit" class="primary">${html('add')}</button></form>`);
      formBind($('[data-new-person]',dialog),async f=>{const value=f.get('name').trim();if(!value)throw Error(text('enterName'));const id=crypto.randomUUID().replaceAll('-','').slice(0,16);bill=await ctx.api('/expenses/'+bill.id,'PUT',{title:bill.title,participants:[...bill.participants,{id,name:value}],retention_days:bill.retention_days,currency:bill.currency,version:bill.version});selected.add(id);$('.modal-close',dialog).click();render();});
    });
    $('[data-refresh-bill]',host).onclick=async()=>{try{bill=await ctx.api('/expenses/'+bill.id);render();$('[data-expense-error]',host).textContent=text('billRefreshed');}catch(err){ctx.toast(err.message);}};
    $('[data-expense-entry]',host).addEventListener('submit',async e=>{
      e.preventDefault();if(busy)return;
      try{
        const value=kopecks(amount),allocations=calculation();busy=true;preview();$('.expense-entry-scroll',host).inert=true;
        await ctx.api('/expenses/'+bill.id+'/entries'+(existing?'/'+existing.id:''),existing?'PUT':'POST',{kind:recordKind(),category:kind==='expense'?category:'mixed',payer,amount:value,allocations,note,repaid_for:kind==='repayment'?repaidFor:null,event_version:bill.version});
        dirty=false;busy=false;close();await after();
      }catch(err){if(closed)return;$('[data-expense-error]',host).textContent=err.message;$('[data-refresh-bill]',host).hidden=false;}
      finally{busy=false;if(!closed){$('.expense-entry-scroll',host).inert=false;$('[data-expense-save]',host).disabled=false;}}
    });
    preview();
    if(focus.length){const candidate=[...host.querySelectorAll('button,input,select')].find(el=>focus.every(([key,value])=>el.getAttribute(key)===value));if(candidate&&!candidate.disabled)candidate.focus({preventScroll:true});}
    $('.expense-entry-scroll',host).scrollTop=scroll;
  }
  render();return overlay;
}
