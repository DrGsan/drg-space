import {tText,tHtml} from '../../core/i18n.js';
import {$,esc,date,formBind} from '../../core/ui.js';

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
 const el=ctx.modal(`<h2>${tHtml('files:share3',{value0:esc(file.kind==='folder'?tText('files:withTheFolder'):tText('files:withTheFile'))})}</h2><form data-file-share-form><label>${tHtml('files:accessMethod')}<select name=mode><option value=public>${tHtml('files:toEveryoneViaLink')}</option><option value=password>${tHtml('files:passwordProtected')}</option><option value=authenticated>${tHtml('files:afterAuthorization')}</option><option value=users>${tHtml('files:toSpecificLogins')}</option></select></label><label data-share-password hidden>${tHtml('files:publicationPassword')}<input name=password type=password minlength=8 maxlength=256></label><label data-share-users hidden>${tHtml('files:loginsSeparatedByCommas')}<input name=allowed maxlength=2100></label><label>${tHtml('files:accessRights')}<select name=permission><option value=view>${tHtml('files:readOnlyAndDownload')}</option><option value=edit>${tHtml('files:readAndEdit')}</option></select></label><div class=duration-fields><label>${tHtml('files:duration')}<input name=duration type=number min=1 max=10000 placeholder="${tHtml('files:forever')}"></label><label>${tHtml('files:unit')}<select name=unit><option value=minutes>${tHtml('files:minutes')}</option><option value=hours>${tHtml('files:clocks')}</option><option value=days selected>${tHtml('files:days')}</option><option value=months>${tHtml('files:months')}</option><option value=years>${tHtml('files:years')}</option></select></label></div><label>${tHtml('files:shareDownloadLimit')}<input name=download_limit type=number min=1 max=1000000 placeholder="${tHtml('files:shareUnlimited')}"></label><p class=hint>${tHtml('files:shareLimitHint')}</p><label class=file-retention-toggle><input type=checkbox name=auto_delete> <span>${tHtml('files:shareAutoDelete')}</span></label><p class=hint>${tHtml('files:shareRetentionHint')}</p><p class=error data-retention-error role=alert></p><button class=primary type=submit>${tHtml('files:createLink')}</button></form><div data-file-share-result></div><div data-file-share-list></div><div data-file-retention-list></div>`);
 const form=$('[data-file-share-form]',el),auto=$('[name=auto_delete]',form),button=$('[type=submit]',form);let approved=null,revision=0;
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
  const [shares,retention]=await Promise.all([ctx.api('/files/'+file.id+'/shares'),ctx.api('/files/'+file.id+'/retention')]);if(!el.isConnected)return;
  if(changed){const states=new Map(changes.map(f=>[f.id,f]));for(const f of retention.files)states.set(f.id,f);ctx.onFilesChanged?.({id:file.id,shares,retention:[...states.values()]});}
  $('[data-file-share-list]',el).innerHTML=`<h3>${tHtml('files:activeLinks')}</h3>${shares.map(s=>`<div class=share-row><div><a href="${esc(s.url)}" target=_blank rel=noopener>${esc(s.mode)} · ${s.permission==='edit'?tText('files:editing'):tText('files:read')}</a><small>${s.expires?tText('files:until',{value0:date(s.expires)}):tText('files:noExpirationDate')}</small>${s.download_limit!==null?`<small>${tHtml('files:shareBudget',{used:s.used,remaining:Math.max(0,s.download_limit-s.used),limit:s.download_limit})}</small>`:''}<button class=text-link data-limit="${esc(s.token)}">${tHtml('files:shareChangeLimit')}</button></div><button class="text-link danger" data-revoke-file-share="${esc(s.token)}">${tHtml('files:revoke')}</button></div>`).join('')}`;
  for(const b of el.querySelectorAll('[data-revoke-file-share]'))b.onclick=async()=>{try{await ctx.api('/file-shares/'+b.dataset.revokeFileShare,'DELETE');await listing(true);}catch(e){ctx.toast(e.message);}};
  for(const b of el.querySelectorAll('[data-limit]'))b.onclick=()=>{
   const s=shares.find(x=>x.token===b.dataset.limit),dialog=ctx.modal(`<h2>${tHtml('files:shareChangeLimit')}</h2><form data-limit-form><label>${tHtml('files:shareDownloadLimit')}<input name=limit type=number min=1 max=1000000 value="${s.download_limit??''}" placeholder="${tHtml('files:shareUnlimited')}"></label><p>${tHtml('files:shareLimitKeepCount')}</p><button class=primary>${tHtml('files:shareSave')}</button></form>`);
   formBind($('form',dialog),async f=>{await ctx.api('/file-shares/'+s.token+'/limit','PATCH',{version:s.version,download_limit:f.get('limit')?Number(f.get('limit')):null});$('.modal-close',dialog).click();await listing(true);});
  };
  $('[data-file-retention-list]',el).innerHTML=retention.files.length?`<h3>${tHtml('files:shareRetentionTitle')}</h3>${retention.files.map(f=>`<div class=share-row><div><strong>${esc(f.name)}</strong><small>${f.delete_at?tHtml('files:shareDeleteAt',{date:esc(date(f.delete_at))}):tHtml(f.kind==='folder'?'files:shareFolderWaiting':'files:shareRetentionBlocked')}</small><button class=text-link data-retention-date="${esc(f.id)}">${tHtml('files:shareChangeDate')}</button></div><button class=text-link data-retention-off="${esc(f.id)}">${tHtml('files:shareCancelDeletion')}</button></div>`).join('')}`:'';
  for(const b of el.querySelectorAll('[data-retention-off]'))b.onclick=async()=>{const f=retention.files.find(x=>x.id===b.dataset.retentionOff);if(!confirm(tText('files:shareCancelDeletionConfirm')))return;try{await ctx.api('/files/'+f.id+'/retention','DELETE',{version:f.version});await listing(true);}catch(error){ctx.toast(error.message);}};
  for(const b of el.querySelectorAll('[data-retention-date]'))b.onclick=()=>{
   const f=retention.files.find(x=>x.id===b.dataset.retentionDate),local=new Date(f.delete_at?f.delete_at*1000:Date.now()+86400000);local.setMinutes(local.getMinutes()-local.getTimezoneOffset());
   const dialog=ctx.modal(`<h2>${tHtml('files:shareChangeDate')}</h2><form><label>${esc(f.name)}<input type=datetime-local name=date required value="${local.toISOString().slice(0,16)}"></label><button class=primary>${tHtml('files:shareSave')}</button></form>`);
   formBind($('form',dialog),async values=>{const data={version:f.version,delete_at:Math.floor(new Date(values.get('date')).getTime()/1000)},plan=await ctx.api('/files/'+f.id+'/retention-preview','POST',data);if(!await confirmRetention(plan,ctx))return;const result=await ctx.api('/files/'+f.id+'/retention','PATCH',{...data,confirmation:plan.confirmation});$('.modal-close',dialog).click();await listing(true,result.retention_changes);});
  };
 };
 formBind(form,async()=>{
  const data=body();let state=approved;
  if(!state||state.value!==JSON.stringify(data)||Date.now()-state.at>240000)state=await approve();
  if(!state){auto.checked=false;approved=null;return;}
  approved=null;const result=await ctx.api('/files/'+file.id+'/shares','POST',{...data,confirmation:state.plan.confirmation});
  $('[data-file-share-result]',el).innerHTML=`<label>${tHtml('files:linkToPublication')}<input readonly value="${esc(result.url)}" aria-label="${tHtml('files:linkToFile')}"></label>`;await listing(true,result.retention_changes);
 });
 listing().catch(e=>ctx.toast(e.message));return el;
}
