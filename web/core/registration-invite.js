import {tText} from './i18n.js';

// Keep the disclosure tied to the exact public link, including after language changes.
export function invitationDisclosure(form,token,api){
  const note=document.createElement('p');note.className='hint invitation-friendship-disclosure';note.setAttribute('role','status');
  form.querySelector('.auth-message').after(note);note.textContent=tText('app:checkingInvitation');
  const submit=form.querySelector('[type=submit]');submit.disabled=true;
  const loaded=api('/invites/'+encodeURIComponent(token)).then(info=>{
    if(!form.isConnected)return;
    if(info.add_creator_as_friend){
      const name=info.friend?[info.friend.display_name,'@'+info.friend.login].filter(Boolean).join(' · '):'';
      note.textContent=name?tText('app:invitationFriendshipDisclosure',{name}):tText('app:invitationFriendshipCreatorUnavailable');
    }else note.remove();
    if(info.login){form.elements.login.value=info.login;form.elements.login.readOnly=true;}
    submit.disabled=false;
  },error=>{if(form.isConnected)note.textContent=error.message;throw error;});
  // Submission also awaits the same request; a scripted Enter cannot bypass disclosure.
  loaded.catch(()=>{});return loaded;
}
