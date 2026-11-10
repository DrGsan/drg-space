import {tText,tHtml,locale} from "../../core/i18n.js";
import {$,esc,formBind} from '../../core/ui.js';
import {personName,personAvatar} from '../../core/people.js';

export async function managedFriends(ctx,user,reload){
  const dialog=ctx.modal(`<h2>${tHtml("admin:userSFriends")}</h2><p class="muted">${esc(personName(user.profile||{login:user.login}))} · @${esc(user.login)}</p><div data-admin-friends><p role="status">${tHtml("admin:loadingList")}</p></div>`);
  dialog.classList.add('admin-friends-dialog');
  try{
    const data=await ctx.api('/admin/users/'+user.id+'/friends');if(!dialog.isConnected)return;
    const selected=new Set(data.friend_ids),original=new Set(data.friend_ids),host=$('[data-admin-friends]',dialog);
    host.innerHTML=`<form><details class="admin-friend-picker" open><summary>${tHtml("admin:selectFriends")} <span data-friend-count></span></summary><div class="admin-friend-options"><label>${tHtml("admin:searchByNameOrLogin")}<input type="search" data-friend-search autocomplete="off" placeholder="${tHtml("admin:findPerson")}"></label><div class="admin-friend-list">${data.users.map(peer=>`<label class="admin-friend-option" data-friend-id="${esc(peer.id)}"><input type="checkbox" name="friend_ids" value="${esc(peer.id)}" ${selected.has(peer.id)?'checked':''}>${personAvatar(peer.profile||{login:peer.login})}<span><strong>${esc(personName(peer.profile||{login:peer.login}))}</strong><small>@${esc(peer.login)}</small></span></label>`).join('')}</div><p class="hint" data-friend-empty hidden>${tHtml("admin:noOneFound")}</p></div></details><p class="hint">${tHtml("admin:friendshipWillAppearForBothUsersWithoutInvitation")}</p><div data-friend-removals hidden><p class="hint danger" data-friend-remove-names></p><label class="check-label"><input type="checkbox" name="confirm_removals">${tHtml("admin:iConfirmTheDeletionOfFriendshipAndShared")}</label></div><p class="hint" role="status" data-friend-status></p><button type="submit" class="primary">${tHtml("admin:saveFriends")}</button></form>`;
    const form=$('form',host),rows=[...host.querySelectorAll('[data-friend-id]')];
    function update(){
      $('[data-friend-count]',host).textContent=`· ${selected.size}`;
      const removed=[...original].filter(id=>!selected.has(id));$('[data-friend-removals]',host).hidden=!removed.length;
      $('[name=confirm_removals]',host).required=!!removed.length;
      $('[name=confirm_removals]',host).checked=false;
      $('[data-friend-remove-names]',host).textContent=removed.length?tText("admin:deletingFriendshipWithTheSharedWorkspaceForEach", {value0:(removed.map(id=>personName(data.users.find(peer=>peer.id===id)?.profile||{})).join(', '))}):'';
    }
    rows.forEach(row=>$('input',row).onchange=event=>{event.target.checked?selected.add(row.dataset.friendId):selected.delete(row.dataset.friendId);update();});
    $('[data-friend-search]',host).oninput=event=>{
      const query=event.target.value.trim().toLocaleLowerCase(locale()).replaceAll("ё","е");let visible=0;
      rows.forEach(row=>{row.hidden=!row.textContent.toLocaleLowerCase(locale()).replaceAll("ё","е").includes(query);if(!row.hidden)visible++;});
      $('[data-friend-empty]',host).hidden=!!visible;
    };
    update();
    formBind(form,async values=>{
      const controls=[...form.querySelectorAll('input')];controls.forEach(input=>input.disabled=true);
      try{
        const result=await ctx.api('/admin/users/'+user.id+'/friends','PUT',{friend_ids:[...selected],version:data.version,confirm_removals:values.has('confirm_removals')});
        $('.modal-close',dialog).click();await reload();ctx.toast(tText("admin:friendsSavedAddedDeleted", {value0:(result.added), value1:(result.removed)}));
      }finally{controls.forEach(input=>input.disabled=false);}
    });
  }catch(error){if(dialog.isConnected)$('[data-admin-friends]',dialog).innerHTML=`<p role="status">${esc(error.message)}</p>`;}
}
