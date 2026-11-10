import {tText,tHtml} from "../../core/i18n.js";
import {$,esc} from '../../core/ui.js';
import {personAvatar,personName,personLabel} from '../../core/people.js';
import './access-menu.css';
import {audienceMarkup,bindAudience,accessExplanation} from './audience.js';
let closeActive;
const roles=[['editor',tText("notes:editing"),tText("notes:editContentAndComments")],['commenter',tText("notes:commenting"),tText("notes:readAndComment")],['viewer',tText("notes:view"),tText("notes:readOnlyPage")]];
export function permissionsDialog(page,ctx,onChanged){
  closeActive?.();
  const anchor=document.querySelector('#page-participants')||document.querySelector('#share-page')||document.querySelector('#page-actions');
  const overlay=document.createElement('div');overlay.className='note-access-overlay';
  overlay.innerHTML=`<section class="note-access-popover" role="dialog" aria-modal="true" aria-label="${tHtml("notes:accessToPage")}"><header class="note-access-tabs"><button class="active" type="button" aria-current="page">${tHtml("notes:access")}</button><button type="button" data-access-publish>${tHtml("notes:publication")}</button><button type="button" class="modal-close" aria-label="${tHtml("notes:close")}">×</button></header><div class="note-access-body" aria-live="polite"><p class="hint">${tHtml("notes:loadingParticipants")}</p></div><footer class="note-access-footer"><span>${tHtml("notes:byInvitation")}</span><button type="button" class="secondary compact" data-copy-page-link>${tHtml("notes:copyLink")}</button></footer><p class="note-access-error error" role="alert"></p></section>`;
  const panel=$('.note-access-popover',overlay),body=$('.note-access-body',overlay),error=$('.note-access-error',overlay);
  let disposed=false,roleMenu,opener=document.activeElement,data,options=[],busy=false,audience;
  const place=()=>{
    const rect=anchor?.getBoundingClientRect(),width=Math.min(456,innerWidth-24);
    panel.style.width=width+'px';panel.style.left=Math.max(12,Math.min((rect?.right||innerWidth-12)-width,innerWidth-width-12))+'px';
    panel.style.top=Math.max(12,Math.min((rect?.bottom||64)+8,innerHeight-panel.offsetHeight-12))+'px';
    closeRoles();
  };
  function closeRoles(){roleMenu?.remove();roleMenu=null;panel.querySelectorAll('[aria-expanded=true]').forEach(b=>b.setAttribute('aria-expanded','false'));}
  function close(){if(disposed)return;disposed=true;closeRoles();overlay.remove();window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);document.removeEventListener('keydown',keys,true);window.removeEventListener('popstate',close);if(closeActive===close)closeActive=null;if(opener?.isConnected)opener.focus();}
  function keys(e){
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();if(roleMenu){const button=panel.querySelector('[aria-expanded=true]');closeRoles();button?.focus();}else close();}
    if(e.key==='Tab'){
      const controls=[...(roleMenu||panel).querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),summary')].filter(el=>el.getClientRects().length);
      const first=controls[0],last=controls.at(-1);
      if(!controls.includes(document.activeElement)){e.preventDefault();(e.shiftKey?last:first)?.focus();return;}
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
    }
  }
  overlay.onpointerdown=e=>{if(e.target===overlay)close();else if(roleMenu&&!roleMenu.contains(e.target)&&!e.target.closest('[data-access-role]'))closeRoles();};
  $('.modal-close',overlay).onclick=close;
  $('[data-access-publish]',overlay).onclick=()=>{close();ctx.sharePanel(page.id,{database:page.kind==='database'});};
  $('[data-copy-page-link]',overlay).onclick=async()=>{try{await navigator.clipboard.writeText(new URL('/page/'+page.id,location.origin).href);ctx.toast(tText("notes:linkCopiedItIsOnlyAccessibleToPage"));}catch{error.textContent=tText("notes:failedToCopyLink");}};
  async function action(path,method,payload){
    if(busy)return;busy=true;error.textContent='';closeRoles();panel.querySelectorAll('button,input,select').forEach(el=>el.disabled=true);
    try{await ctx.api('/notes/'+page.id+path,method,payload);await onChanged?.();await load();}
    catch(e){if(!disposed){error.textContent=e.message;await load(false);}}
    finally{busy=false;if(!disposed)panel.querySelectorAll('.note-access-tabs button,[data-copy-page-link]').forEach(el=>el.disabled=false);}
  }
  function openRoles(user,button){
    const already=button.getAttribute('aria-expanded')==='true';closeRoles();if(already)return;
    roleMenu=document.createElement('div');roleMenu.className='note-access-role-menu';roleMenu.setAttribute('role','menu');roleMenu.setAttribute('aria-label',tText("notes:permissions", {value0:(personName(user))}));
    roleMenu.innerHTML=`<p>${tHtml("notes:accessLevel")}</p>${roles.map(([key,label,description])=>`<button type="button" role="menuitemradio" aria-checked="${user.role===key}" data-set-role="${key}"><span><strong>${label}</strong><small>${description}</small></span><span aria-hidden="true">${user.role===key?'✓':''}</span></button>`).join('')}<hr><button type="button" role="menuitem" class="note-access-remove" data-remove-access><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg><span>${tHtml("notes:removeAccess")}</span></button>`;
    overlay.append(roleMenu);button.setAttribute('aria-expanded','true');
    const rect=button.getBoundingClientRect(),width=Math.min(276,innerWidth-24);
    roleMenu.style.width=width+'px';roleMenu.style.left=Math.max(12,Math.min(rect.right-width,innerWidth-width-12))+'px';roleMenu.style.top=Math.max(12,Math.min(rect.bottom+5,innerHeight-roleMenu.offsetHeight-12))+'px';
    roleMenu.querySelectorAll('[data-set-role]').forEach(b=>b.onclick=()=>{if(b.dataset.setRole===user.role){closeRoles();button.focus();return;}action('/permissions','PUT',{user_id:user.id,role:b.dataset.setRole});});
    $('[data-remove-access]',roleMenu).onclick=()=>action('/members/'+encodeURIComponent(user.id),'DELETE');
    roleMenu.onkeydown=e=>{if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key))return;e.preventDefault();const items=[...roleMenu.querySelectorAll('button')],index=items.indexOf(document.activeElement);items[e.key==='Home'?0:e.key==='End'?items.length-1:(index+(e.key==='ArrowDown'?1:items.length-1))%items.length].focus();};
    roleMenu.querySelector('[aria-checked=true]')?.focus();
  }
  async function load(resetError=true){
    if(disposed)return;if(resetError)error.textContent='';
    try{
      data=await ctx.api('/notes/'+page.id+'/permissions');
      if(disposed)return;
      $('[data-access-publish]',overlay).hidden=!!data.work_space&&!data.manage;
      if(data.manage&&page.space_id){try{options=await ctx.api('/notes/'+page.id+'/member-options');}catch{options=[];}}
      audience=data.manage&&data.audience_supported&&(data.advanced||data.has_audience)?await ctx.api('/notes/'+page.id+'/audience'):null;
      if(disposed)return;
      const inviteLabel=audience?tText("notes:selectParticipants"):tText("notes:selectFriends");
      body.innerHTML=`${data.manage&&page.space_id?`<form class="note-access-invite"><details class="note-access-friends"><summary>${inviteLabel}</summary><div class="note-access-friend-list">${options.map(u=>`<label><input type="checkbox" name="friend" value="${esc(u.login)}">${personAvatar(u)}<span><strong>${personLabel(u)}</strong><small>@${esc(u.login)}</small></span></label>`).join('')||`<p class="hint">${tHtml("notes:noParticipantsAvailableYet")}</p>`}</div></details><select name="role" aria-label="${tHtml("notes:permissionsForNewParticipants")}">${roles.map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select><button type="submit" class="primary">${tHtml("notes:add")}</button></form>`:''}${data.work_space?`<p class="hint">${tHtml("notes:projectOrOrganizationRolesAreAssignedInPeople")}</p>`:''}${audienceMarkup(audience)}<div class="note-access-members">${data.members.map(user=>`<div class="note-access-member">${personAvatar(user)}<div class="note-access-person"><strong>${personLabel(user)}${user.id===ctx.state.me.id?` <span>${tHtml("notes:you")}</span>`:''}</strong><small>@${esc(user.login)}${user.owner?` · <span class="page-owner-label">${data.work_space?tText("notes:author"):tText("notes:owner")}</span>`:''}</small>${user.sources?.length?`<small class="note-access-source">${esc(accessExplanation(user))}</small>`:''}</div>${user.manager?`<span class="note-access-manager">${tHtml("notes:fullAccess")}</span>`:`<button type="button" class="note-access-role" data-access-role="${esc(user.id)}" aria-haspopup="menu" aria-expanded="false" ${data.manage&&!user.protected?'':'disabled'}>${roles.find(r=>r[0]===user.role)?.[1]||tText("notes:view")}<span aria-hidden="true">⌄</span></button>`}</div>`).join('')}</div>${!page.space_id?`<p class="hint">${tHtml("notes:thisPageIsOnlyAccessibleToYou")}</p>`:`<p class="note-access-note">${tHtml("notes:accessExtendsToThisPageAndItsSubpages")}</p>`}`;
      body.querySelectorAll('[data-access-role]').forEach(b=>b.onclick=()=>openRoles(data.members.find(u=>u.id===b.dataset.accessRole),b));
      bindAudience(body,audience,value=>action('/audience','PUT',value),value=>ctx.api('/notes/'+page.id+'/audience/preview','POST',value));
      const form=$('form.note-access-invite',body);if(form){const submit=form.querySelector('[type=submit]');submit.disabled=true;form.onchange=()=>{const count=form.querySelectorAll('[name=friend]:checked').length;submit.disabled=!count;form.querySelector('summary').textContent=count?(audience?(""+tText("notes:participantsSelected")+" "):(""+tText("notes:friendsSelected")+" "))+count:inviteLabel;};}if(form)form.onsubmit=e=>{e.preventDefault();const logins=[...form.querySelectorAll('[name=friend]:checked')].map(input=>input.value);if(logins.length)action('/members','POST',{logins,role:form.elements.role.value});};
      place();
    }catch(e){if(!disposed)error.textContent=e.message;}
  }
  closeActive=close;window.addEventListener('popstate',close);document.body.append(overlay);place();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);document.addEventListener('keydown',keys,true);
  load().then(()=>{if(!disposed)(body.querySelector('summary')||body.querySelector('select')||$('.modal-close',overlay)).focus();});
  return close;
}
