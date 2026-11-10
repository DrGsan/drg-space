import {tText,tHtml} from "../../core/i18n.js";
import {mountFamilyGroups} from './family-groups.js';
import {catalog,labelTableRows} from '../../core/catalog.js';
import {$,esc} from '../../core/ui.js';
import {preferenceStatus} from '../../core/preferences.js';
import {personAvatar,personName,personSearch} from '../../core/people.js';

export function mountFamilyPhotoAccess(host,ctx){
  if(ctx.state.me.role!=='owner'){host.textContent=tText("admin:availableToAdministrator");return;}
  const row=host.closest('.preference-row');
  let started=false,stopGroups;
  const load=async()=>{
    stopGroups?.();
    host.innerHTML=`<p role="status">${tHtml("admin:loadingUsersAndGroups")}</p>`;
    try{
      const data=await ctx.api('/family-photos/admin');if(!host.isConnected)return;
      const users=data.users.filter(u=>u.role!=='owner');
      const status=()=>preferenceStatus(host,tText("admin:access", {value0:(users.filter(u=>u.enabled).length)}));
      status();
      host.innerHTML=`<section data-fp-group-management></section><h2>${tHtml("admin:userAccess")}</h2><p class="hint fp-access-help">${tHtml("admin:selectThePersonAndAllowedGroupsAccessIs")}</p><div data-fp-users><div class="fp-catalog-head"><span>${tHtml("admin:user")}</span><span>${tHtml("admin:allowedGroups")}</span><span>${tHtml("admin:access2")}</span></div>${users.map(user=>`<details class="admin-account" data-fp-user="${esc(user.id)}"><summary class="admin-account-head">${personAvatar(user.profile||{login:user.login})}<span class="admin-account-copy"><strong>${esc(personName(user.profile||{login:user.login}))}</strong><small data-fp-user-description></small></span><span class="fp-catalog-groups" data-fp-user-groups></span><span class="tag fp-access-state" data-fp-user-state></span></summary><div class="admin-account-body"><form data-fp-admin-form><label class="fp-access-toggle"><span><strong>${tHtml("admin:accessToFamilyPhotos")}</strong><small>${tHtml("admin:showUserSection")}</small></span><input type="checkbox" name="enabled" aria-label="${tHtml("admin:allowFamilyPhotos")}"></label><div class="admin-accounts-scroll"><table class="admin-accounts-table fp-access-permissions"><thead><tr><th>${tHtml("admin:group")}</th><th>${tHtml("admin:view")}</th><th>${tHtml("admin:send")}</th></tr></thead><tbody>${data.groups.map(g=>`<tr data-fp-access-group="${esc(g.id)}"><td>${esc(g.title)}${g.delete_at?(" "+tText("admin:awaitingDeletion")+""):g.enabled===false?(" "+tText("admin:disabled2")+""):''}${g.copy_created?(" "+tText("admin:copy")+""):''}</td><td><input type="checkbox" name="group" value="${esc(g.id)}" aria-label="${tHtml("admin:view2", {value0:(esc(g.title))})}"></td><td><input type="checkbox" name="write_group" value="${esc(g.id)}" aria-label="${tHtml("admin:send2", {value0:(esc(g.title))})}" ${g.kind!=='supergroup'||g.copy_created?'data-fp-read-only="1"':''}></td></tr>`).join('')||'<tr><td colspan="3">'+(data.status?.available===false?tText("admin:familyPhotoArchiveIsTemporarilyUnavailableAccessCan"):tText("admin:noConnectedGroupsYet"))+'</td></tr>'}</tbody></table></div><div class="fp-access-actions"><button type="submit" class="primary">${tHtml("admin:saveAccess")}</button><p role="status" data-fp-grant-status></p></div></form></div></details>`).join('')||`<p class="hint">${tHtml("admin:noOtherUsersAreCurrentlyPresentCreateAn")}</p>`}</div>`;
      stopGroups=mountFamilyGroups($('[data-fp-group-management]',host),ctx,data,load);
      if(ctx.state.adminFamilyExportRequested){const gid=ctx.state.adminFamilyExportRequested;delete ctx.state.adminFamilyExportRequested;stopGroups.openExport(gid);}
      host.querySelectorAll('.fp-access-permissions').forEach(labelTableRows);
      for(const user of users){
        const item=[...host.querySelectorAll('[data-fp-user]')].find(el=>el.dataset.fpUser===user.id),form=$('form',item);
        const summary=()=>{
          $('[data-fp-user-description]',item).textContent='@'+user.login;
          $('[data-fp-user-groups]',item).textContent=user.enabled?(user.groups.map(id=>data.groups.find(group=>group.id===id)?.title||tText("admin:inactiveGroup")).join(', ')||tText("admin:noGroupsSelected")):'—';
          const badge=$('[data-fp-user-state]',item);badge.textContent=user.enabled?tText("admin:enabled2"):tText("admin:noAccess");badge.classList.toggle('is-enabled',user.enabled);
        };
        const groupState=()=>{form.querySelectorAll('[name=group]').forEach(input=>input.disabled=!form.elements.enabled.checked);form.querySelectorAll('[name=write_group]').forEach(input=>{const view=[...form.querySelectorAll('[name=group]')].find(view=>view.value===input.value);if(!view.checked)input.checked=false;input.disabled=!form.elements.enabled.checked||!view.checked||input.dataset.fpReadOnly==='1';});};
        const show=()=>{form.elements.enabled.checked=!!user.enabled;form.querySelectorAll('[name=group]').forEach(input=>input.checked=user.groups.includes(input.value));form.querySelectorAll('[name=write_group]').forEach(input=>input.checked=(user.write_groups||[]).includes(input.value));groupState();summary();};
        form.querySelectorAll('[name=group]').forEach(input=>input.onchange=groupState);
        form.elements.enabled.onchange=()=>{groupState();$('[data-fp-grant-status]',form).textContent='';};show();
        form.onsubmit=async e=>{
          e.preventDefault();const notice=$('[data-fp-grant-status]',form),values=new FormData(form),uid=user.id;
          if(values.has('enabled')&&!values.getAll('group').length){notice.textContent=tText("admin:selectAtLeastOneGroup");return;}
          notice.textContent=tText("admin:saving");
          const controls=[...form.querySelectorAll('input,select,button')];controls.forEach(control=>control.disabled=true);
          try{
            const saved=await ctx.api('/family-photos/admin/users/'+uid,'PUT',{enabled:values.has('enabled'),groups:values.getAll('group'),write_groups:values.getAll('write_group')});
            if(!host.isConnected)return;
            user.enabled=saved.enabled;user.groups=saved.groups;user.write_groups=saved.write_groups||[];show();status();
            notice.textContent=saved.enabled?tText("admin:accessSavedModuleEnabled"):tText("admin:accessRevokedModuleDisabled");
          }catch(error){if(host.isConnected)notice.textContent=error.message;}
          finally{controls.forEach(control=>control.disabled=false);groupState();}
        };
      }
      const list=$('[data-fp-users]',host),items=[...list.querySelectorAll('[data-fp-user]')];
      catalog(list,{items,label:tText("admin:searchForPhotoAccess"),search:item=>{const user=users.find(u=>u.id===item.dataset.fpUser);return personSearch(user.profile||user)+' '+user.login;}});
    }catch(error){
      if(!host.isConnected)return;
      preferenceStatus(host,error.status===404?tText("admin:notConfigured"):tText("admin:notAvailable"));
      host.innerHTML=`<p role="status">${esc(error.message)}</p><button type="button" class="secondary" data-fp-admin-retry>${tHtml("admin:retry")}</button>`;
      $('[data-fp-admin-retry]',host).onclick=load;
    }
  };
  const open=()=>{if(row.open&&!started){started=true;load();}};
  row.addEventListener('toggle',open);open();
}
