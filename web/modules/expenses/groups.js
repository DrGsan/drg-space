import {tText,tHtml} from '../../core/i18n.js';
import {$,esc,formBind} from '../../core/ui.js';
export function groupTargets(bill){const names=Object.fromEntries(bill.participants.map(p=>[p.id,p.name]));return (bill.groups||[]).map(g=>({...g,name:g.members.map(id=>names[id]).join(' + '),isGroup:true}));}
export function selectTarget(selected,id,checked,bill){
  if(checked){for(const group of bill.groups||[]){if(group.id===id)group.members.forEach(member=>selected.delete(member));else if(group.members.includes(id))selected.delete(group.id);}selected.add(id);}else selected.delete(id);
}
export function includePayer(selected,payer,bill){
 if((bill.groups||[]).some(g=>g.members.includes(payer)&&selected.has(g.id)))selected.delete(payer);
 else selected.add(payer);
 return selected;
}
export function defaultTargets(bill){const grouped=new Set((bill.groups||[]).flatMap(g=>g.members));return [...(bill.groups||[]).map(g=>g.id),...bill.participants.filter(p=>p.id!=='unknown'&&!grouped.has(p.id)).map(p=>p.id)];}
export function manageGroups(ctx,bill,changed){
 const dialog=ctx.modal(`<h2>${tHtml('expenses:groups')}</h2><p class="hint">${tHtml('expenses:groupHint')}</p><div data-existing-groups></div><form data-add-group></form>`);
 function draw(){
  const grouped=new Set((bill.groups||[]).flatMap(g=>g.members)),free=bill.participants.filter(p=>p.id!=='unknown'&&!grouped.has(p.id));
  $('[data-existing-groups]',dialog).innerHTML=groupTargets(bill).map(g=>`<div class="share-row"><strong>${esc(g.name)}</strong><button type="button" class="text-link" data-remove-group="${g.id}">${tHtml('expenses:removeGroup')}</button></div>`).join('');
  const previous=$('[data-add-group]',dialog),form=document.createElement('form');form.dataset.addGroup='';previous.replaceWith(form);form.innerHTML=free.length<2?'':`<label>${tHtml('expenses:firstPartner')}<select name="first">${free.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label><label>${tHtml('expenses:secondPartner')}<select name="second">${free.map((p,i)=>`<option value="${p.id}" ${i===1?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><button class="primary" type="submit">${tHtml('expenses:addGroup')}</button>`;
  const save=async groups=>{bill=await ctx.api('/expenses/'+bill.id,'PUT',{title:bill.title,participants:bill.participants,retention_days:bill.retention_days,version:bill.version,groups});changed(bill);draw();};
  if(free.length>=2)formBind(form,async data=>{if(data.get('first')===data.get('second'))throw Error(tText('expenses:expenseGroupsInvalid'));await save([...(bill.groups||[]),{id:'pair_'+crypto.randomUUID().replaceAll('-','').slice(0,20),members:[data.get('first'),data.get('second')]}]);});
  dialog.querySelectorAll('[data-remove-group]').forEach(button=>button.onclick=async()=>{try{await save(bill.groups.filter(g=>g.id!==button.dataset.removeGroup));}catch(error){ctx.toast(error.message);}});
 }
 draw();return dialog;
}
