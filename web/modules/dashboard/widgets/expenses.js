import {tText,tHtml,locale} from "../../../core/i18n.js";
import {$,esc} from '../../../core/ui.js';
import {categories,money} from '../../expenses/report.js';
import {startExpense} from '../../expenses/index.js';
import {widgetSize} from '../sizes.js';
import './expenses.css';

const compactMoney=currency=>new Intl.NumberFormat(locale(),{style:'currency',currency:currency||'RUB',notation:'compact',maximumFractionDigits:1});
const shortDate=new Intl.DateTimeFormat(locale(),{day:'numeric',month:'short'});
const amount=(value,currency='RUB')=>`<span class="expense-widget-money" title="${esc(money(value,currency))}" aria-label="${esc(money(value,currency))}">${esc(value>=100000000?'≈ '+compactMoney(currency).format(value/100):money(value,currency))}</span>`;
const inScope=(event,id)=>id==='me'||(event.groups||[]).some(g=>g.members.includes('me')&&(g.id===id||g.members.includes(id)));
const personalDebts=e=>({incoming:e.my_incoming??e.debts.filter(d=>d.to==='me').reduce((n,d)=>n+d.amount,0),outgoing:e.my_outgoing??e.debts.filter(d=>d.from==='me').reduce((n,d)=>n+d.amount,0)});

function accountCard(e){
  return `<button type="button" class="expense-widget-account" data-expense-event="${esc(e.id)}" aria-label="${tHtml("dashboard:myShare", {value0:(esc(e.title)), value1:(esc(money((e.my_cost??e.cost.me),e.currency)))})}">
    <span class="expense-widget-account-title" title="${esc(e.title)}">${esc(e.title)}</span>
    ${amount((e.my_cost??e.cost.me),e.currency)}
    <span class="expense-widget-account-meta"><span class="expense-widget-status ${e.active?'is-active':''}">${e.active?tText("dashboard:currently"):tText("dashboard:completed2")}</span><time datetime="${new Date(e.created*1000).toISOString()}">${esc(shortDate.format(e.created*1000))}</time></span>
  </button>`;
}

function categorySection(items,limit,currency='RUB'){
  const totals=Object.fromEntries(Object.keys(categories).map(k=>[k,items.reduce((n,e)=>n+(e.categories[k]||0),0)]));
  const top=Object.entries(totals).filter(([,v])=>v>0).sort((a,b)=>b[1]-a[1]).slice(0,limit),largest=Math.max(1,...top.map(([,v])=>v));
  return `<section class="expense-widget-categories"><h3>${tHtml("dashboard:myExpensesByCategory")}</h3><div>${top.map(([k,v])=>`<div class="expense-widget-category"><span>${esc(categories[k])}</span>${amount(v,currency)}<span class="expense-widget-bar" aria-hidden="true"><i style="width:${v/largest*100}%"></i></span></div>`).join('')||`<p class="expense-widget-empty-detail">${tHtml("dashboard:noPersonalExpensesYet")}</p>`}</div></section>`;
}

function recentSection(items,limit){
  const recent=items.flatMap(e=>e.entries.slice(-limit).map(entry=>({e,entry}))).sort((a,b)=>b.entry.created-a.entry.created||b.entry.id.localeCompare(a.entry.id)).slice(0,limit);
  return `<section class="expense-widget-recent"><h3>${tHtml("dashboard:recentEntries")}</h3><div>${recent.map(({e,entry})=>`<button type="button" class="expense-widget-entry" data-expense-event="${esc(e.id)}"><span>${esc(entry.kind==='debt'?tText("dashboard:directDebt"):entry.kind==='repayment'?tText('expenses:repayment'):categories[entry.category])}</span>${amount(entry.amount,e.currency)}<small title="${esc(e.title)}">${esc(e.title)}</small></button>`).join('')||`<p class="expense-widget-empty-detail">${tHtml("dashboard:addTheFirstPaymentToTheInvoice")}</p>`}</div></section>`;
}

function debtSection(items){
  const debts=items.flatMap(e=>e.debts.filter(d=>inScope(e,d.from)||inScope(e,d.to)).map(d=>({e,d}))).sort((a,b)=>b.d.amount-a.d.amount).slice(0,2);
  return `<section class="expense-widget-debts"><h3>${tHtml("dashboard:myTransactionsWithParticipants")}</h3><div>${debts.map(({e,d})=>{
    const name=[...e.participants,...(e.groups||[]).map(g=>({id:g.id,name:g.members.map(id=>e.participants.find(p=>p.id===id)?.name||'').join(' + ')}))].find(p=>p.id===(inScope(e,d.from)?d.to:d.from))?.name||tText("dashboard:member");
    return `<button type="button" class="expense-widget-entry" data-expense-event="${esc(e.id)}"><span title="${esc(name)}">${esc(name)}</span>${amount(d.amount,e.currency)}<small title="${esc(e.title)}">${inScope(e,d.from)?tText("dashboard:iOwe"):tText("dashboard:theyOweMe")} · ${esc(e.title)}</small></button>`;
  }).join('')||`<p class="expense-widget-empty-detail">${tHtml("dashboard:allSettled")}</p>`}</div></section>`;
}

export function mountExpenseWidget(host,ctx){
  let disposed=false,currencyChoice,cachedItems=[];
  const size=widgetSize(ctx.instance.size),rich=size==='1x2'||size==='2x2';
  host.innerHTML=`<div class="expense-widget-state" role="status">${tHtml("dashboard:loadingInvoices")}</div>`;
  const openEvent=id=>{ctx.state.expenseSelected=id;ctx.navigate('expenses');};
  const load=async(refresh=true)=>{
    try{
      const allItems=refresh?await ctx.api('/expenses'):cachedItems;cachedItems=allItems;
      const available=[...new Set(allItems.map(e=>e.currency||'RUB'))];
      const currency=available.includes(currencyChoice)?currencyChoice:(available[0]||'RUB');currencyChoice=currency;
      const items=allItems.filter(e=>(e.currency||'RUB')===currency);
      if(disposed||!host.isConnected)return;
      const debts=items.map(personalDebts),cost=items.reduce((n,e)=>n+(e.my_cost??e.cost.me),0),paid=items.reduce((n,e)=>n+(e.my_paid??e.paid.me),0);
      const incoming=debts.reduce((n,d)=>n+d.incoming,0),outgoing=debts.reduce((n,d)=>n+d.outgoing,0);
      const limit=size==='2x2'?4:2;
      host.innerHTML=`<div class="expense-widget expense-widget-view-${size} ${items.length?'':'is-empty'}">
        ${available.length>1?`<select data-expense-currency aria-label="${tHtml('expenses:currency')}">${available.map(id=>`<option value="${id}" ${id===currency?'selected':''}>${id}</option>`).join('')}</select>`:''}
        ${items.length?`<div class="expense-widget-summary" aria-label="${tHtml("dashboard:resultsForAllSavedInvoices")}">
          <div><span>${tHtml("dashboard:myShare2")}</span><strong>${amount(cost,currency)}</strong></div>
          <div><span>${tHtml("dashboard:theyOweMe")}</span><strong>${amount(incoming,currency)}</strong></div>
          <div><span>${tHtml("dashboard:iOwe")}</span><strong>${amount(outgoing,currency)}</strong></div>
          ${size!=='1x1'?`<div class="expense-widget-paid"><span>${tHtml("dashboard:iPaid")}</span><strong>${amount(paid,currency)}</strong></div>`:''}
        </div>
        <div class="widget-expense-list">${items.slice(0,limit).map(accountCard).join('')}</div>
        ${rich?`<div class="expense-widget-details">${categorySection(items,size==='2x2'?3:2,currency)}${size==='2x2'?debtSection(items):''}${recentSection(items,size==='2x2'?4:2)}</div>`:''}`:
        `<div class="expense-widget-empty"><span aria-hidden="true">₽</span><h3>${tHtml("dashboard:splitExpenses")}</h3><p>${tHtml("dashboard:createAnInvoiceAddPaymentsAndTrackYour")}</p></div>`}
        <div class="expense-widget-footer"><button type="button" class="secondary compact" data-start-expense aria-label="${tHtml("dashboard:startTheParty")}" title="${tHtml("dashboard:startTheParty")}">＋ <span class="expense-widget-start-label">${tHtml("dashboard:startTheParty")}</span></button><button type="button" class="text-link" data-open-expenses aria-label="${tHtml("dashboard:openAllInvoices")}">${tHtml("dashboard:allInvoices", {value0:(items.length?' · '+items.length:'')})}</button></div>
      </div>`;
      $('[data-expense-currency]',host)?.addEventListener('change',e=>{currencyChoice=e.target.value;load(false);});
      host.querySelectorAll('[data-expense-event]').forEach(b=>b.onclick=()=>openEvent(b.dataset.expenseEvent));
      $('[data-open-expenses]',host).onclick=()=>ctx.navigate('expenses');
      $('[data-start-expense]',host).onclick=()=>startExpense(ctx,e=>openEvent(e.id),ctx.instance.config);
    }catch(error){
      if(disposed||!host.isConnected)return;
      host.innerHTML=`<div class="expense-widget-state" role="status"><p>${esc(error.message)}</p><button type="button" class="secondary compact" data-retry-expenses>${tHtml("dashboard:tryLoadingAgain")}</button></div>`;
      $('[data-retry-expenses]',host).onclick=()=>{host.innerHTML=`<div class="expense-widget-state" role="status">${tHtml("dashboard:loadingInvoices")}</div>`;load();};
    }
  };
  load();
  return ()=>{disposed=true;};
}
