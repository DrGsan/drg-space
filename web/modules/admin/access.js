import {tText,tHtml} from "../../core/i18n.js";
import {$} from '../../core/ui.js';
import {sectionHeader} from '../../core/section-header.js';

export function adminWorkspace(host,ctx,mount){
  let alive=true,ready=false,unlocked=false,deadline=0,pending=null,unlockDialog=null;
  let activityTimer=null,lastTouch=0,lastInteraction=0,checking=false;
  const owned=new Set();
  host.innerHTML=`<div data-admin-locked><div class="page-heading"><h1>${tHtml("admin:administration")}</h1><button class="secondary" data-admin-back data-back-settings>${tHtml("admin:toSettings")}</button></div><section class="panel admin-locked"><h2>${tHtml("admin:accessControl")}</h2><p>${tHtml("admin:enterAdministratorPasswordAccessWillBeReVerified")}</p><button type="button" class="primary" data-admin-enter>${tHtml("admin:enterAdministration")}</button><p role="status" data-admin-access-error></p></section></div><div data-admin-content hidden></div>`;
  const locked=$('[data-admin-locked]',host),content=$('[data-admin-content]',host);
  sectionHeader(locked,ctx);$('[data-admin-back]',locked).onclick=()=>ctx.navigate('settings');
  host.closest('#view-content')?.classList.add('preferences-surface');
  host.prepend(locked.querySelector(':scope>.page-heading'));
  function reveal(value){
    unlocked=value;locked.hidden=value;content.hidden=!value;content.inert=!value;
    for(const modal of owned)if(modal.isConnected){modal.inert=!value;modal.style.visibility=value?'':'hidden';}
  }
  function accept(data){deadline=Date.now()+Math.max(0,data.expires_at-data.server_time)*1000;lastTouch=Date.now();reveal(true);}
  function prompt(){
    if(pending)return pending;
    reveal(false);
    pending=new Promise((resolve,reject)=>{
      const dialog=unlockDialog=ctx.modal(`<h2>${tHtml("admin:enterAdministration2")}</h2><p>${tHtml("admin:toManageEnterAdministratorPasswordAccessWillBe")}</p><form data-admin-unlock><label>${tHtml("admin:administratorPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label><p class="hint" role="status" data-unlock-error></p><button type="submit" class="primary">${tHtml("admin:enterAdministration3")}</button></form>`);
      dialog.classList.add('admin-unlock-dialog');
      let settled=false;
      const observer=new MutationObserver(()=>{if(!dialog.isConnected){observer.disconnect();if(!settled){settled=true;reject(new Error(tText("admin:administrationAccessCancelled")));}}});
      observer.observe(document.body,{childList:true});
      const form=$('form',dialog),input=$('[name=password]',form),button=$('[type=submit]',form);
      form.onsubmit=async event=>{
        event.preventDefault();if(button.disabled)return;button.disabled=true;
        try{
          const data=await ctx.api('/admin/access/unlock','POST',{password:input.value});
          if(!alive||!dialog.isConnected)return;
          accept(data);settled=true;observer.disconnect();$('.modal-close',dialog).click();resolve(data);
        }catch(error){if(dialog.isConnected)$('[data-unlock-error]',dialog).textContent=error.message;}
        finally{input.value='';button.disabled=false;}
      };
      input.focus();
    }).finally(()=>{pending=null;unlockDialog=null;});
    return pending;
  }
  const request=async action=>{
    try{return await action();}catch(error){
      if(!error.adminUnlock||!alive)throw error;
      await prompt();if(!alive)throw new Error(tText("admin:sectionClosed"));return action();
    }
  };
  const guarded={...ctx,api:(...args)=>request(()=>ctx.api(...args)),adminTransfer:request,
    modal:html=>{const dialog=ctx.modal(html);owned.add(dialog);return dialog;}};
  async function enter(){
    try{
      const state=await ctx.api('/admin/access','GET',undefined,true,{}, {timeoutMs:10000});if(!alive)return;
      if(state.unlocked)accept(state);else await prompt();
      if(!alive)return;
      if(!ready){
        ready=true;mount(content,guarded);
        // The shared page header stays at the working area's edge. Protected
        // forms keep their narrower container and can be hidden independently.
        const heading=content.querySelector(':scope>.page-heading');
        if(heading){host.querySelector(':scope>.page-heading')?.remove();host.prepend(heading);}
      }
    }catch(error){if(alive)$('[data-admin-access-error]',host).textContent=error.message;}
  }
  $('[data-admin-enter]',host).onclick=enter;
  async function touch(){
    activityTimer=null;if(!alive||!unlocked||document.hidden)return;
    try{const state=await ctx.api('/admin/access/activity','POST',{});if(alive)accept(state);}
    catch(error){if(error.adminUnlock&&alive)prompt().catch(()=>{});}
  }
  function activity(event){
    if(!alive||!unlocked||!event.isTrusted||document.hidden)return;
    if(!host.contains(event.target)&&![...owned].some(modal=>modal.contains(event.target)))return;
    lastInteraction=Date.now();clearTimeout(activityTimer);
    activityTimer=setTimeout(touch,Math.max(0,Math.min(lastInteraction+5000,lastTouch+30000)-Date.now()));
  }
  async function check(){
    if(!alive||!unlocked||checking||document.hidden||Date.now()<deadline)return;
    checking=true;reveal(false);
    try{const state=await ctx.api('/admin/access','GET',undefined,true,{}, {timeoutMs:10000});if(!alive)return;if(state.unlocked)accept(state);else await prompt();}
    catch(error){if(alive&&!pending){reveal(false);$('[data-admin-access-error]',host).textContent=tText("admin:failedToVerifyAccessReEnterAdministrationAccess");}}
    finally{checking=false;}
  }
  const events=['pointerdown','keydown','input','wheel'];
  events.forEach(name=>document.addEventListener(name,activity,{passive:true}));
  document.addEventListener('visibilitychange',check);
  const timer=setInterval(check,1000);
  enter();
  return ()=>{
    alive=false;clearInterval(timer);clearTimeout(activityTimer);
    events.forEach(name=>document.removeEventListener(name,activity));document.removeEventListener('visibilitychange',check);
    if(unlockDialog?.isConnected)$('.modal-close',unlockDialog).click();
    for(const modal of owned)if(modal.isConnected)$('.modal-close',modal).click();
  };
}
