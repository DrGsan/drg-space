import {tText,tHtml} from "../../core/i18n.js";
import {catalog} from '../../core/catalog.js';
import {editNickname} from '../../core/nickname-editor.js';
import {personForLogin} from '../../core/person-names.js';
import {deletionScreen,deletionPeriod,offboardMember,invitationLinks} from './lifecycle.js';
import {preferenceSections} from '../../core/preferences.js';
import {sectionHeader} from '../../core/section-header.js';
import './social.css';
import {spaceAvatar} from '../../core/space-avatar.js';
import {spaceTemplates,spaceTemplate} from '../../core/space-templates.js';
import {editFamilyAvatar} from './family-avatar.js';
import {manageSpace} from './management.js';
import {peopleTable} from './tables.js';
import {personAvatar,personPresence,personName} from '../../core/people.js';
import { $, esc, formBind } from '../../core/ui.js';
import {mountFederationPeople} from '../federation/people.js';
const mounts=new WeakMap(),tableViews=new WeakMap();
export async function mountSocial(host,ctx){
  if(!tableViews.has(host))tableViews.set(host,new Map());
  const views=tableViews.get(host);
  const sequence=(mounts.get(host)||0)+1;mounts.set(host,sequence);
  host.innerHTML=`<p class="muted">${tHtml("social:loadingPeopleAndGroups")}</p>`;sectionHeader(host,ctx);
  try {
    const data=await ctx.api('/social');if(!host.isConnected||mounts.get(host)!==sequence)return;
    ctx.state.spaces=data.spaces;
    const spaces=data.spaces.filter(s=>['family','friends'].includes(s.kind));
    const card=(s,index)=>`<article class="panel social-card" data-social-panel="${index}">${s.deletion?'<div data-space-deletion="'+esc(s.id)+'"></div>':`${s.kind==='family'?`<div data-space-people="${esc(s.id)}"></div>`:`<div class="social-members">${s.members.filter(m=>m.id!==ctx.state.me.id).map(m=>`<div class="social-member">${personAvatar(m)}<span><strong>${m.id!==ctx.state.me.id?`<button type="button" class="text-link person-name-button" data-person-name="${esc(m.id)}" aria-label="${tHtml("social:personalNameFor", {value0:(esc(m.login))})}" title="${tHtml("social:changeYourOwnName")}">${esc(personName(m))}</button>`:esc(personName(m))}</strong><small>@${esc(m.login)}</small>${personPresence(m)}</span></div>`).join('')}</div>`}<div class="social-actions">${ctx.moduleEnabled('notes')?`<button class="primary compact" data-space-notes="${esc(s.id)}">${tHtml("social:sharedNotes")}</button>`:''}${ctx.moduleEnabled('chat')?`<button class="secondary compact" data-space-chat="${esc(s.id)}">${tHtml("social:chat")}</button>`:''}${s.kind==='family'&&(s.manage??s.owner)?`<button class="text-link" data-invite="${esc(s.id)}">${tHtml("social:participant")}</button><button class="text-link" data-rename="${esc(s.id)}">${tHtml("social:rename")}</button><button class="text-link" data-family-avatar="${esc(s.id)}">${(!s.template||s.template==='family')?tText("social:familyPhoto"):tText("social:spacePhoto")}</button>`:''}${s.kind==='family'?`<button class="text-link" data-manage-space="${esc(s.id)}">${(s.manage??s.owner)?tText("social:administration"):['project','organization'].includes(s.template)?tText("social:participantsAndTeams"):tText("social:members")}</button>`:''}</div><button class="text-link danger-link" data-leave="${esc(s.id)}">${s.kind==='friends'?tText("social:removeFromFriends"):s.owner?tText("social:deleteWorkspace"):tText("social:exitWorkspace")}</button>`}</article>`;
    const pending=data.requests.map(q=>`<div class="social-request"><div><strong>${esc(personName(personForLogin(q.incoming?q.sender:q.recipient)))}</strong><p class="hint">${q.kind==='family'?tText("social:space", {value0:(esc(q.space_name))}):tText("social:addToFriends")} · ${q.incoming?tText("social:invitingYou"):tText("social:invitationSent")}</p></div><div>${q.incoming?`<button class="primary compact" data-accept="${esc(q.id)}">${tHtml("social:accept")}</button>`:''}<button class="secondary compact" data-decline="${esc(q.id)}">${q.incoming?tText("social:decline"):tText("social:cancel")}</button></div></div>`).join('');
    host.innerHTML=`<div class="page-heading preferences-heading social-heading"><h1>${tHtml("social:peopleAndGroups")}</h1><button id="refresh-social" class="icon-btn" aria-label="${tHtml("social:updateInvitations")}" title="${tHtml("social:updateInvitations")}"><span aria-hidden="true">↻</span></button></div><section class="social-view">${pending?`<section class="preference-group"><h2 class="preference-group-title">${tHtml("social:invitations", {value0:(data.requests.length)})}</h2><div class="preference-rows social-requests">${pending}</div></section>`:''}<div class="social-sections">${spaces.map(card).join('')}<form id="create-space" class="panel social-create"><label>${tHtml("social:spaceType")}<select name="template" aria-label="${tHtml("social:spaceType")}">${Object.entries(spaceTemplates).map(([id,t])=>`<option value="${id}" ${id==='group'?'selected':''}>${t.label}</option>`).join('')}</select></label><label>${tHtml("social:spaceName")}<input name="name" maxlength="80" required placeholder="${tHtml("social:enterName")}"></label><p class="hint">${tHtml("social:sharedNotesAndConversationsForSelectedParticipantsAdding")}</p><button class="primary compact" type="submit">${tHtml("social:createWorkspace")}</button></form><form id="add-friend" class="panel social-create"><label>${tHtml("social:friendSLogin")}<input name="login" minlength="3" maxlength="40" required placeholder="login"></label><button class="secondary compact" type="submit">${tHtml("social:inviteFriend")}</button></form></div><p class="hint social-help">${tHtml("social:personalNotesAreOnlyVisibleToYouAccess", {value0:(!ctx.moduleEnabled('chat')?(" "+tText("social:theChatCanBeEnabledInModuleSettings")+""):'')})}</p></section>`;
    const descriptors=spaces.map((s,index)=>{
      const other=s.members.find(m=>m.id!==ctx.state.me.id);
      return {id:'space:'+s.id,selector:`[data-social-panel="${index}"]`,group:s.kind==='family'?tText("social:workspaces"):tText("social:contacts"),title:s.kind==='family'?s.name:personName(other),description:s.kind==='family'?tText("social:participantsNotes", {value0:(spaceTemplate(s).label), value1:(s.members.length), value2:(s.notes), value3:(s.owner?(" "+tText("social:youAreTheOwner")+""):'')}):tText("social:sharedNotes2", {value0:(other?.login||''), value1:(s.notes)}),icon:s.kind==='family'?spaceTemplate(s).icon:'users-round'};
    }).sort((a,b)=>(a.group===tText("social:workspaces")?0:1)-(b.group===tText("social:workspaces")?0:1));
    preferenceSections($('.social-sections',host),[...descriptors,
      {id:'create-space',selector:'#create-space',group:tText("social:add"),title:tText("social:createWorkspace"),description:tText("social:familyFriendsProjectOrOrganization"),icon:'users-round'},
      {id:'add-friend',selector:'#add-friend',group:tText("social:add"),title:tText("social:addAFriend"),description:tText("social:inviteViaUserLogin"),icon:'user-round-plus'},
    ],{userId:ctx.state.me.id,scope:'social'});
    spaces.forEach((space,index)=>{
      const row=$(`[data-social-panel="${index}"]`,host).closest('.preference-row');row.dataset.socialName=space.name;
      row.dataset.catalogFilter=space.template||'family';
      const label=$('.preference-label',row),description=$('.preference-description',row);
      description.textContent=space.kind==='family'?spaceTemplate(space).label:'@'+(space.members.find(m=>m.id!==ctx.state.me.id)?.login||'');
      const facts=document.createElement('span');facts.className='social-catalog-facts';
      if(space.kind==='friends')row.classList.add('social-contact-row');
      facts.innerHTML=`<span>${space.members.length}<small>${tHtml("social:participants")}</small></span><span>${space.notes}<small>${tHtml("social:notes")}</small></span><span>${space.deletion?tText("social:waitingToDelete"):space.owner?tText("social:owner"):space.role==='admin'?tText("social:administrator"):tText("social:member")}</span>`;
      if(space.kind==='friends')facts.innerHTML=`<span>${space.deletion?tText("social:waitingToDelete"):personPresence(space.members.find(m=>m.id!==ctx.state.me.id))}</span><span>${space.notes}<small>${tHtml("social:notes")}</small></span>`;
      label.after(facts);

      const icon=$('.preference-icon',row);icon.classList.add('social-person-icon');icon.innerHTML=spaceAvatar(space,ctx.state.me);
    });
    for(const group of host.querySelectorAll('.social-sections > .preference-group')){
      const items=[...group.querySelectorAll('[data-social-name]')];if(!items.length)continue;
      group.classList.add('social-catalog');
      const rows=$('.preference-rows',group),heading=document.createElement('div');heading.className='social-catalog-head';
      heading.innerHTML=`<span>${tHtml("social:name")}</span><span>${tHtml("social:members")}</span><span>${tHtml("social:notes2")}</span><span>${tHtml("social:yourRoleStatus")}</span>`;rows.prepend(heading);
      if(group.querySelector('h2').textContent===tText("social:contacts")){group.classList.add('social-contact-catalog');heading.innerHTML=`<span>${tHtml("social:person")}</span><span>${tHtml("social:activityStatus")}</span><span>${tHtml("social:sharedNotes")}</span>`;}
      const key='catalog:'+group.querySelector('h2').textContent;if(!views.has(key))views.set(key,{});
      catalog(group,{items,view:views.get(key),search:item=>{const space=spaces.find(s=>s.name===item.dataset.socialName&&'space:'+s.id===item.dataset.preferenceKey);return item.querySelector('summary').textContent+' '+(space?.members||[]).map(p=>personName(p)+' '+p.login).join(' ');},label:group.querySelector('h2').textContent===tText("social:contacts")?tText("social:contactSearch"):tText("social:searchForWorkspaces"),filters:group.querySelector('h2').textContent===tText("social:workspaces")?Object.entries(spaceTemplates).map(([value,t])=>({value,label:t.label})):[]});
    }
    const refresh=async()=>{ctx.state.notes=ctx.moduleEnabled('notes')?await ctx.api('/notes'):[];await mountSocial(host,ctx);};
    const federationHost=document.createElement('div');federationHost.className='federation-social-host';$('.social-view',host).append(federationHost);
    mountFederationPeople(federationHost,ctx);
    host.querySelectorAll('[data-space-deletion]').forEach(el=>deletionScreen(el,spaces.find(s=>s.id===el.dataset.spaceDeletion),ctx,refresh,{compact:true}));
    const act=async fn=>{try{await fn();await refresh();}catch(e){ctx.toast(e.message);}};
    host.querySelectorAll('[data-space-people]').forEach(table=>{
      const space=spaces.find(s=>s.id===table.dataset.spacePeople);
      if(!views.has(space.id))views.set(space.id,{});
      peopleTable(table,{...space,me_id:ctx.state.me.id},{view:views.get(space.id),onName:person=>editNickname(ctx,person,refresh),onRemove:(space.manage??space.owner)?uid=>offboardMember(ctx,space,uid,refresh):null});
    });
    host.querySelectorAll('.social-members [data-person-name]').forEach(b=>b.onclick=()=>{const person=spaces.flatMap(s=>s.members).find(p=>p.id===b.dataset.personName);editNickname(ctx,person,refresh);});
    const createForm=$('#create-space',host);
    formBind(createForm,async f=>{await ctx.api('/social/spaces','POST',{name:f.get('name'),template:f.get('template')});await refresh();});
    formBind($('#add-friend',host),async f=>{await ctx.api('/social/friends','POST',{login:f.get('login')});ctx.toast(tText("social:invitationSent"));await refresh();});
    $('#refresh-social',host).onclick=()=>refresh().catch(e=>ctx.toast(e.message));
    sectionHeader(host,ctx,{mobileActions:['#refresh-social']});
    host.querySelectorAll('[data-accept]').forEach(b=>b.onclick=()=>act(()=>ctx.api('/social/requests/'+b.dataset.accept+'/accept','POST')));
    host.querySelectorAll('[data-decline]').forEach(b=>b.onclick=()=>act(()=>ctx.api('/social/requests/'+b.dataset.decline,'DELETE')));
    host.querySelectorAll('[data-space-notes]').forEach(b=>b.onclick=()=>{ctx.state.notesSpace=b.dataset.spaceNotes;ctx.state.selected=null;ctx.navigate('notes');});
    host.querySelectorAll('[data-space-chat]').forEach(b=>b.onclick=()=>{ctx.state.chatSpace=b.dataset.spaceChat;ctx.navigate('chat',{chatList:false});});
    host.querySelectorAll('[data-invite]').forEach(b=>b.onclick=()=>{
      const el=ctx.modal(`<h2>${tHtml("social:inviteToWorkspace")}</h2><form id="invite-space"><label>${tHtml("social:participantLogin")}<input name="login" minlength="3" maxlength="40" required></label><p class="hint">${tHtml("social:theParticipantWillGainAccessAfterAcceptingThe")}</p><button class="primary" type="submit">${tHtml("social:sendInvitation")}</button></form><button type="button" class="secondary" data-invitation-links>${tHtml("social:inviteViaLink")}</button>`);
      $('[data-invitation-links]',el).onclick=()=>{const space=spaces.find(s=>s.id===b.dataset.invite);$('.modal-close',el).click();invitationLinks(ctx,space);};
      formBind($('#invite-space',el),async f=>{await ctx.api('/social/spaces/'+b.dataset.invite+'/invites','POST',{login:f.get('login')});$('.modal-close',el).click();await refresh();});
    });
    host.querySelectorAll('[data-manage-space]').forEach(b=>b.onclick=()=>manageSpace(ctx,spaces.find(s=>s.id===b.dataset.manageSpace),refresh));
    host.querySelectorAll('[data-family-avatar]').forEach(b=>b.onclick=()=>editFamilyAvatar(ctx,spaces.find(s=>s.id===b.dataset.familyAvatar),refresh));
    host.querySelectorAll('[data-rename]').forEach(b=>b.onclick=()=>{
      const space=data.spaces.find(s=>s.id===b.dataset.rename);
      const el=ctx.modal(`<h2>${tHtml("social:spaceName")}</h2><form id="rename-space"><label>${tHtml("social:newName")}<input name="name" maxlength="80" required value="${esc(space.name)}"></label><button class="primary" type="submit">${tHtml("social:save")}</button></form>`);
      formBind($('#rename-space',el),async f=>{await ctx.api('/social/spaces/'+space.id,'PATCH',{name:f.get('name')});$('.modal-close',el).click();await refresh();});
    });
    host.querySelectorAll('[data-leave]').forEach(b=>b.onclick=()=>{
      const space=data.spaces.find(s=>s.id===b.dataset.leave);const whole=space.kind==='friends'||space.owner;
      if(confirm(whole?tText("social:scheduleWorkspaceDeletionAllItsDataWillBe", {value0:(deletionPeriod(space))}):tText("social:exitWorkspaceYourAccessToSharedNotesAnd")))act(()=>ctx.api(whole?'/social/spaces/'+space.id:`/social/spaces/${space.id}/members/${ctx.state.me.id}`,'DELETE'));
    });
  }catch(e){if(host.isConnected&&mounts.get(host)===sequence){host.innerHTML=`<p class="muted">${esc(e.message)}</p>`;sectionHeader(host,ctx);}}
}
