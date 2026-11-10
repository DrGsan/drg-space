import {tText,tHtml} from "../../core/i18n.js";
import {pageLayout} from '../notes/page-layout.js';
import {pageWordCount,pageActions,showPageMenu,applyPageView} from '../notes/page-menu.js';
import {pageIcon} from '../../core/page-assets.js';
import {iconPicker,coverPicker} from '../notes/decoration.js';
import {$,esc,formBind} from '../../core/ui.js';
import {csrfCookie,refreshSession} from '../../core/auth.js';
import {abortable} from '../../core/request.js';
import {loadEditor,mountBlockEditor,pageBlocks,pageLinkIds,subpagePlaceholder} from '../notes/editor-runtime.js';
import {mountDatabase,recordForm,display} from '../notes/database.js';
import './vault.css';
import {mountSecretDrive} from '../files/secret.js';
import {uploadFile} from '../files/upload-panel.js';

// Only this loaded document owns these grants. Never persist PINs, grants,
// document contents or object URLs in storage, cookies or navigation URLs.
const grants=new Map();
const drafts=new Map();
const noteTrees=new Map(),treeListeners=new Map();
function treeValue(ctx){const grant=grants.get(key(ctx,'notes'));return grant&&grant.expires*1000>Date.now()&&!document.hidden?noteTrees.get(ctx.state.me.id)||null:null;}
function emitTree(ctx){for(const fn of treeListeners.get(ctx.state.me.id)||[])fn(treeValue(ctx));}
export function vaultNotesActivity(ctx){const grant=grants.get(key(ctx,'notes'));if(!grant||Date.now()-(grant.lastTreeActivity||0)<15000)return;grant.lastTreeActivity=Date.now();vaultApi(ctx,'notes','/activity','POST').then(value=>Object.assign(grant,value)).catch(()=>{});}
function cacheTree(ctx,items){noteTrees.set(ctx.state.me.id,items);emitTree(ctx);}
export function subscribeVaultNotes(ctx,fn){const uid=ctx.state.me.id;if(!treeListeners.has(uid))treeListeners.set(uid,new Set());treeListeners.get(uid).add(fn);fn(treeValue(ctx));let visible=!!treeValue(ctx),lastCheck=0;const tick=()=>{const grant=grants.get(key(ctx,'notes'));if(!grant||grant.expires*1000<=Date.now())noteTrees.delete(uid);const next=!!treeValue(ctx);if(next!==visible){visible=next;emitTree(ctx);}if(next&&Date.now()-lastCheck>30000){lastCheck=Date.now();vaultApi(ctx,'notes','/state').catch(()=>{});}};const timer=setInterval(tick,1000);document.addEventListener('visibilitychange',tick);return ()=>{clearInterval(timer);document.removeEventListener('visibilitychange',tick);treeListeners.get(uid)?.delete(fn);};}
if(typeof window!=='undefined')window.addEventListener('beforeunload',event=>{if(drafts.size){event.preventDefault();event.returnValue='';}});
let active;
const key=(ctx,section)=>ctx.state.me.id+':'+section;
const mobile=()=>matchMedia('(pointer:coarse)').matches||/Mobile|Android|iPhone|iPad/i.test(navigator.userAgent);
const pinInput=(name='pin',label='PIN')=>`<label>${label}<input name="${name}" type="password" inputmode="numeric" pattern="(?:[0-9]{4}|[0-9]{6})" minlength="4" maxlength="6" autocomplete="off" required aria-label="${label}"></label>`;
const factorInput=`<label>${tHtml("security:label2faOrRecoveryCode")}<input name="code" autocomplete="one-time-code" maxlength="100" required></label>`;
export const vaultController={flush:()=>active?.flush()??Promise.resolve(true),destroy:()=>{active?.destroy();active=null;},clear:()=>{active?.destroy();active=null;grants.clear();drafts.clear();noteTrees.clear();for(const listeners of treeListeners.values())for(const fn of listeners)fn(null);}};

export async function mountVaultSettings(host,ctx){
 const status=await ctx.api('/secret-vault');if(!host.isConnected)return;
 host.innerHTML=`<h2>${tHtml("security:secureStorage")}</h2><p class="hint">${tHtml("security:personalNotesAndFilesWithAnAdditional4")}</p>${!status.two_factor?`<p class="vault-warning">${tHtml("security:toEnableFirstSetUpTwoFactorAuthentication")}</p>`:!status.encryption_enabled?`<p class="vault-warning">${tHtml("security:toEnableServerDataEncryptionIsRequired")}</p>`:status.enabled?`<span class="tag">${status.deleting?tText("security:deletionNotCompleted"):tText("security:enabled")}</span><div class="vault-actions">${status.deleting?'':`<button class="secondary" data-vault-change>${tHtml("security:changePin")}</button>`}<button class="secondary danger" data-vault-delete>${status.deleting?tText("security:reDelete"):tText("security:deleteStorage")}</button></div><p class="hint">${tHtml("security:disablingAndResetting2FaIsProhibitedWhileA")}</p>`:`<button class="primary" data-vault-enable>${tHtml("security:enableStorage")}</button>`}`;
 const refresh=async()=>{vaultController.clear();await mountVaultSettings(host,ctx);const toggle=document.querySelector('[data-toggle-2fa]');if(toggle&&status.two_factor){const next=await ctx.api('/secret-vault');toggle.disabled=next.enabled;toggle.title=next.enabled?tText("security:firstDeleteTheSecretStorage"):'';}};
 host.querySelector('[data-vault-enable]')?.addEventListener('click',()=>{
  const el=ctx.modal(`<h2>${tHtml("security:enableTheSecretStorage")}</h2><p class="hint">${tHtml("security:rememberThePinPinRecoveryIsNotAvailable")}</p><form data-vault-form>${pinInput()}${pinInput('repeat',tText("security:reEnterPin"))}${factorInput}<button class="primary">${tHtml("security:enable")}</button></form>`);
  formBind($('[data-vault-form]',el),async form=>{if(form.get('pin')!==form.get('repeat'))throw Error(tText("security:pinsDoNotMatch"));await ctx.api('/secret-vault/enable','POST',{pin:form.get('pin'),code:form.get('code')});$('.modal-close',el).click();await refresh();ctx.toast(tText("security:secretSectionsHaveAppearedInNotesAndFiles"));});
 });
 host.querySelector('[data-vault-change]')?.addEventListener('click',()=>{
  const el=ctx.modal(`<h2>${tHtml("security:changePin")}</h2><form data-vault-form>${pinInput('old_pin',tText("security:currentPin"))}${pinInput('pin',tText("security:newPin"))}${pinInput('repeat',tText("security:reEnterNewPin"))}${factorInput}<button class="primary">${tHtml("security:changePin")}</button></form>`);
  formBind($('[data-vault-form]',el),async form=>{if(form.get('pin')!==form.get('repeat'))throw Error(tText("security:pinsDoNotMatch"));await ctx.api('/secret-vault/pin','PUT',{old_pin:form.get('old_pin'),pin:form.get('pin'),code:form.get('code')});$('.modal-close',el).click();await refresh();ctx.toast(tText("security:pinChangedAllSecretSectionsAreLocked"));});
 });
 host.querySelector('[data-vault-delete]')?.addEventListener('click',()=>{
  const el=ctx.modal(`<h2>${tHtml("security:deleteTheSecretStorage")}</h2><p class="vault-warning">${tHtml("security:allSecretNotesFilesAndAttachmentsWillBe")}</p><form data-vault-form><label>${tHtml("security:accountPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label>${factorInput}<label class="check-label"><input type="checkbox" required>${tHtml("security:permanentlyDeleteAllSecretContent")}</label><button class="primary danger">${tHtml("security:deleteStorage")}</button></form>`);
  formBind($('[data-vault-form]',el),async form=>{await ctx.api('/secret-vault','DELETE',Object.fromEntries(form));$('.modal-close',el).click();await refresh();ctx.toast(tText("security:secretStorageDeleted"));});
 });
 const toggle=document.querySelector('[data-toggle-2fa]');if(toggle&&status.two_factor){toggle.disabled=status.enabled;toggle.title=status.enabled?tText("security:firstDeleteTheSecretStorage"):'';}
}

export async function ensureVault(ctx,section){
 const id=key(ctx,section),old=grants.get(id);
 if(old&&old.expires*1000>Date.now())return old;
 grants.delete(id);if(section==='notes'){noteTrees.delete(ctx.state.me.id);emitTree(ctx);}
 return new Promise(resolve=>{
  const el=ctx.modal(`<h2>${tHtml("security:secret", {value0:(section==='notes'?tText("security:notes"):tText("security:files"))})}</h2><p class="hint">${tHtml("security:enterThe4Or6DigitStoragePin")}</p><form data-vault-unlock>${pinInput()}<button class="primary">${tHtml("security:open")}</button></form><button class="text-link" data-vault-unblock>${tHtml("security:disableRetryAttemptsVia2Fa")}</button>`);
  let finished=false;const observer=new MutationObserver(()=>{if(!el.isConnected){observer.disconnect();if(!finished)resolve(null);}});observer.observe(document.body,{childList:true,subtree:true});
  formBind($('[data-vault-unlock]',el),async form=>{const grant=await ctx.api('/secret-vault/unlock','POST',{pin:form.get('pin'),section,mobile:mobile()});grants.set(id,grant);finished=true;observer.disconnect();$('.modal-close',el).click();resolve(grant);});
  $('[data-vault-unblock]',el).onclick=()=>{
   const proof=ctx.modal(`<h2>${tHtml("security:disableRetryAttempts")}</h2><p class="hint">${tHtml("security:youWillStillNeedTheCorrectPinAfter")}</p><form data-vault-proof>${factorInput}<button class="primary">${tHtml("security:confirm")}</button></form>`);
   formBind($('[data-vault-proof]',proof),async form=>{await ctx.api('/secret-vault/unblock','POST',Object.fromEntries(form));$('.modal-close',proof).click();ctx.toast(tText("security:retryAttemptsDisabledEnterPin"));});
  };
  $('input[name=pin]',el).focus();
 });
}

export async function vaultApi(ctx,section,path,method='GET',body,options={}){
 const grant=grants.get(key(ctx,section));if(!grant)throw Error(tText("security:enterThePinForTheSecretSection"));
 try{return await ctx.api('/secret-vault/'+section+path,method,body,true,{'X-Vault-Token':grant.token},{timeoutMs:path.startsWith('/move-')?0:15000,...options});}
 catch(error){if(error.status===401||error.status===423){grants.delete(key(ctx,section));if(section==='notes')emitTree(ctx);if(active?.section===section&&active.ctx.state.me.id===ctx.state.me.id)active.expire();}throw error;}
}

// Unlock the sidebar without replacing the page that is already open.
export async function unlockVaultNotes(ctx){
 if(!await ensureVault(ctx,'notes'))return false;
 const data=await vaultApi(ctx,'notes','/items');cacheTree(ctx,data.items);return true;
}

export async function moveIntoVault(ctx,section,item){
 if(!confirm(tText("security:moveToTheSecretStorageAlongWithNested")))return false;
 if(!await ensureVault(ctx,section))return false;
 await vaultApi(ctx,section,'/move-in','POST',{id:item.id,version:item.version});return true;
}

export function mountVault(host,ctx,section){
 active?.destroy();
 const panel=new VaultPanel(host,ctx,section);active=panel;panel.ready=panel.mount();return panel;
}

class VaultPanel{
 constructor(host,ctx,section){this.host=host;this.ctx=ctx;this.section=section;this.urls=new Map();this.parent=null;this.cleanups=[];this.disposed=false;this.generation=0;this.network=new AbortController();}
 api(path,method,body){return vaultApi(this.ctx,this.section,path,method,body,{signal:this.network.signal});}
 async mount(){
  this.host.dataset.vaultObscuredLabel=tText('security:vaultObscured');this.host.classList.add('secret-vault');if(this.section==='notes'){this.host.replaceChildren();return this.open();}this.host.innerHTML=`<div class="vault-locked"><span aria-hidden="true">🔒</span><h2>${tHtml("security:secret", {value0:(this.section==='notes'?tText("security:notes"):tText("security:files"))})}</h2><p class="hint">${tHtml("security:additionalPinProtectionWithoutHistoryAndTrash")}</p><button class="primary" data-vault-open>${tHtml("security:openStorage")}</button></div>`;
  $('[data-vault-open]',this.host).onclick=()=>this.open();
  return this.open();
 }
 async open(){
  try{const grant=await ensureVault(this.ctx,this.section);if(!grant||this.disposed)return;
   this.network=new AbortController();this.locked=false;
   if(this.section==='notes')await this.refreshTree();else await this.list();if(this.disposed)return;
   const pending=drafts.get(key(this.ctx,this.section));if(pending){const sealed=await pending;const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:sealed.iv},sealed.key,sealed.body);const draft=JSON.parse(new TextDecoder().decode(plain));await this.openNote(draft.id,draft);}
   this.bindActivity();return true;
  }catch(error){if(!this.disposed)this.ctx.toast(error.message);}
 }
 bindActivity(){
  if(this.bound)return;this.bound=true;
  const activity=e=>{if(!e.isTrusted||document.hidden)return;this.touch();};
  for(const type of ['pointerdown','keydown','wheel','touchstart']){this.host.addEventListener(type,activity,{passive:true});this.cleanups.push(()=>this.host.removeEventListener(type,activity));}
  const visibility=()=>{
   // Occlude synchronously before background timers can be suspended by iOS.
   if(document.hidden){this.closePageMenu?.(false);this.filesView?.obscure(true);this.flush();this.host.classList.add('vault-obscured');if(this.previewModal)this.previewModal.hidden=true;for(const modal of this.secretModals||[])modal.hidden=true;this.hidePortals();}
   else{const grant=grants.get(key(this.ctx,this.section));if(!grant||grant.expires*1000<=Date.now())this.expire(true);else{this.filesView?.obscure(false);this.host.classList.remove('vault-obscured');if(this.previewModal)this.previewModal.hidden=false;for(const modal of this.secretModals||[])modal.hidden=false;}}
  };
  document.addEventListener('visibilitychange',visibility);this.cleanups.push(()=>document.removeEventListener('visibilitychange',visibility));
  const beforeUnload=e=>{if(this.dirty||this.saving){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',beforeUnload);this.cleanups.push(()=>window.removeEventListener('beforeunload',beforeUnload));
  const leave=()=>{this.destroy();grants.clear();};window.addEventListener('pagehide',leave);this.cleanups.push(()=>window.removeEventListener('pagehide',leave));
  this.clock=setInterval(()=>{
   if(this.disposed||!this.host.isConnected){this.destroy();return;}
   const grant=grants.get(key(this.ctx,this.section));if(!grant||grant.expires*1000<=Date.now()){this.expire(true);return;}
   if(!document.hidden&&Date.now()-(this.lastCheck||0)>30000){this.lastCheck=Date.now();this.api('/state').catch(error=>{if(error.status===404)this.expire();});}
  },1000);
 }
 async touch(force=false){
  if(this.disposed||this.touching||!grants.has(key(this.ctx,this.section)))return;
  if(!force&&Date.now()-(this.lastTouch||0)<15000)return;
  this.lastTouch=Date.now();this.touching=true;
  try{const value=await this.api('/activity','POST');const grant=grants.get(key(this.ctx,this.section));if(grant)Object.assign(grant,value);}
  catch(error){if(!this.disposed)this.expire();}finally{this.touching=false;}
 }
 hidePortals(){document.querySelectorAll('.bn-container [role=dialog],.bn-container [role=listbox],.bn-container [role=menu]').forEach(el=>el.remove());}
 async expire(automatic=false){
  if(this.disposed||this.locked)return;
  if(automatic)vaultApi(this.ctx,this.section,'/auto-lock','POST').catch(()=>{});
  this.locked=true;grants.delete(key(this.ctx,this.section));if(this.section==='notes'){noteTrees.delete(this.ctx.state.me.id);emitTree(this.ctx);}
  this.host.classList.add('vault-obscured');
  if(this.sourceDirty&&this.editor){this.editor.replaceMarkdown(this.convert($('#markdown-source',this.host).value,false));this.sourceDirty=false;}
  if((this.dirty||this.saving)&&this.note&&(this.editor||this.note.database)){
   const payload=this.convert({id:this.note.id,version:this.note.version,title:$('#page-title',this.host)?.value||this.note.title,blocks:this.editor?this.editor.document():this.note.blocks},true);
   drafts.set(key(this.ctx,this.section),(async()=>{const secret=await crypto.subtle.generateKey({name:'AES-GCM',length:256},false,['encrypt','decrypt']),iv=crypto.getRandomValues(new Uint8Array(12));return {key:secret,iv,body:await crypto.subtle.encrypt({name:'AES-GCM',iv},secret,new TextEncoder().encode(JSON.stringify(payload)))};})());
  }
  this.generation++;this.network.abort();
  const dirty=this.dirty;this.disposeEditor();this.transferPanel=null;this.note=null;this.viewPreferences=null;this.items=[];this.dirty=false;this.urls.forEach(url=>URL.revokeObjectURL(url));this.urls.clear();
  clearInterval(this.clock);this.cleanups.splice(0).forEach(fn=>fn());this.bound=false;
  this.host.classList.remove('vault-obscured');this.host.innerHTML=`<div class="vault-locked"><span aria-hidden="true">🔒</span><h2>${tHtml("security:secretSectionIsLocked")}</h2><p class="hint">${dirty?tText("security:theLastChangesAreOnlySavedInThe"):tText("security:toContinueEnterPin")}</p><button class="primary" data-vault-open>${tHtml("security:unlock")}</button></div>`;
  $('[data-vault-open]',this.host).onclick=()=>this.open();
 }
 bindHeader(){const button=$('[data-vault-lock]',this.host);if(button)button.onclick=async()=>{this.host.classList.add('vault-obscured');await this.flush();const closing=vaultApi(this.ctx,this.section,'/lock','POST');this.expire();closing.catch(error=>this.ctx.toast(error.message));};}
 async list(){
  const generation=++this.generation,data=await this.api('/items');if(this.disposed||generation!==this.generation)return;
  this.disposeEditor();this.items=data.items;this.fileLimit=data.max_file_bytes;this.fileQuota=data.quota;
  this.filesView=mountSecretDrive(this);
 }
 async newPage(parent=null,blockId=null){
  try{await loadEditor();}catch(error){this.ctx.toast(error.message);return false;}if(this.disposed)return false;
  blockId=subpagePlaceholder(this.editor?.editor,blockId);
  if(!await this.flush())return false;
  if(this.items.find(item=>item.id===parent)?.kind==='database'){const db=await this.api('/databases/'+parent);recordForm(db,null,this.databaseContext(),async()=>{await this.refreshTree();await this.openNote(parent);});return true;}
  try{
   const name=prompt(tText("security:titleOfTheSecretNote"),tText("security:untitled"));if(!name?.trim())return false;
   let note;
   if(parent){const latest=await this.api('/pages/'+parent);const result=await this.api('/notes/'+parent+'/subpages','POST',{title:name.trim(),version:latest.version,parent_blocks:latest.blocks===null?pageBlocks(latest):null,replace_block_id:blockId});note=result.page;if(this.note?.id===parent){Object.assign(this.note,result.parent);this.dirty=false;}}
   else note=await this.api('','POST',{title:name.trim()});
   await this.refreshTree();await this.openNote(note.id);return true;
  }catch(error){this.ctx.toast(error.message);return false;}
 }
 async openNote(id,draft=null){
  if(!await this.flush())return;
  const generation=++this.generation;
  try{await loadEditor();if(this.disposed||generation!==this.generation)return;let note=await this.api('/pages/'+id);if(this.disposed||generation!==this.generation)return;
   if(!draft&&!note.database){
    const children=(this.items||[]).filter(item=>item.parent_id===id).map(item=>item.id),links=pageLinkIds(note.blocks);
    if(children.some(child=>!links.includes(child))||links.some(child=>!this.items.some(item=>item.id===child))){await this.api('/notes/'+id+'/subpages','POST',{version:note.version,repair_ids:children,parent_blocks:note.blocks===null?pageBlocks(note):null});note=await this.api('/pages/'+id);await this.refreshTree();}
   }
   this.draftConflict=!!draft&&note.version!==draft.version;
   if(draft){note={...note,title:draft.title,blocks:draft.blocks};this.ctx.toast(this.draftConflict?tText("security:noteChangedOnAnotherDeviceTheUnsavedVersion"):tText("security:unsavedTextRecovered"));}
   this.disposeEditor();this.note=note;this.dirty=false;
   const prepared=JSON.parse(JSON.stringify(note));
   const ids=[...new Set([...JSON.stringify(prepared).matchAll(/\/api\/secret-vault\/notes\/content\/([a-zA-Z0-9_-]+)/g)].map(m=>m[1]).concat([note.icon,note.cover,...pageLinkIds(note.blocks).map(pid=>this.items.find(item=>item.id===pid)?.icon)].filter(value=>/^image:[a-f0-9]{32}$/.test(value||'')).map(value=>value.slice(6))))];
   for(const fid of ids){const response=await this.binary('/content/'+fid);const blob=new Blob([await response.blob()],{type:response.headers.get('X-Vault-Mime')||'application/octet-stream'});this.remember('/api/secret-vault/notes/content/'+fid,URL.createObjectURL(blob));}
   if(this.disposed||generation!==this.generation)return;
   const displayNote={...note,kind:note.database?'database':'page'};
   for(const kind of ['icon','cover'])if(note[kind]?.startsWith('image:'))displayNote[kind+'_url']=this.urls.get('/api/secret-vault/notes/content/'+note[kind].slice(6));
   const attribution=note.decorations?.find(item=>item.kind==='cover')?.payload;
   displayNote.cover_attribution=attribution?.attribution;displayNote.cover_position=attribution?.position;
   const ancestors=[],seen=new Set();let ancestor=note;
   while(ancestor&&!seen.has(ancestor.id)){seen.add(ancestor.id);ancestors.unshift(ancestor);ancestor=this.items?.find(item=>item.id===ancestor.parent_id);}
   const breadcrumbs=ancestors.map(item=>'<button class="crumb" data-vault-crumb="'+esc(item.id)+'">'+(item.id===note.id?pageIcon(displayNote):esc(item.icon?.startsWith('image:')?'📄':item.icon||'📄'))+' '+esc(item.title)+'</button>').join('<span class="muted">/</span>');
   this.host.classList.add('vault-note-page');this.host.classList.toggle('full-width',!!note.full_width);this.host.classList.toggle('has-cover',!!note.cover);this.host.classList.toggle('is-database-page',!!note.database);
   this.host.innerHTML=pageLayout(displayNote,{secret:true,breadcrumbs});
   this.mode='blocks';this.sourceDirty=false;this.viewPreferences??={font:'default',small_text:false};applyPageView(this.host,this.viewPreferences);
   $('#page-save-status',this.host).dataset.vaultStatus='';$('#retry-page-save',this.host).dataset.vaultRetry='';
   $('#block-editor',this.host).dataset.vaultEditor='';$('#record-properties-panel',this.host).dataset.vaultRecord='';
   this.bindHeader();
   this.host.querySelectorAll('[data-vault-crumb]').forEach(button=>button.onclick=()=>this.openNote(button.dataset.vaultCrumb));
   $('#page-actions',this.host).onclick=()=>this.actions();$('#favorite-page',this.host).onclick=()=>this.meta({favorite:!this.note.favorite});
   $('#change-icon',this.host).onclick=()=>iconPicker(this.decorationContext());$('#change-cover',this.host).onclick=()=>coverPicker(this.decorationContext());
   $('[data-vault-retry]',this.host).onclick=()=>this.flush();
   $('#markdown-source',this.host).oninput=()=>{this.sourceDirty=true;this.changed();};
   if(note.database){
    $('#page-title',this.host).oninput=()=>this.changed();
    this.database=mountDatabase($('[data-vault-editor]',this.host),{...note,role:'editor'},this.databaseContext(),async()=>{await this.refreshTree();});
    if(draft){if(this.draftConflict){const button=$('[data-vault-retry]',this.host);button.hidden=false;button.textContent=tText("security:applyMyName");button.onclick=()=>{this.draftConflict=false;this.changed();this.flush();};}else this.changed();}
    return;
   }
   if(note.record){const db=await this.databaseContext().api('/databases/'+note.record.database_id);if(this.disposed||generation!==this.generation)return;const record=db.records.find(r=>r.note_id===id);const box=$('[data-vault-record]',this.host);box.innerHTML=`<div class="record-property-list">${db.properties.map(p=>'<div><span>'+esc(p.name)+'</span><strong>'+esc(display(record.properties[p.id])||'—')+'</strong></div>').join('')}</div><button class="secondary compact" data-vault-record-properties>${tHtml("security:recordProperties")}</button>`;$('[data-vault-record-properties]',box).onclick=()=>recordForm(db,record,this.databaseContext(),()=>this.openNote(id));}

   if(this.draftConflict){const button=$('[data-vault-retry]',this.host);button.hidden=false;button.textContent=tText("security:saveMyVersionAsANewNote");button.onclick=async()=>{try{const created=await this.api('','POST',{title:$('#page-title',this.host).value||tText("security:untitled"),body:this.convert(await this.editor.markdown(),true),blocks:this.convert(this.editor.document(),true),parent_id:this.note.parent_id});drafts.delete(key(this.ctx,this.section));this.dirty=false;this.draftConflict=false;await this.openNote(created.id);}catch(error){this.ctx.toast(error.message);}};}
   const converted=this.convert(prepared,false);
   this.editor=mountBlockEditor($('[data-vault-editor]',this.host),converted,{secret:true,toast:message=>this.ctx.toast(message),getPage:pid=>{const item=this.items?.find(item=>item.id===pid);if(!item||!item.icon?.startsWith('image:'))return item;const url=this.urls.get('/api/secret-vault/notes/content/'+item.icon.slice(6));return {...item,icon:url?item.icon:'📄',icon_url:url};},openPage:pid=>this.openNote(pid),createSubpage:blockId=>this.newPage(id,blockId),createDatabase:blockId=>this.newDatabase(id,blockId),uploadFile:file=>this.upload(file,id),onChange:()=>this.changed()});
   $('#page-title',this.host).oninput=()=>this.changed();
   if(draft&&!this.draftConflict)this.changed();
  }catch(error){this.ctx.toast(error.message);}
 }
 async meta(fields){
  try{if(!await this.flush())return;const id=this.note.id;await this.api('/pages/'+id+'/meta','PATCH',{...fields,version:this.note.version});await this.refreshTree();await this.openNote(id);}catch(error){this.ctx.toast(error.message);}
 }
 decorationContext(){
  const panel=this,ctx=this.databaseContext();
  return {secret:true,page:this.note,ctx:{...ctx,api:async(path,method,body)=>{
   const asset=/^\/notes\/([^/]+)\/assets\/(icon|cover)$/.exec(path);
   if(asset)return (await panel.binary('/pages/'+asset[1]+'/assets/'+asset[2],body)).json();
   const cover=/^\/notes\/([^/]+)\/select-cover$/.exec(path);
   if(cover)return panel.api('/pages/'+cover[1]+'/select-cover',method,body);
   if(path.startsWith('/note-cover-search?'))return panel.ctx.api(path,method,body);
   throw Error(tText("security:actionIsNotAvailableInTheSecretSection"));
  }},persistDraft:async()=>true,flush:()=>this.flush(),meta:fields=>this.meta(fields),refresh:()=>this.refreshTree(),open:id=>this.openNote(id)};
 }
 async setMode(mode){
  if(mode===this.mode||!this.editor)return;
  if(mode==='markdown')$('#markdown-source',this.host).value=this.convert(await this.editor.markdown(),true);
  else if(this.sourceDirty){this.editor.replaceMarkdown(this.convert($('#markdown-source',this.host).value,false));this.sourceDirty=false;this.changed();}
  this.mode=mode;$('#markdown-source',this.host).hidden=mode!=='markdown';$('#block-editor',this.host).hidden=mode==='markdown';
 }
 actions(){
  if(this.closePageMenu){this.closePageMenu();return;}
  const page={...this.note,kind:this.note.database?'database':'page'};
  const words=pageWordCount(this.sourceDirty?null:this.editor?.document(),this.sourceDirty?$('#markdown-source',this.host).value:this.note.body||'');
  const close=showPageMenu({anchor:$('#page-actions',this.host),page,view:{...this.viewPreferences},mode:this.mode,editable:true,temporary:true,actions:pageActions(page,true,{secret:true}),words,
   onAction:action=>this.pageAction(action,page),onClose:()=>{this.closePageMenu=null;},
   onView:value=>{this.viewPreferences={...value};applyPageView(this.host,value);},onMode:mode=>this.setMode(mode)});
  this.closePageMenu=(focus=true)=>{close(focus);this.closePageMenu=null;};
  const menu=document.getElementById('note-page-menu');for(const type of ['pointerdown','keydown','wheel'])menu.addEventListener(type,event=>{if(event.isTrusted&&!document.hidden)this.touch();},{passive:true});
 }
 async pageAction(action,page){
  try{
   if(this.note?.id!==page.id||!await this.flush())return;
   if(action==='move-out')return this.moveOut(this.note);
   if(action==='trash')return this.remove(this.note);
   if(action==='child')return this.newPage(this.note.id);
   if(action==='move')return this.moveWithin(this.note);
   if(action==='width')return this.meta({full_width:!this.note.full_width});
   if(action==='duplicate'){const copy=await this.api('/pages/'+page.id+'/duplicate','POST',{version:this.note.version});await this.refreshTree();return this.openNote(copy.id);}
   if(action==='copy-content'){
    const text='# '+this.note.title+'\n\n'+this.convert(await this.editor.markdown(),true);
    try{await navigator.clipboard.writeText(text);this.ctx.toast(tText("security:pageContentCopied"));}catch{const el=this.databaseContext().modal(`<h2>${tHtml("security:copy")}</h2><textarea rows="6" readonly aria-label="${tHtml("security:textToCopy")}"></textarea>`);$('textarea',el).value=text;$('textarea',el).select();}return;
   }
   if(action==='import'){
    const id=this.note.id,el=this.databaseContext().modal(`<h2>${tHtml("security:importMarkdown")}</h2><label>${tHtml("security:markdownFile")}<input type="file" accept=".md,.markdown,text/markdown,text/plain"></label><p class="hint">${tHtml("security:thePageContentWillBeReplacedHistoryIn")}</p>`);
    $('input',el).onchange=async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>800000)throw Error(tText("security:markdownIsNoMoreThan200000Characters"));const text=await file.text();if(text.length>200000)throw Error(tText("security:markdownIsNoMoreThan200000Characters"));if(this.note?.id!==id||this.locked)return;this.editor.replaceMarkdown(text);this.sourceDirty=false;$('#markdown-source',this.host).value=text;this.changed();await this.flush();$('.modal-close',el).click();}catch(error){this.ctx.toast(error.message);}};
   }
  }catch(error){this.ctx.toast(error.message);}
 }
 convert(value,back){
  if(typeof value==='string'){for(const [path,url]of this.urls){if(!path.startsWith('/api/'))continue;value=back?value.split(url).join(location.origin+path):value.split(location.origin+path).join(url).split(path).join(url);}return value;}
  if(Array.isArray(value))return value.map(item=>this.convert(item,back));
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,this.convert(v,back)]));return value;
 }
 changed(){this.dirty=true;clearTimeout(this.saveTimer);this.saveTimer=setTimeout(()=>this.flush(),500);const status=$('[data-vault-status]',this.host);if(status)status.textContent=tText("security:saving");}
 async flush(){
  clearTimeout(this.saveTimer);if(this.saving){const ok=await this.saving;if(!ok)return false;}
  if(this.draftConflict)return false;
  if(!this.dirty||!this.note)return true;
  if(this.note.database){const title=$('#page-title',this.host)?.value.trim()||tText("security:untitled");try{const saved=await this.api('/notes/'+this.note.id+'/database-title','PATCH',{title,version:this.note.version});Object.assign(this.note,saved);drafts.delete(key(this.ctx,this.section));this.dirty=false;await this.refreshTree();const status=$('[data-vault-status]',this.host);if(status)status.textContent=tText("security:saved");return true;}catch(error){this.ctx.toast(error.message);return false;}}
  if(!this.editor)return true;
  if(this.sourceDirty){this.editor.replaceMarkdown(this.convert($('#markdown-source',this.host).value,false));this.sourceDirty=false;}
  const note=this.note,editor=this.editor,title=$('#page-title',this.host)?.value.trim()||tText("security:untitled");
  const generation=this.generation;this.dirty=false;
  this.saving=(async()=>{try{const body=this.convert(await editor.markdown(),true),blocks=this.convert(editor.document(),true);const saved=await this.api('/pages/'+note.id,'PUT',{title,body,blocks,parent_id:note.parent_id,version:note.version});if(!this.disposed&&generation===this.generation){this.note=saved;if(!this.dirty&&JSON.stringify(pageLinkIds(saved.blocks))!==JSON.stringify(pageLinkIds(blocks)))editor.editor.replaceBlocks(editor.editor.document,this.convert(saved.blocks,false));await this.refreshTree();drafts.delete(key(this.ctx,this.section));const status=$('[data-vault-status]',this.host);if(status)status.textContent=tText("security:saved");this.host.querySelector('[data-vault-retry]')?.setAttribute('hidden','');}return true;}
   catch(error){if(!this.disposed&&generation===this.generation){this.dirty=true;const status=$('[data-vault-status]',this.host);if(status)status.textContent=tText("security:notSaved", {value0:(error.message)});this.host.querySelector('[data-vault-retry]')?.removeAttribute('hidden');}return false;}
  })();const ok=await this.saving;this.saving=null;return ok&&(!this.dirty||await this.flush());
 }
 async binary(path,body){
  const grant=grants.get(key(this.ctx,this.section));if(!grant)throw Error(tText("security:thisSectionIsLocked"));
  return abortable(async signal=>{signal=AbortSignal.any([signal,this.network.signal]);let response=await fetch('/api/secret-vault/'+this.section+path,{method:body?'POST':'GET',body,signal,credentials:'same-origin',cache:'no-store',headers:{'X-Vault-Token':grant.token,'X-CSRF-Token':csrfCookie(),'Content-Type':'application/octet-stream'}});
   if(response.status===401&&await refreshSession())response=await fetch('/api/secret-vault/'+this.section+path,{method:body?'POST':'GET',body,signal,credentials:'same-origin',cache:'no-store',headers:{'X-Vault-Token':grant.token,'X-CSRF-Token':csrfCookie(),'Content-Type':'application/octet-stream'}});
   if(!response.ok){let message=tText("security:failedToReadTheFile");try{message=(await response.json()).detail||message;}catch{}throw Error(message);}return response;
  },{timeoutMs:120000});
 }
 remember(path,url){const previous=this.urls.get(path);if(previous)URL.revokeObjectURL(previous);if(this.disposed||this.locked){URL.revokeObjectURL(url);return '';}this.urls.set(path,url);return url;}
 async upload(file,parent,progress){if(this.section==='files'){const grant=grants.get(key(this.ctx,this.section));if(!grant)throw Error(tText("security:thisSectionIsLocked"));try{return await uploadFile('/secret-vault/files/upload?name='+encodeURIComponent(file.name)+(parent?'&parent_id='+encodeURIComponent(parent):''),file,{'X-Vault-Token':grant.token},progress,true,this.network.signal);}catch(error){if(error.status===423)this.expire();throw error;}}const response=await this.binary('/upload?name='+encodeURIComponent(file.name)+(parent?'&parent_id='+encodeURIComponent(parent):''),file),item=await response.json();return this.remember(item.url,URL.createObjectURL(file));}
 async preview(item){
  try{if(item.size>32*1024**2){const el=this.ctx.modal(`<h2>${esc(item.name)}</h2><p class="hint">${tHtml("security:downloadTheFileToViewIt")}</p><button class="secondary" data-vault-download>${tHtml("security:download")}</button>`);$('[data-vault-download]',el).onclick=()=>this.download(item.id);this.previewModal=el;return;}
   const response=await this.binary('/content/'+item.id),blob=new Blob([await response.blob()],{type:item.mime}),url=this.remember('preview:'+item.id,URL.createObjectURL(blob));if(!url)return;
   const el=this.ctx.modal(`<h2>${esc(item.name)}</h2>${item.mime?.startsWith('image/')?`<img class="vault-preview" src="${url}" alt="">`:item.mime?.startsWith('video/')?`<video class="vault-preview" src="${url}" controls></video>`:item.mime?.startsWith('audio/')?`<audio src="${url}" controls></audio>`:blob.size<2*1024**2&&(/^(text\/|application\/(json|xml|javascript))/.test(item.mime))?'<pre class="vault-text-preview" data-vault-text></pre>':`<p class="hint">${tHtml("security:downloadTheFileToViewIt2")}</p>`}<a class="secondary" href="${url}" download="${esc(item.name)}">${tHtml("security:download")}</a>`);
   if($('[data-vault-text]',el))$('[data-vault-text]',el).textContent=await blob.text();
   this.previewModal=el;
   for(const type of ['pointerdown','keydown','wheel'])el.addEventListener(type,()=>this.touch(),{passive:true});
  }catch(error){this.ctx.toast(error.message);}
 }
 async remove(item){
  if(!confirm(tText("security:permanentlyDeleteAndAllNestedItemsThereIs", {value0:(item.title||item.name)})))return;
  try{await this.api('/items/'+item.id,'DELETE');}
  catch(error){this.ctx.toast(error.message);}
  if(this.disposed||this.locked)return;
  if(this.section==='files')return this.list();
  await this.refreshTree();if(!this.items.some(note=>note.id===item.id)){this.dirty=false;this.note=null;this.ctx.vaultNoteClosed?.();}
 }
 async moveOut(item){
  if(!await this.flush())return;
  if(!confirm(tText("security:moveToTheRegularSectionTheUsualHistory")))return;
  try{
   await this.api('/move-out','POST',{id:item.id,version:(this.note?.id===item.id?this.note:item).version});
   if(this.section==='files')return this.list();
   this.dirty=false;this.note=null;this.ctx.state.notes=await this.ctx.api('/notes');await this.refreshTree();this.ctx.vaultNoteClosed?.(item.id);
  }catch(error){this.ctx.toast(error.message);}
 }
 async download(id){try{const result=Array.isArray(id)?await this.api('/prepare-selection','POST',{ids:id}):await this.api('/prepare-download/'+id,'POST');const link=document.createElement('a');link.href=result.url;link.download='';document.body.append(link);link.click();link.remove();}catch(error){this.ctx.toast(error.message);}}
 moveWithin(item){
  const candidates=this.items.filter(x=>x.id!==item.id&&!x.record&&!x.database);
  const el=this.databaseContext().modal(`<h2>${tHtml("security:moveWithinTheVault")}</h2><div class="page-action-list"><button data-vault-target="">${tHtml("security:topLevel")}</button>${candidates.map(x=>'<button data-vault-target="'+esc(x.id)+'">'+esc(x.title)+'</button>').join('')}</div>`);
  el.querySelectorAll('[data-vault-target]').forEach(b=>b.onclick=async()=>{try{const note=await this.api('/pages/'+item.id);await this.api('/pages/'+item.id,'PUT',{title:note.title,body:note.body,blocks:note.blocks,version:note.version,parent_id:b.dataset.vaultTarget||null});$('.modal-close',el).click();await this.refreshTree();await this.openNote(item.id);}catch(error){this.ctx.toast(error.message);}});
 }
 async newDatabase(parent=null,blockId=null){try{await loadEditor();}catch(error){this.ctx.toast(error.message);return;}if(this.disposed)return;blockId=subpagePlaceholder(this.editor?.editor,blockId);if(!await this.flush())return;const title=prompt(tText("security:secretDatabaseName"),tText("security:newDatabase"));if(!title?.trim())return;const result=await this.api('/databases','POST',{title:title.trim(),parent_id:parent});if(parent){const latest=await this.api('/pages/'+parent);const linked=await this.api('/notes/'+parent+'/subpages','POST',{version:latest.version,repair_ids:[result.id],parent_blocks:latest.blocks===null?pageBlocks(latest):null,replace_block_id:blockId});if(this.note?.id===parent){Object.assign(this.note,linked.parent);this.dirty=false;}}await this.refreshTree();await this.openNote(result.id);}
 async refreshTree(){const result=await this.api('/items');if(!this.disposed&&!this.locked){this.items=result.items;cacheTree(this.ctx,this.items);}}
 databaseContext(){return {secret:true,state:{notes:this.items,dirty:false},toast:message=>this.ctx.toast(message),openPage:id=>this.openNote(id),modal:html=>{const el=this.ctx.modal(html);this.secretModals??=new Set();this.secretModals.add(el);for(const type of ['pointerdown','keydown','wheel'])el.addEventListener(type,event=>{if(event.isTrusted&&!document.hidden)this.touch();},{passive:true});return el;},api:async(path,method='GET',body)=>{const result=await this.api(path,method,body);if(result?.records){for(const record of result.records)for(const field of ['icon_url','cover_url','preview_url'])if(record[field]?.startsWith('/api/secret-vault/notes/')){const target=record[field];let url=this.urls.get(target);if(!url){const response=await this.binary(target.slice('/api/secret-vault/notes'.length));url=this.remember(target,URL.createObjectURL(await response.blob()));}record[field]=url;}}return result;}};}
 disposeEditor(){this.closePageMenu?.(false);this.host.classList.remove('vault-note-page','full-width','has-cover','is-database-page','page-small-text');delete this.host.dataset.pageFont;this.filesView?.destroy();this.filesView=null;this.database?.destroy();this.database=null;for(const el of this.secretModals||[])el.querySelector('.modal-close')?.click();this.secretModals?.clear();clearTimeout(this.saveTimer);this.editor?.destroy();this.editor=null;this.previewModal?.querySelector('.modal-close')?.click();this.previewModal=null;this.hidePortals();}
 destroy(){if(this.disposed)return;const grant=grants.get(key(this.ctx,this.section));if(grant&&grant.expires*1000>Date.now())vaultApi(this.ctx,this.section,'/activity','POST').then(value=>Object.assign(grant,value)).catch(()=>{});this.disposed=true;this.generation++;this.network.abort();this.disposeEditor();this.transferPanel=null;this.note=null;this.items=[];clearInterval(this.clock);this.cleanups.splice(0).forEach(fn=>fn());this.urls.forEach(url=>URL.revokeObjectURL(url));this.urls.clear();this.host.classList.remove('secret-vault','vault-obscured');}
}
