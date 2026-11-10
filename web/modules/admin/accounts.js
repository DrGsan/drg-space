import {tText,tHtml} from "../../core/i18n.js";
import {mountAvatarPresets} from '../../core/avatar-presets.js';
import {prepareAvatar,avatarDataUrl,avatarHint} from '../../core/avatar.js';
import {$,esc,formBind} from '../../core/ui.js';
import {personAvatar,personName} from '../../core/people.js';
import {avatarMarkup,displayName} from '../profile/index.js';
import {requireStrongPassword} from '../../core/passwords.js';
const passwordFields=`<label>${tHtml("admin:userPassword")}<input type="password" name="password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label>${tHtml("admin:reEnterUserPassword")}<input type="password" name="repeat_password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label class="check-label"><input type="checkbox" data-show-user-password>${tHtml("admin:showUserPassword")}</label>`;
function bindPasswords(el){$('[data-show-user-password]',el).onchange=e=>el.querySelectorAll('[name=password],[name=repeat_password]').forEach(input=>input.type=e.target.checked?'text':'password');}
function userPassword(form){const password=form.get('password');requireStrongPassword(password);if(password!==form.get('repeat_password'))throw new Error(tText("admin:userPasswordsDoNotMatch"));return password;}
export function managedAccount(ctx,{user,families,reload}){
  const creating=!user,self=user?.id===ctx.state.me.id;
  const dialog=ctx.modal(`<h2>${creating?tText("admin:createAccount"):tText("admin:profileAndContacts")}</h2>${creating?'':`<p class="muted">${esc(personName(user.profile||{login:user.login}))} (@${esc(user.login)})</p>`}<form data-managed-account>${creating?`<label>${tHtml("admin:userLogin")}<input name="login" minlength="3" maxlength="40" pattern="[a-zA-Z0-9_.-]{3,40}" autocomplete="off" autocapitalize="none" spellcheck="false" required></label>${passwordFields}`:''}<label>${tHtml("admin:userName")}<input name="display_name" maxlength="80" value="${esc(user?.profile?.display_name||'')}" autocomplete="off" placeholder="${tHtml("admin:forExampleGrandmaNina")}"></label><div class="admin-managed-avatar" data-avatar-preview>${personAvatar(user?.profile||{login:'?'})}</div><div data-avatar-presets></div><label>${tHtml("admin:userAvatar")}<input type="file" name="avatar_file" accept="image/png,image/jpeg,image/webp,image/gif,image/mpo,.mpo"></label><p class="hint">${avatarHint}</p>${user?.profile?.avatar_url?`<label class="check-label"><input type="checkbox" name="remove_avatar">${tHtml("admin:removeCurrentAvatar")}</label>`:''}${!self?`<fieldset class="admin-managed-connections"><legend>${tHtml("admin:contactsAndSpaces")}</legend><label class="check-label"><input type="checkbox" name="add_friend" ${user?.friend?'checked disabled':''}>${user?.friend?tText("admin:alreadyInMyFriends"):tText("admin:addMeAsAFriendImmediately")}</label><p class="hint">${tHtml("admin:addToWorkspacesWithoutConfirmation")}</p>${families.map(f=>`<label class="check-label"><input type="checkbox" name="family_ids" value="${esc(f.id)}" ${user?.families?.includes(f.id)?'checked disabled':''}>${esc(f.name)}${user?.families?.includes(f.id)?(" "+tText("admin:alreadyAMember")+""):''}</label>`).join('')||`<p class="hint">${tHtml("admin:youDonTCurrentlyHaveAnyWorkspacesYou")}</p>`}</fieldset>`:''}${creating?`<p class="hint">${tHtml("admin:accountIsImmediatelyReadyForLoginWithUsername")}</p>`:''}<button type="submit" class="primary">${creating?tText("admin:createAccount"):tText("admin:saveProfileAndContacts")}</button></form>`);
  dialog.classList.add('admin-managed-dialog');if(creating)bindPasswords(dialog);
  let avatarRead=Promise.resolve(null),avatarSequence=0,preset;
  const preview=$('[data-avatar-preview]',dialog),input=$('[name=avatar_file]',dialog),remove=$('[name=remove_avatar]',dialog);
  const presets=mountAvatarPresets($('[data-avatar-presets]',dialog),ctx,{selected:()=>preset??(user?.profile?.avatar_preset||''),choose:async value=>{++avatarSequence;preset=value;input.value='';avatarRead=Promise.resolve(null);if(remove)remove.checked=false;preview.innerHTML=`<img src="/api/avatar-presets/${value}" alt="${tHtml("admin:previewAvatar")}">`;}});
  input.onchange=()=>{
    const file=input.files[0],sequence=++avatarSequence;
    if(!file){avatarRead=Promise.resolve(null);preview.innerHTML=personAvatar(user?.profile||{login:'?'});return;}
    if(remove)remove.checked=false;preset='';presets.refresh();
    preview.textContent=tText("admin:preparingPhoto");
    avatarRead=prepareAvatar(file).then(avatarDataUrl).then(data=>{
      if(sequence!==avatarSequence)return null;
      preview.innerHTML=`<img src="${esc(data)}" alt="${tHtml("admin:previewAvatar")}">`;
      return {data:data.slice(data.indexOf(',')+1)};
    }).catch(error=>{
      if(sequence!==avatarSequence)return null;
      preview.textContent=error.message;ctx.toast(error.message);return {error};
    });
  };
  if(remove)remove.onchange=()=>{preset=remove.checked?'':undefined;presets.refresh();++avatarSequence;input.value='';avatarRead=Promise.resolve(null);preview.innerHTML=personAvatar(remove.checked?{login:user.login}:user.profile);};
  formBind($('[data-managed-account]',dialog),async form=>{
    input.disabled=true;presets.setBusy(true);if(remove)remove.disabled=true;
    try{
      const selected=await avatarRead;if(selected?.error)throw selected.error;
      const data={display_name:form.get('display_name'),add_friend:form.has('add_friend'),family_ids:form.getAll('family_ids'),remove_avatar:form.has('remove_avatar'),avatar:selected?.data||null};
      if(preset)data.avatar_preset=preset;
      if(creating){data.login=form.get('login');data.password=userPassword(form);}
      await ctx.api('/admin/users'+(creating?'':'/'+user.id+'/profile'),creating?'POST':'PUT',data);
      dialog.querySelectorAll('[name=password],[name=repeat_password]').forEach(input=>input.value='');$('.modal-close',dialog).click();await reload();
      if(self){ctx.state.me=await ctx.api('/me');const avatar=document.querySelector('.user .avatar'),name=document.querySelector('.user strong');if(avatar)avatar.innerHTML=avatarMarkup(ctx.state.me);if(name)name.textContent=displayName(ctx.state.me);}
      ctx.toast(creating?tText("admin:accountCreatedAndReadyToSignIn"):tText("admin:profileAndContactsSaved"));
    }finally{input.disabled=false;presets.setBusy(false);if(remove)remove.disabled=false;}
  });
}
export function managedPassword(ctx,user){
  const dialog=ctx.modal(`<h2>${tHtml("admin:setPassword")}</h2><p>${esc(personName(user.profile||{login:user.login}))} (@${esc(user.login)})</p><p class="hint">${tHtml("admin:allPreviousSessionsForThisUserWillBe")}</p><form data-managed-password>${passwordFields}<button type="submit" class="primary">${tHtml("admin:savePassword")}</button></form>`);bindPasswords(dialog);
  formBind($('[data-managed-password]',dialog),async form=>{await ctx.api('/admin/users/'+user.id+'/password','PUT',{password:userPassword(form)});$('.modal-close',dialog).click();ctx.toast(tText("admin:passwordChangedPreviousSessionsTerminated"));});
}
