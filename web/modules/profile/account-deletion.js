import {tText,tHtml,locale} from "../../core/i18n.js";
import './account-deletion.css';
import {$,esc,md} from '../../core/ui.js';
export function mountAccountDeletion(host,ctx){
  const deletion=ctx.state.me.account_deletion;
  host.innerHTML=`<main class="space-deletion-screen" data-account-waiting><div><span class="space-deletion-icon" aria-hidden="true">◷</span><h1>${tHtml("profile:accountIsAwaitingDeletion")}</h1><p>@${esc(ctx.state.me.login)}</p><p>${tHtml("profile:theAccountAndPersonalDataWillBePermanently")} <strong>${esc(new Date(deletion.delete_at*1000).toLocaleString(locale()))}</strong>.</p><p>${tHtml("profile:changesAreBlockedDuringTheWaitingPeriodShared")}</p><p role="timer" data-account-countdown></p><details data-account-notes><summary>${tHtml("profile:viewPersonalNotes")}</summary><div data-account-note-list></div><article data-account-note-view></article></details><button type="button" class="primary" data-restore-account>${tHtml("profile:cancelAccountDeletion")}</button><button type="button" class="text-link" data-account-logout>${tHtml("profile:signOut")}</button><p role="alert" data-account-error></p></div></main>`;
  const screen=host.firstElementChild;
  const update=()=>{
    if(!screen.isConnected){clearInterval(timer);return;}
    const seconds=Math.max(0,deletion.delete_at-Math.floor(Date.now()/1000));
    $('[data-account-countdown]',host).textContent=seconds?tText("profile:beforeDeletionHoursMinutesSeconds", {value0:(Math.floor(seconds/3600)), value1:(Math.floor(seconds%3600/60)), value2:(seconds%60)}):tText("profile:theDeadlineHasExpiredDataIsBeingDeleted");
    $('[data-restore-account]',host).disabled=!seconds;
  };
  const timer=setInterval(update,1000);update();let polling=false;
  const poll=setInterval(async()=>{
    if(!screen.isConnected||Math.floor(Date.now()/1000)>=deletion.delete_at){clearInterval(poll);return;}
    if(polling||document.hidden)return;polling=true;
    try{const me=await ctx.api('/me');if(!screen.isConnected)return;if(!me.account_deletion){clearInterval(poll);ctx.state.me=me;await ctx.loadWorkspace();}}catch(error){if(error.status===401&&screen.isConnected){clearInterval(timer);clearInterval(poll);ctx.expired();}}finally{polling=false;}
  },5000);
  $('[data-account-notes]',host).ontoggle=async e=>{
    if(!e.target.open||e.target.dataset.loaded)return;e.target.dataset.loaded='1';
    const list=$('[data-account-note-list]',host),view=$('[data-account-note-view]',host);list.textContent=tText("profile:loading");
    try{
      const notes=(await ctx.api('/notes')).filter(note=>!note.space_id);
      if(!screen.isConnected)return;
      list.innerHTML=notes.map(note=>`<button type="button" class="text-link" data-read-note="${esc(note.id)}">${esc(note.title)}</button>`).join('')||`<p>${tHtml("profile:noPersonalNotes")}</p>`;
      list.querySelectorAll('[data-read-note]').forEach(button=>button.onclick=()=>{const note=notes.find(note=>note.id===button.dataset.readNote);view.innerHTML=`<h2>${esc(note.title)}</h2>${md(note.body||'')}`;});
    }catch(error){list.textContent=error.message;delete e.target.dataset.loaded;}
  };
  $('[data-account-logout]',host).onclick=ctx.logout;
  $('[data-restore-account]',host).onclick=async e=>{
    e.target.disabled=true;
    try{await ctx.api('/account/deletion/restore','POST');clearInterval(timer);clearInterval(poll);if(!screen.isConnected)return;const me=await ctx.api('/me');if(!screen.isConnected)return;ctx.state.me=me;await ctx.loadWorkspace();}
    catch(error){if(screen.isConnected){$('[data-account-error]',host).textContent=error.message;update();}}
  };
}
