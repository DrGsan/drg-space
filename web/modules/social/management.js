import {tText,tHtml} from "../../core/i18n.js";
import {editNickname} from '../../core/nickname-editor.js';
import {auditLog} from './lifecycle.js';
import {$,esc} from '../../core/ui.js';
import {personName} from '../../core/people.js';

import {peopleTable,teamsTable} from './tables.js';
export function manageSpace(ctx,space,changed){
  const dialog=ctx.modal(`<h2>${tHtml("social:workspaceManagement")}</h2><p>${esc(space.name)}</p><nav class="space-management-tabs" aria-label="${tHtml("social:workspaceManagement")}"></nav><div data-space-management></div><p class="error" role="alert" data-management-error></p>`);
  dialog.classList.add('space-management-dialog');
  const host=$('[data-space-management]',dialog),error=$('[data-management-error]',dialog),tabs=$('.space-management-tabs',dialog);
  let state,tab='members',busy=false,activeTeam;
  const peopleView={},teamsView={},selected=new Set();
  const path='/social/spaces/'+space.id;
  const config=()=>({revision:state.revision,advanced:state.advanced,admins:[...state.admins],teams:structuredClone(state.teams)});
  async function load(){state=await ctx.api(path+'/management');state.me_id=ctx.state.me.id;if(dialog.isConnected)render();}
  async function action(value,ownership=false){
    if(busy)return;busy=true;error.textContent='';dialog.querySelectorAll('button,input,select').forEach(el=>el.disabled=true);
    try{await ctx.api(path+(ownership?'/ownership':'/management'),ownership?'POST':'PUT',value);await changed();await load();}
    catch(e){if(dialog.isConnected){error.textContent=e.message;await load().catch(()=>{});}}
    finally{busy=false;const close=$('.modal-close',dialog);if(close)close.disabled=false;}
  }
  function render(){
    if(!state.manage&&tab==='settings')tab='members';
    tabs.innerHTML=[['members',tText("social:members")],...(state.advanced_available&&state.advanced?[['teams',tText("social:teams2")]]:[]),...(state.manage?[['audit',tText("social:log")],['settings',tText("social:settings")]]:[])].map(([id,label])=>`<button type="button" data-space-tab="${id}" ${tab===id?'aria-current="page"':''}>${label}</button>`).join('');
    tabs.querySelectorAll('button').forEach(b=>b.onclick=()=>{tab=b.dataset.spaceTab;error.textContent='';render();});
    if(tab==='members'){
      host.innerHTML=`<div data-management-people></div><p class="hint">${tHtml("social:participantCanWorkWithAvailableNotesAndThe")}</p>`;
      const bulk=state.manage&&state.advanced_available&&state.advanced&&Object.keys(state.teams).length;
      let selection;
      if(bulk){
        for(const uid of selected)if(!state.members.some(u=>u.id===uid))selected.delete(uid);
        host.insertAdjacentHTML('afterbegin',`<form class="space-bulk-actions"><span data-bulk-count aria-live="polite"></span><select name="team" aria-label="${tHtml("social:teamForSelectedParticipants")}">${Object.entries(state.teams).map(([id,t])=>`<option value="${esc(id)}">${esc(t.name)}</option>`).join('')}</select><button class="secondary compact" type="submit" name="operation" value="add">${tHtml("social:addToTeam")}</button><button class="secondary compact" type="submit" name="operation" value="remove">${tHtml("social:removeFromTeam")}</button><button type="button" class="text-link" data-clear-selection>${tHtml("social:deselect")}</button></form>`);
        const form=$('.space-bulk-actions',host);
        selection={ids:selected,compact:false,changed:()=>{$('[data-bulk-count]',host).textContent=tText("social:selected", {value0:(selected.size)});form.querySelectorAll('[type=submit]').forEach(b=>b.disabled=!selected.size);}};selection.changed();
        $('[data-clear-selection]',host).onclick=()=>{selected.clear();render();};
        form.onsubmit=e=>{e.preventDefault();if(!selected.size)return;const value=config(),team=value.teams[form.elements.team.value],ids=new Set(team.members);for(const uid of selected){if(e.submitter?.value==='remove')ids.delete(uid);else ids.add(uid);}team.members=[...ids];action(value);};
      }
      peopleTable($('[data-management-people]',host),state,{view:peopleView,selection,onName:person=>editNickname(ctx,person,async()=>{await changed();await load();}),onRole:state.role==='owner'?(uid,role)=>{const value=config();value.admins=value.admins.filter(id=>id!==uid);if(role==='admin')value.admins.push(uid);action(value);}:null});
    }else if(tab==='teams'){
      if(activeTeam&&!state.teams[activeTeam])activeTeam=null;
      if(activeTeam){
        const id=activeTeam,team=state.teams[id],selection={ids:new Set(team.members),disabled:!state.manage};
        host.innerHTML=`<button type="button" class="text-link" data-back-teams>${tHtml("social:allTeams")}</button><form data-edit-team="${esc(id)}" class="space-team-editor"><label>${tHtml("social:teamName")}<input name="name" maxlength="80" value="${esc(team.name)}" required ${state.manage?'':'disabled'}></label><p class="hint" data-team-selected></p><div data-team-people></div>${state.manage?`<div class="space-team-actions"><button class="primary compact" type="submit">${tHtml("social:saveTeam")}</button><button type="button" class="text-link danger-link" data-delete-team>${tHtml("social:deleteTeam")}</button></div>`:''}</form>`;
        const form=$('[data-edit-team]',host);
        $('[data-back-teams]',host).onclick=()=>{activeTeam=null;render();};
        selection.changed=()=>{$('[data-team-selected]',host).textContent=tText("social:selectedParticipants2", {value0:(selection.ids.size)});};selection.changed();
        peopleTable($('[data-team-people]',host),state,{selection});
        form.onsubmit=e=>{e.preventDefault();const value=config();value.teams[id]={name:new FormData(form).get('name'),members:[...selection.ids]};action(value);};
        $('[data-delete-team]',host)?.addEventListener('click',()=>{if(!confirm(tText("social:deleteTeamAccessGrantedThroughItWillBe")))return;const value=config();delete value.teams[id];action(value);});
      }else{
        host.innerHTML=`${state.manage?`<form data-create-team class="space-team-create"><label>${tHtml("social:teamName")}<input name="name" maxlength="80" placeholder="${tHtml("social:enterName")}" required></label><button class="primary compact" type="submit">${tHtml("social:createTeam")}</button></form>`:''}<div data-management-teams></div>`;
        teamsTable($('[data-management-teams]',host),state,id=>{activeTeam=id;render();},teamsView);
        const create=$('[data-create-team]',host);if(create)create.onsubmit=e=>{e.preventDefault();const value=config(),id=crypto.randomUUID().replaceAll('-','').slice(0,16);value.advanced=true;value.teams[id]={name:new FormData(create).get('name'),members:[]};activeTeam=id;action(value);};
      }
    }else if(tab==='audit'){
      auditLog(host,ctx,space).catch(e=>{error.textContent=e.message;});
    }else{
      const others=state.members.filter(u=>u.id!==state.owner_id);
      host.innerHTML=`${state.advanced_available?`<label class="check-label"><input type="checkbox" data-space-advanced ${state.advanced?'checked':''} ${state.manage?'':'disabled'}>${tHtml("social:extendedView")}</label><p class="hint">${tHtml("social:displaysTeamsAndDetailedManagementTurningOffDoes")}</p>`:''}${state.role==='owner'?`<form data-transfer-space><h3>${tHtml("social:transferOwnership")}</h3><label>${tHtml("social:newOwner")}<select name="user_id" aria-label="${tHtml("social:newOwner")}" required><option value="">${tHtml("social:selectParticipant")}</option>${others.map(u=>`<option value="${esc(u.id)}">${esc(personName(u))} · @${esc(u.login)}</option>`).join('')}</select></label><p class="hint">${tHtml("social:youWillRemainAnAdministratorOfTheWorkspace")}</p><button class="secondary compact" type="submit" ${others.length?'':'disabled'}>${tHtml("social:transferOwnership")}</button></form>`:''}`;
      const advanced=$('[data-space-advanced]',host);if(advanced)advanced.onchange=e=>{const value=config();value.advanced=e.target.checked;action(value);};
      const transfer=$('[data-transfer-space]',host);if(transfer)transfer.onsubmit=e=>{e.preventDefault();if(confirm(tText("social:transferOwnershipToTheSelectedParticipantYouWill")))action({revision:state.revision,user_id:new FormData(transfer).get('user_id')},true);};
    }
  }
  load().catch(e=>{if(dialog.isConnected)error.textContent=e.message;});
}
