import {tText,tHtml} from '../../core/i18n.js';
import {$,esc,date,formBind} from '../../core/ui.js';
import './sharing.css';

let dialogSequence=0;

export function confirmRetention(plan,ctx){
 return new Promise(resolve=>{
  let settled=false;
  const el=ctx.modal(`<h2>${tHtml(plan.auto_delete?'files:shareConfirmRetention':'files:shareConfirmDelay')}</h2><p>${tHtml('files:shareConfirmDescription')}</p><ul class=file-retention-confirm>${plan.files.map(f=>`<li><strong>${esc(f.name)}</strong><span>${esc(date(f.delete_at))}</span></li>`).join('')}</ul><div class=share-actions><button class=primary data-retention-confirm>${tHtml('files:shareConfirmButton')}</button><button class=text-link data-retention-cancel>${tHtml('common:cancel')}</button></div>`);
  const finish=value=>{if(settled)return;settled=true;observer.disconnect();resolve(value);$('.modal-close',el)?.click();};
  const observer=new MutationObserver(()=>{if(!el.isConnected)finish(false);});observer.observe(document.body,{childList:true,subtree:true});
  $('[data-retention-confirm]',el).onclick=()=>finish(true);$('[data-retention-cancel]',el).onclick=()=>finish(false);
 });
}
export function fileShare(file,ctx){
 const prefix='file-share-'+(++dialogSequence);
 const el=ctx.modal(`<h2>${tHtml('files:share3',{value0:esc(file.kind==='folder'?tText('files:withTheFolder'):tText('files:withTheFile'))})}</h2><p class="hint file-share-target">${esc(file.name)}</p><div class=file-share-tabs role=tablist aria-label="${tHtml('files:shareDialogSections')}"><button type=button role=tab id="${prefix}-links-tab" aria-controls="${prefix}-links" aria-selected=true data-share-tab=links>${tHtml('files:activeLinks')}<span data-share-count></span></button><button type=button role=tab id="${prefix}-create-tab" aria-controls="${prefix}-create" aria-selected=false tabindex=-1 data-share-tab=create>${tHtml('files:shareNewLink')}</button></div><section id="${prefix}-links" role=tabpanel aria-labelledby="${prefix}-links-tab" data-share-panel=links><p data-file-share-result role=status></p><div data-file-share-list><p role=status>${tHtml('files:shareLoadingLinks')}</p></div><div data-file-retention-list></div></section><section id="${prefix}-create" role=tabpanel aria-labelledby="${prefix}-create-tab" data-share-panel=create hidden><form data-file-share-form><label>${tHtml('files:accessMethod')}<select name=mode><option value=public>${tHtml('files:toEveryoneViaLink')}</option><option value=password>${tHtml('files:passwordProtected')}</option><option value=authenticated>${tHtml('files:afterAuthorization')}</option><option value=users>${tHtml('files:toSpecificLogins')}</option></select></label><label data-share-password hidden>${tHtml('files:publicationPassword')}<input name=password type=password minlength=8 maxlength=256></label><label data-share-users hidden>${tHtml('files:loginsSeparatedByCommas')}<input name=allowed maxlength=2100></label><label>${tHtml('files:accessRights')}<select name=permission><option value=view>${tHtml('files:readOnlyAndDownload')}</option><option value=edit>${tHtml('files:readAndEdit')}</option></select></label><div class=duration-fields><label>${tHtml('files:duration')}<input name=duration type=number min=1 max=10000 placeholder="${tHtml('files:forever')}"></label><label>${tHtml('files:unit')}<select name=unit><option value=minutes>${tHtml('files:minutes')}</option><option value=hours>${tHtml('files:clocks')}</option><option value=days selected>${tHtml('files:days')}</option><option value=months>${tHtml('files:months')}</option><option value=years>${tHtml('files:years')}</option></select></label></div><label>${tHtml('files:shareDownloadLimit')}<input name=download_limit type=number min=1 max=1000000 placeholder="${tHtml('files:shareUnlimited')}"></label><p class=hint>${tHtml('files:shareLimitHint')}</p><label class=file-retention-toggle><input type=checkbox name=auto_delete> <span>${tHtml('files:shareAutoDelete')}</span></label><p class=hint>${tHtml('files:shareRetentionHint')}</p><p class=error data-retention-error role=alert></p><button class=primary type=submit>${tHtml('files:createLink')}</button></form></section>`);
 el.classList.add('file-share-dialog');
 const form=$('[data-file-share-form]',el),auto=$('[name=auto_delete]',form),button=$('[type=submit]',form);let approved=null,revision=0,listingSequence=0,tabChosen=false;
 const tabs=[...el.querySelectorAll('[data-share-tab]')];
 const selectTab=(name,user=false,focus=false)=>{
  if(user)tabChosen=true;
  for(const tab of tabs){const selected=tab.dataset.shareTab===name;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;}
  for(const panel of el.querySelectorAll('[data-share-panel]'))panel.hidden=panel.dataset.sharePanel!==name;
  $('.modal',el).scrollTop=0;
  if(focus)tabs.find(tab=>tab.dataset.shareTab===name)?.focus({preventScroll:true});
 };
 tabs.forEach((tab,index)=>{
  tab.onclick=()=>selectTab(tab.dataset.shareTab,true);
  tab.onkeydown=event=>{const next=event.key==='ArrowRight'?(index+1)%tabs.length:event.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:event.key==='Home'?0:event.key==='End'?tabs.length-1:null;if(next===null)return;event.preventDefault();selectTab(tabs[next].dataset.shareTab,true,true);};
 });
 const modeLabel=mode=>({public:tText('files:toEveryoneViaLink'),password:tText('files:passwordProtected'),authenticated:tText('files:afterAuthorization'),users:tText('files:toSpecificLogins')})[mode]||tText('files:accessMethod');
 const body=()=>{const f=new FormData(form);return {mode:f.get('mode'),permission:f.get('permission'),password:f.get('mode')==='password'?f.get('password'):null,allowed:f.get('mode')==='users'?f.get('allowed').split(',').map(x=>x.trim()).filter(Boolean):[],duration:f.get('duration')?Number(f.get('duration')):null,unit:f.get('unit'),download_limit:f.get('download_limit')?Number(f.get('download_limit')):null,auto_delete:auto.checked};};
 form.oninput=()=>{revision++;approved=null;};
 $('[name=mode]',form).onchange=e=>{$('[data-share-password]',el).hidden=e.target.value!=='password';$('[data-share-users]',el).hidden=e.target.value!=='users';};
 const approve=async()=>{
  const data=body(),value=JSON.stringify(data),generation=revision,plan=await ctx.api('/files/'+file.id+'/share-preview','POST',data);
  if(!el.isConnected)return null;
  if(plan.requires_confirmation&&!await confirmRetention(plan,ctx))return null;
  if(generation!==revision||JSON.stringify(body())!==value)throw Error(tText('server:fileRetentionChanged'));
  approved={value,plan,at:Date.now()};return approved;
 };
 auto.onchange=async()=>{
  approved=null;if(!auto.checked)return;button.disabled=true;$('[data-retention-error]',el).textContent='';
  try{if(!await approve())auto.checked=false;}catch(error){auto.checked=false;$('[data-retention-error]',el).textContent=error.message;}finally{button.disabled=false;}
 };
 const listing=async(changed=false,changes=[])=>{
  const request=++listingSequence;
  const [shares,retention]=await Promise.all([ctx.api('/files/'+file.id+'/shares'),ctx.api('/files/'+file.id+'/retention')]);if(!el.isConnected||request!==listingSequence)return;
  $('[data-share-count]',el).textContent=' · '+shares.length;
  if(!changed&&!tabChosen&&!shares.length&&!retention.files.length)selectTab('create');
  if(changed){const states=new Map(changes.map(f=>[f.id,f]));for(const f of retention.files)states.set(f.id,f);ctx.onFilesChanged?.({id:file.id,shares,retention:[...states.values()]});}
  const list=$('[data-file-share-list]',el);
  list.innerHTML=shares.length?shares.map(s=>`<article class="share-row file-share-link" data-share-id="${esc(s.token)}"><strong>${esc(modeLabel(s.mode))}</strong><small>${s.permission==='edit'?tHtml('files:editing'):tHtml('files:read')} · ${s.expires?tHtml('files:until',{value0:date(s.expires)}):tHtml('files:noExpirationDate')}</small><input type=url readonly value="${esc(s.url)}" aria-label="${tHtml('files:shareLinkAddress')}" data-share-url><div class=file-share-link-actions><button type=button class="primary compact" data-copy-file-share="${esc(s.token)}">${tHtml('app:copy')}</button><a class="secondary compact" href="${esc(s.url)}" target=_blank rel=noopener>${tHtml('files:open')}</a><button type=button class="text-link danger" data-revoke-file-share="${esc(s.token)}">${tHtml('files:revoke')}</button></div><div class=file-share-budget>${s.download_limit!==null?`<small>${tHtml('files:shareBudget',{used:s.used,remaining:Math.max(0,s.download_limit-s.used),limit:s.download_limit})}</small>`:''}<button type=button class=text-link data-limit="${esc(s.token)}">${tHtml('files:shareChangeLimit')}</button></div></article>`).join(''):`<p class=hint>${tHtml('files:shareNoActiveLinks')}</p><button type=button class="secondary compact" data-share-start>${tHtml('files:createLink')}</button>`;
  $('[data-share-start]',el)?.addEventListener('click',()=>selectTab('create',true,true));
  list.querySelectorAll('[data-share-url]').forEach(input=>input.onclick=()=>input.select());
  for(const b of el.querySelectorAll('[data-copy-file-share]'))b.onclick=async()=>{
   const share=shares.find(s=>s.token===b.dataset.copyFileShare),input=b.closest('[data-share-id]').querySelector('[data-share-url]');
   try{await navigator.clipboard.writeText(share.url);if(el.isConnected)ctx.toast(tText('app:linkCopied'));}
   catch{if(!input.isConnected)return;input.focus();input.select();ctx.toast(tText('app:copyTheSelectedLink'));}
  };
  for(const b of el.querySelectorAll('[data-revoke-file-share]'))b.onclick=async()=>{b.disabled=true;try{await ctx.api('/file-shares/'+b.dataset.revokeFileShare,'DELETE');await refreshListing(true);}catch(e){ctx.toast(e.message);}finally{if(b.isConnected)b.disabled=false;}};
  for(const b of el.querySelectorAll('[data-limit]'))b.onclick=()=>{
   const s=shares.find(x=>x.token===b.dataset.limit),dialog=ctx.modal(`<h2>${tHtml('files:shareChangeLimit')}</h2><form data-limit-form><label>${tHtml('files:shareDownloadLimit')}<input name=limit type=number min=1 max=1000000 value="${s.download_limit??''}" placeholder="${tHtml('files:shareUnlimited')}"></label><p>${tHtml('files:shareLimitKeepCount')}</p><button class=primary>${tHtml('files:shareSave')}</button></form>`);
   formBind($('form',dialog),async f=>{await ctx.api('/file-shares/'+s.token+'/limit','PATCH',{version:s.version,download_limit:f.get('limit')?Number(f.get('limit')):null});$('.modal-close',dialog).click();await refreshListing(true);});
  };
  $('[data-file-retention-list]',el).innerHTML=retention.files.length?`<h3>${tHtml('files:shareRetentionTitle')}</h3>${retention.files.map(f=>`<div class=share-row><div><strong>${esc(f.name)}</strong><small>${f.delete_at?tHtml('files:shareDeleteAt',{date:esc(date(f.delete_at))}):tHtml(f.kind==='folder'?'files:shareFolderWaiting':'files:shareRetentionBlocked')}</small><button class=text-link data-retention-date="${esc(f.id)}">${tHtml('files:shareChangeDate')}</button></div><button class=text-link data-retention-off="${esc(f.id)}">${tHtml('files:shareCancelDeletion')}</button></div>`).join('')}`:'';
  for(const b of el.querySelectorAll('[data-retention-off]'))b.onclick=async()=>{const f=retention.files.find(x=>x.id===b.dataset.retentionOff);if(!confirm(tText('files:shareCancelDeletionConfirm')))return;try{await ctx.api('/files/'+f.id+'/retention','DELETE',{version:f.version});await refreshListing(true);}catch(error){ctx.toast(error.message);}};
  for(const b of el.querySelectorAll('[data-retention-date]'))b.onclick=()=>{
   const f=retention.files.find(x=>x.id===b.dataset.retentionDate),local=new Date(f.delete_at?f.delete_at*1000:Date.now()+86400000);local.setMinutes(local.getMinutes()-local.getTimezoneOffset());
   const dialog=ctx.modal(`<h2>${tHtml('files:shareChangeDate')}</h2><form><label>${esc(f.name)}<input type=datetime-local name=date required value="${local.toISOString().slice(0,16)}"></label><button class=primary>${tHtml('files:shareSave')}</button></form>`);
   formBind($('form',dialog),async values=>{const data={version:f.version,delete_at:Math.floor(new Date(values.get('date')).getTime()/1000)},plan=await ctx.api('/files/'+f.id+'/retention-preview','POST',data);if(!await confirmRetention(plan,ctx))return;const result=await ctx.api('/files/'+f.id+'/retention','PATCH',{...data,confirmation:plan.confirmation});$('.modal-close',dialog).click();await refreshListing(true,result.retention_changes);});
  };
 };
 formBind(form,async()=>{
  const data=body();let state=approved;
  if(!state||state.value!==JSON.stringify(data)||Date.now()-state.at>240000)state=await approve();
  if(!state){auto.checked=false;approved=null;return;}
  approved=null;const result=await ctx.api('/files/'+file.id+'/shares','POST',{...data,confirmation:state.plan.confirmation});
  if(!el.isConnected)return;selectTab('links',false,true);$('[data-file-share-result]',el).textContent=tText('files:shareLinkCreated');await refreshListing(true,result.retention_changes);
 });
 const refreshListing=async(changed=false,changes=[])=>{
  const pending=listing(changed,changes),request=listingSequence;
  try{await pending;}catch(error){if(!el.isConnected||request!==listingSequence)return;const list=$('[data-file-share-list]',el);list.innerHTML='<p role=alert>'+esc(error.message)+'</p><button type=button class="secondary compact" data-share-retry>'+tHtml('files:shareRetryLinks')+'</button>';$('[data-share-retry]',el).onclick=()=>refreshListing(changed,changes);}
 };
 refreshListing();return el;
}
