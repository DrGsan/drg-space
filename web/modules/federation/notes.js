import {tText,tHtml} from "../../core/i18n.js";
import * as Y from 'yjs';
import {Awareness} from 'y-protocols/awareness';
import {$,esc} from '../../core/ui.js';
import {mountBlockEditor,pageBlocks} from '../notes/editor-runtime.js';
import {pageLayout} from '../notes/page-layout.js';
import {mountDatabase,recordForm,display} from '../notes/database.js';
import {databaseIntent,databasePayload,validateDatabasePayload} from './database-protocol.js';
import {contacts,operate,base64,unbase64,saveDraft,loadDraft,peerOrigin,assetBlobMime} from './client.js';
import './federation.css';

const noteId=note=>note.note_id||note.id;
export class FederatedNotes {
  constructor(workspace,ctx){this.workspace=workspace;this.ctx=ctx;this.uid=ctx.state.me.id;this.sequence=0;this.assets=new Map();this.dbResources=new Map();this.pending=[];this.active=false;this.closed=false;}
  async mount(){
    this.list=document.createElement('section');this.list.className='federation-remote-tree';this.list.hidden=true;this.list.setAttribute('aria-label',tText("federation:notesFromOtherServers"));$('#page-tree',this.workspace.host).after(this.list);
    try{
      const info=await this.ctx.api('/federation/info');if(!info.enabled||this.closed||!this.list.isConnected){this.list.hidden=true;return;}
      this.people=(await contacts(this.ctx)).filter(person=>person.status==='accepted');this.drawList();
      const requested=this.ctx.state.federationContact;delete this.ctx.state.federationContact;
      if(requested&&this.people.some(person=>person.peer_id===requested.peer_id&&person.actor_id===requested.actor_id))await this.openContact(requested);
    }catch{this.list.hidden=true;}
  }
  drawList(){
    if(this.closed||!this.list?.isConnected)return;
    this.list.hidden=!this.people?.length;
    if(this.list.hidden){this.list.replaceChildren();return;}
    this.list.innerHTML=`<div class="tree-section-heading"><span class="tree-section-label">${tHtml("federation:otherServers")}</span><button class="icon-btn" data-refresh-remote aria-label="${tHtml("federation:updateSharedNotes")}">↻</button></div>${this.people?.length?this.people.map((person,index)=>`<button class="favorite-page" data-remote-contact="${index}"><span class="tree-page-icon">↗</span><span class="tree-page-title">${esc(person.display_name||person.login||person.actor_id)}</span></button>`).join(''):`<p class="hint">${tHtml("federation:addAFriendFromAnotherServerInThe")}</p>`}<div data-remote-catalog></div>`;
    $('[data-refresh-remote]',this.list).onclick=()=>this.contact?this.openContact(this.contact):this.mountRefresh();
    this.list.querySelectorAll('[data-remote-contact]').forEach(button=>button.onclick=()=>this.openContact(this.people[Number(button.dataset.remoteContact)]));
    if(this.catalog)this.drawCatalog();
  }
  async mountRefresh(){try{this.people=(await contacts(this.ctx)).filter(person=>person.status==='accepted');this.drawList();}catch(error){this.ctx.toast(error.message);}}
  async openContact(contact){
    this.contact=contact;const box=$('[data-remote-catalog]',this.list);box.innerHTML=`<p class="hint">${tHtml("federation:loadingSharedPages")}</p>`;
    try{const result=await operate(this.ctx,contact.peer_id,contact.actor_id,'catalog',{});if(this.closed||this.contact!==contact)return;this.catalog=result.notes||[];this.drawCatalog();}
    catch(error){if(box.isConnected)box.innerHTML=`<p class="hint">${esc(error.message)}</p>`;}
  }
  drawCatalog(){
    const box=$('[data-remote-catalog]',this.list);if(!box)return;
    const render=(item,depth=0,seen=new Set())=>{const id=noteId(item);if(seen.has(id)||depth>30)return '';const chain=new Set(seen).add(id);return `<button class="favorite-page" data-remote-note="${esc(id)}" style="padding-left:${.6+depth*.8}rem"><span class="tree-page-icon">${esc(item.icon?.startsWith('image:')?'📄':item.icon||'📄')}</span><span class="tree-page-title">${esc(item.title||tText("federation:untitled"))}</span></button>`+this.catalog.filter(child=>!child.database_id&&child.parent_id===id).map(child=>render(child,depth+1,chain)).join('');};
    box.innerHTML=this.catalog?.length?this.catalog.filter(item=>!item.database_id&&(!item.parent_id||!this.catalog.some(parent=>noteId(parent)===item.parent_id))).map(item=>render(item)).join(''):`<p class="hint">${tHtml("federation:yourFriendHasNotYetGrantedYouAccess")}</p>`;
    box.querySelectorAll('[data-remote-note]').forEach(button=>button.onclick=()=>this.open(this.contact,this.catalog.find(item=>noteId(item)===button.dataset.remoteNote)));
  }
  async open(contact,summary){
    if(!summary||!await this.workspace.flush())return;
    await this.workspace.releaseLease(true);this.workspace.discardDraft();this.workspace.vault?.destroy();this.workspace.vault=null;this.workspace.live?.destroy();this.workspace.live=null;this.workspace.handle?.destroy();this.workspace.handle=null;this.workspace.database?.destroy();this.workspace.database=null;this.workspace.discussions?.destroy();clearInterval(this.workspace.readTimer);clearTimeout(this.workspace.timer);clearTimeout(this.workspace.maxSaveTimer);
    this.close();this.active=true;this.contact=contact;this.summary=summary;this.workspace.page=null;this.ctx.state.selected=null;this.ctx.state.notesList=false;this.ctx.state.dirty=false;
    const token=++this.sequence;this.key=contact.peer_id+':'+noteId(summary);const host=$('#notion-page',this.workspace.host);host.innerHTML=`<p class="muted">${tHtml("federation:openingSharedPage")}</p>`;
    history.replaceState({},'','/');if(this.workspace.narrowSidebar.matches)this.workspace.sidebar(false);
    let saved;try{saved=await loadDraft(this.ctx,this.key);}catch(error){this.ctx.toast(error.message);}
    try{
      let page=await this.request('read',{note_id:noteId(summary),revision:summary.revision});if(!this.current(token))return;
      if(page.legacy&&!page.state&&page.role==='editor'){page=await this.request('database_record_initialize',{note_id:noteId(summary),revision:page.revision??summary.revision,version:page.version,blocks:pageBlocks({...page,blocks:null},page.web_origin||await peerOrigin(this.ctx,this.contact.peer_id))});if(!this.current(token))return;}
      this.page={...summary,...page,id:noteId(summary),space_id:summary.space_id||'federation',title:page.title||summary.title||tText("federation:untitled")};this.epoch=page.epoch;
      if(this.page.kind==='database'){this.drawPage();if(saved?.database_title!==undefined){$('#page-title',host).value=saved.database_title;this.databaseTitleDirty=true;this.ctx.state.dirty=true;if(saved.version!==this.page.version)this.fail(new Error(tText("federation:theDatabaseNameHasBeenChangedOnThe")),true);else this.status('error');}return;}
      if(this.page.legacy&&!this.page.state){this.drawPage();this.schedulePoll();return;}
      this.doc=new Y.Doc();Y.applyUpdate(this.doc,unbase64(page.state),'remote');
      if(saved){if(saved.epoch!==page.epoch){this.doc.destroy();this.doc=new Y.Doc();Y.applyUpdate(this.doc,unbase64(saved.state),'remote');this.page=saved.page;this.epoch=saved.epoch;this.pending=(saved.pending||[]).map(item=>({eventId:item.eventId,update:unbase64(item.update)}));this.ctx.state.dirty=!!this.pending.length;this.drawPage();this.fail(new Error(tText("federation:theServerHasRestoredADifferentVersionOf")),true);return;}else{Y.applyUpdate(this.doc,unbase64(saved.state),'remote');this.pending=(saved.pending||[]).map(item=>({eventId:item.eventId,update:unbase64(item.update)}));this.ctx.state.dirty=this.pending.length>0;}}
      this.drawPage();this.observe();this.schedulePoll();if(this.pending.length)this.flush();
    }catch(error){if(!this.current(token))return;if(saved){this.page=saved.page;this.epoch=saved.epoch;if(saved.database_title!==undefined){this.drawPage();$('#page-title',host).value=saved.database_title;this.databaseTitleDirty=true;this.ctx.state.dirty=true;this.fail(error,true);return;}this.doc=new Y.Doc();Y.applyUpdate(this.doc,unbase64(saved.state),'remote');this.pending=(saved.pending||[]).map(item=>({eventId:item.eventId,update:unbase64(item.update)}));this.ctx.state.dirty=!!this.pending.length;this.drawPage();this.fail(error,true);}else{host.innerHTML=`<div class="federation-remote-error"><h2>${tHtml("federation:pageNotAvailable")}</h2><p>${esc(error.message)}</p><button class="secondary" data-remote-back>${tHtml("federation:toSharedNotes")}</button></div>`;$('[data-remote-back]',host).onclick=()=>{this.close();this.workspace.empty();};}}
  }
  current(sequence){return !this.closed&&this.active&&sequence===this.sequence&&this.ctx.state.me?.id===this.uid;}
  request(operation,payload,options){return operate(this.ctx,this.contact.peer_id,this.contact.actor_id,operation,payload,options);}
  drawPage(){
    const host=$('#notion-page',this.workspace.host),page=this.page;host.classList.remove('is-database-page');host.classList.toggle('full-width',!!page.full_width);host.classList.toggle('has-cover',!!page.cover);
    const decoration={...page,icon:page.icon?.startsWith('image:')?'📄':page.icon,icon_url:undefined,cover_url:undefined};
    host.innerHTML=pageLayout(decoration,{breadcrumbs:`<button class="text-link" data-remote-back>${tHtml("federation:sharedNotes")}</button><span>/</span><span>${esc(page.title)}</span>`});
    const sequence=this.sequence;for(const kind of ['icon','cover'])if(page[kind+'_url'])this.assetUrl(page[kind+'_url']).then(url=>{if(!this.current(sequence)||this.accessLost)return;const image=document.createElement('img');image.src=url;image.alt=kind==='icon'?tText("federation:pageIcon"):'';if(kind==='icon'){image.className='page-custom-icon';$('#change-icon',host).replaceChildren(image);}else{image.style.objectPosition='center '+Math.min(100,Math.max(0,page.cover_position??50))+'%';$('.page-cover',host).replaceChildren(image);}}).catch(()=>{});
    const source=document.createElement('p');source.className='federation-remote-source';source.textContent=tText("federation:friendSServer", {value0:(this.contact.origin||this.contact.peer_id)});$('.page-top',host).after(source);
    const closePage=async()=>{if(!await this.flush())return;this.close();this.workspace.empty();};$('[data-remote-back]',host).onclick=closePage;
    $('#share-page',host).hidden=true;$('#favorite-page',host).hidden=true;$('#change-icon',host).disabled=true;$('#change-cover',host).hidden=true;$('#discussions-panel',host).hidden=true;
    $('#page-actions',host).onclick=()=>this.actions();$('#retry-page-save',host).onclick=()=>this.flush(true);
    if(page.kind==='database'){this.drawDatabase(host,sequence);return;}
    if(page.legacy&&!page.state){this.editor=mountBlockEditor($('#block-editor',host),{...page,blocks:null},{readOnly:true,fileOrigin:page.web_origin||this.contact.origin,getPage:id=>this.resource(id),openPage:id=>this.openRecord(id),resolveFileUrl:value=>this.assetUrl(value).catch(error=>{if(this.current(sequence))this.fail(error);return 'data:application/octet-stream;base64,';})});$('#page-title',host).value=page.title;$('#page-title',host).disabled=true;$('.editor-help',host).textContent=tText("federation:pageIsReadOnly");$('#live-presence',host).textContent=tText("federation:sharedEntryReadOnly");this.status('saved');if(page.database_id||page.record_database)this.drawRecordProperties(host,sequence);return;}
    this.awareness=new Awareness(this.doc);const user={id:this.uid,name:this.ctx.state.me.display_name||this.ctx.state.me.login,color:'#'+this.uid.slice(0,6)};
    const readonly=page.role!=='editor';
    this.editor=mountBlockEditor($('#block-editor',host),page,{readOnly:readonly,collaboration:{fragment:this.doc.getXmlFragment('prosemirror'),user,provider:{awareness:this.awareness},showCursorLabels:'always'},
      getPage:id=>this.catalog?.find(item=>noteId(item)===id),openPage:id=>this.open(this.contact,this.catalog?.find(item=>noteId(item)===id)),
      createSubpage:blockId=>this.createChild(blockId),createDatabase:blockId=>this.createDatabase(blockId),toast:message=>this.ctx.toast(message),
      resolveFileUrl:value=>this.assetUrl(value).catch(error=>{if(this.current(sequence))this.fail(error);return 'data:application/octet-stream;base64,';}),uploadFile:file=>this.upload(file)});
    const title=$('#page-title',host);title.value=this.doc.getMap('meta').get('title')||page.title;title.disabled=readonly;title.oninput=()=>this.doc.getMap('meta').set('title',title.value.trim()||tText("federation:untitled"));
    $('.editor-help',host).textContent=readonly?tText("federation:pageIsReadOnly"):tText("federation:changesAreSavedOnThePageOwnerS");
    $('#live-presence',host).textContent=tText("federation:sharedPage", {value0:(readonly?tText("federation:readOnly"):tText("federation:coEditing"))});this.status(this.pending.length?'saving':'saved');
    if(page.database_id||page.record_database)this.drawRecordProperties(host,sequence);
  }
  resource(id){return id===this.page?.id?this.page:this.dbResources.get(id)||this.catalog?.find(item=>noteId(item)===id);}
  databaseContext(){
    const sequence=this.sequence,ctx=this.ctx,state={...ctx.state,notes:this.catalog||[]};Object.defineProperty(state,'dirty',{get:()=>ctx.state.dirty,set:value=>ctx.state.dirty=value});
    return {...ctx,state,databaseScope:'federation:'+this.contact.peer_id+':'+this.contact.actor_id+':',
      openPage:id=>this.openRecord(id),resolveFileUrl:value=>this.assetUrl(value).catch(error=>{if(this.current(sequence))this.fail(error);return 'data:application/octet-stream;base64,';}),
      api:async(path,method='GET',data)=>{
        if(!this.current(sequence)||this.accessLost)throw new Error(tText("federation:thisSharedPageHasAlreadyBeenClosed"));
        const mapped=databaseIntent(path,method,data,id=>this.resource(id));
        try{const result=await this.request(mapped.operation,databasePayload(mapped.payload));if(!this.current(sequence))throw new Error(tText("federation:thisSharedPageHasAlreadyBeenClosed"));
          if(mapped.operation==='database_read'||mapped.operation==='database_configure'){validateDatabasePayload(result);this.rememberDatabase(result);}else if(mapped.operation==='database_catalog'){const entries=Array.isArray(result)?result:result.databases;validateDatabasePayload(entries,{catalog:true});return entries;}return result;
        }catch(error){if(this.current(sequence)&&['database_read','database_title'].includes(mapped.operation)&&[401,403,404,410,423].includes(error.status))this.fail(error);throw error;}
      }};
  }
  rememberDatabase(db){
    const parent=this.resource(db.note_id);for(const [id,item] of this.dbResources)if(item.database_id===db.note_id)this.dbResources.delete(id);this.dbResources.set(db.note_id,{...parent,id:db.note_id,note_id:db.note_id,kind:'database',revision:db.revision??parent?.revision,role:db.role});
    for(const row of db.records||[]){const id=row.note_id||row.id;this.dbResources.set(id,{...row,id,note_id:id,kind:'page',database_id:db.note_id,parent_id:db.note_id,revision:row.revision??db.revision??parent?.revision,role:row.role||db.role});}
    if(this.page?.id===db.note_id){this.page.role=db.role;if(!this.databaseTitleDirty){this.page.version=db.note_version;this.page.title=db.title;}}
  }
  async openRecord(id){const summary=this.resource(id);if(summary)await this.open(this.contact,summary);else this.ctx.toast(tText("federation:theEntryIsNoLongerAvailableUpdateShared"));}
  drawDatabase(host,sequence){
    host.classList.add('is-database-page');$('#block-editor',host).hidden=true;$('.editor-help',host).textContent=tText("federation:viewsPropertiesAndEntriesAreSavedOnThe");
    const heading=document.createElement('div');heading.className='database-page-heading';const titleInput=$('#page-title',host);titleInput.before(heading);heading.append($('#change-icon',host),titleInput);
    $('#live-presence',host).textContent=tText("federation:sharedDatabase", {value0:(this.page.role==='editor'?tText("federation:editing"):tText("federation:readOnly"))});
    const title=$('#page-title',host);title.value=this.page.title;title.disabled=this.page.role!=='editor';title.oninput=()=>{this.databaseTitleDirty=true;this.ctx.state.dirty=true;this.status('saving');this.persist().catch(error=>this.ctx.toast(error.message));clearTimeout(this.saveTimer);this.saveTimer=setTimeout(()=>this.flush(),350);};
    this.database=mountDatabase($('#database-container',host),this.page,this.databaseContext(),async()=>{if(this.current(sequence))await this.openContact(this.contact);});this.status('saved');
  }
  async drawRecordProperties(host,sequence){
    try{const adapter=this.databaseContext(),limited=this.page.record_database?validateDatabasePayload(this.page.record_database,{limited:true}):null,db=limited?{...limited,records:[limited.record]}:await adapter.api('/databases/'+this.page.database_id);if(!this.current(sequence))return;const record=db.records.find(row=>row.note_id===this.page.id);if(!record)return;const box=$('#record-properties-panel',host);box.innerHTML='<div class="record-property-list">'+db.properties.map(property=>'<div><span>'+esc(property.name)+'</span><strong>'+esc(display(record.properties[property.id])||'—')+'</strong></div>').join('')+'</div>'+((this.page.role==='editor'&&db.role==='editor')?`<button class="secondary compact" id="edit-record-properties">${tHtml("federation:entryProperties")}</button>`:'');$('#edit-record-properties',box)?.addEventListener('click',()=>recordForm(db,record,adapter,()=>this.open(this.contact,this.page)));}
    catch(error){if(this.current(sequence)&&!this.accessLost)this.ctx.toast(error.message);}
  }
  observe(){
    this.doc.on('update',(update,origin)=>{if(origin==='remote'||!this.active)return;this.pending.push({eventId:crypto.randomUUID().replaceAll('-',''),update});this.ctx.state.dirty=true;this.status('saving');clearTimeout(this.saveTimer);this.saveTimer=setTimeout(()=>this.flush(),350);this.persist().catch(error=>this.ctx.toast(error.message));});
    this.doc.getMap('meta').observe(()=>{if(!this.active)return;this.page.title=this.doc.getMap('meta').get('title')||tText("federation:untitled");const input=$('#page-title',this.workspace.host);if(document.activeElement!==input)input.value=this.page.title;});
  }
  status(value){const host=$('#notion-page',this.workspace.host),label=$('#page-save-status',host),retry=$('#retry-page-save',host);if(label)label.textContent={saved:tText("federation:savedFriendSServer"),saving:tText("federation:saving"),offline:tText("federation:noConnectionDraftOnDevice"),error:tText("federation:accessOrSavingIsUnavailable")}[value]||value;if(retry)retry.hidden=!['offline','error'].includes(value);}
  persist(){if(!this.active||!this.doc&&this.page?.kind!=='database')return Promise.resolve();const key=this.key,ctx={...this.ctx,state:{...this.ctx.state,me:{...this.ctx.state.me}}},snapshot=this.page.kind==='database'?(this.databaseTitleDirty?{page:this.page,database_title:$('#page-title',this.workspace.host).value,version:this.page.version}:null):this.pending.length?{page:this.page,epoch:this.epoch,state:base64(Y.encodeStateAsUpdate(this.doc)),pending:this.pending.map(item=>({eventId:item.eventId,update:base64(item.update)}))}:null;this.draftWrite=(this.draftWrite||Promise.resolve()).catch(()=>{}).then(()=>saveDraft(ctx,key,snapshot));return this.draftWrite;}
  async flush(notify=false){
    if(!this.active)return true;if(this.saving)return this.saving;if(this.accessLost)return !this.pending.length&&!this.databaseTitleDirty;
    if(this.page?.kind==='database')return this.flushDatabaseTitle(notify);
    if(!this.pending.length)return true;
    const sequence=this.sequence;
    this.saving=(async()=>{try{
      while(this.current(sequence)&&this.pending.length){
        const batch=this.pending[0],result=await this.request('update',{note_id:this.page.id,revision:this.page.revision,epoch:this.epoch,update:base64(batch.update)},{eventId:batch.eventId});if(!this.current(sequence))return false;
        if(result.epoch!==this.epoch)throw Object.assign(new Error(tText("federation:thePageVersionHasChangedYourDraftIs")),{status:409});
        Y.applyUpdate(this.doc,unbase64(result.state),'remote');this.pending.shift();this.page={...this.page,...result};this.ctx.state.dirty=!!this.pending.length;await this.persist();
      }
      if(!this.current(sequence))return false;this.status('saved');if(notify)this.ctx.toast(tText("federation:sharedPageSaved"));return true;
    }catch(error){if(this.current(sequence)){await this.persist().catch(()=>{});this.fail(error);}return false;}finally{this.saving=null;}})();return this.saving;
  }
  async flushDatabaseTitle(notify){
    if(!this.databaseTitleDirty)return true;const sequence=this.sequence,input=$('#page-title',this.workspace.host),raw=input.value,title=raw.trim()||tText("federation:untitled"),version=this.page.version;
    this.saving=(async()=>{try{const result=await this.databaseContext().api('/notes/'+this.page.id+'/database-title','PATCH',{title,version});if(!this.current(sequence))return false;Object.assign(this.page,result);this.page.title=title;if(input.value===raw)input.value=title;this.databaseTitleDirty=input.value.trim()!==title;this.ctx.state.dirty=this.databaseTitleDirty;await this.persist();this.status(this.databaseTitleDirty?'saving':'saved');if(notify)this.ctx.toast(tText("federation:databaseNameSaved"));return !this.databaseTitleDirty;}
      catch(error){if(this.current(sequence)){await this.persist().catch(()=>{});this.fail(error,error.status===409);this.ctx.toast(error.message);}return false;}finally{this.saving=null;}})();return this.saving;
  }
  schedulePoll(){clearTimeout(this.pollTimer);this.pollTimer=setTimeout(()=>this.poll(),3000);}
  async poll(){
    if(!this.active||this.closed||this.accessLost)return;
    if(document.hidden||this.saving){this.schedulePoll();return;}
    const sequence=this.sequence;
    try{const result=await this.request('read',{note_id:this.page.id,revision:this.page.revision});if(!this.current(sequence))return;if(this.page.legacy&&!this.doc){if(result.state||result.version!==this.page.version){await this.open(this.contact,{...this.page,...result});return;}this.status('saved');return;}if(result.epoch!==this.epoch)throw Object.assign(new Error(tText("federation:aDifferentVersionOfThePageHasBeen")),{status:409});Y.applyUpdate(this.doc,unbase64(result.state),'remote');if(result.role&&result.role!==this.page.role){this.page.role=result.role;this.editor.editor.isEditable=result.role==='editor';$('#page-title',this.workspace.host).disabled=result.role!=='editor';}if(!this.pending.length)this.status('saved');}
    catch(error){if(this.current(sequence))this.fail(error);}
    finally{if(this.current(sequence)&&!this.accessLost)this.schedulePoll();}
  }
  fail(error,locked=false){
    this.status('offline');
    if(locked||[401,403,404,409,423].includes(error.status)){
      this.accessLost=true;clearTimeout(this.pollTimer);if(this.editor)this.editor.editor.isEditable=false;const host=$('#notion-page',this.workspace.host);$('#page-title',host).disabled=true;
      this.database?.destroy();host.querySelectorAll('#database-container button,#database-container input,#database-container select,#edit-record-properties').forEach(input=>input.disabled=true);
      let warning=$('[data-remote-access-lost]',host);if(!warning){warning=document.createElement('div');warning.dataset.remoteAccessLost='';warning.className='federation-remote-error';$('.page-top',host).after(warning);}
      warning.innerHTML=`<p>${esc(error.message)}</p><p class="hint">${(this.pending.length||this.databaseTitleDirty)?tText("federation:unsavedTextRemainsOnThisDeviceCopyIt"):tText("federation:newPageDataIsNoLongerBeingLoaded")}</p><button class="secondary compact" data-copy-remote-draft>${tHtml("federation:copyText")}</button><button class="text-link" data-close-remote>${tHtml("federation:closePage")}</button>${(this.pending.length||this.databaseTitleDirty)?`<button class="text-link danger-link" data-discard-remote-draft>${tHtml("federation:deleteLocalDraft")}</button>`:''}`;
      $('[data-copy-remote-draft]',warning).onclick=()=>navigator.clipboard.writeText(this.page.kind==='database'?$('#page-title',host).value:this.editor?.markdown()||this.page.body||'').then(()=>this.ctx.toast(tText("federation:textCopied"))).catch(()=>this.ctx.toast(tText("federation:manuallySelectAndCopyThePageText")));
      $('[data-close-remote]',warning).onclick=()=>{if((this.pending.length||this.databaseTitleDirty)&&!confirm(tText("federation:theDraftWillRemainEncryptedOnTheDevice")))return;this.close();this.workspace.empty();};this.status('error');
      $('[data-discard-remote-draft]',warning)?.addEventListener('click',async()=>{if(!confirm(tText("federation:deleteUnsavedLocalDraftFirstCopyTheRequired")))return;try{await saveDraft(this.ctx,this.key,null);this.close();this.workspace.empty();}catch(problem){this.ctx.toast(problem.message);}});
    }
  }
  async createChild(blockId){
    if(!this.active||this.page.role!=='editor'||!await this.flush())return;
    try{const result=await this.request('create',{parent_id:this.page.id,title:tText("federation:untitled"),blocks:[]});if(blockId&&this.editor){const block=this.editor.editor.getBlock(blockId);if(block)this.editor.editor.replaceBlocks([block],[{type:'pageLink',props:{pageId:noteId(result),title:result.title||tText("federation:untitled")}}]);if(!await this.flush())return;}await this.openContact(this.contact);const summary=this.catalog.find(item=>noteId(item)===noteId(result));if(summary)await this.open(this.contact,summary);}catch(error){this.ctx.toast(error.message);}
  }
  async createDatabase(blockId){
    if(!this.active||this.page.role!=='editor'||!await this.flush())return;
    const dialog=this.ctx.modal(`<h2>${tHtml("federation:newNotesBase")}</h2><form data-remote-new-database><label>${tHtml("federation:baseName")}<input name="title" maxlength="200" value="${tHtml('notes:newDatabaseDefault')}" required></label><button class="primary" type="submit">${tHtml("federation:createBase")}</button><p class="error" role="alert"></p></form>`);
    $('[data-remote-new-database]',dialog).onsubmit=async event=>{event.preventDefault();const button=$('[type=submit]',dialog);button.disabled=true;try{const result=await this.databaseContext().api('/databases','POST',{parent_id:this.page.id,title:$('[name=title]',dialog).value.trim()});$('.modal-close',dialog).click();await this.openContact(this.contact);const summary=this.resource(result.id||result.note_id);if(summary)await this.open(this.contact,summary);}catch(error){$('.error',dialog).textContent=error.message;}finally{button.disabled=false;}};
  }
  async assetUrl(value){
    if(this.accessLost)throw new Error(tText("federation:accessToPageAttachmentsIsClosed"));
    let url;try{url=new URL(value,this.contact.origin||location.origin);}catch{return value;}
    let match=/^\/api\/notes\/([A-Za-z0-9_-]+)\/(?:files|assets)\/([A-Za-z0-9_-]+)$/.exec(url.pathname);const media=/^\/api\/media\/([A-Za-z0-9_-]+)$/.exec(url.pathname);if(!match&&media)match=[media[0],this.page.id,media[1]];
    if(!match)return value;
    if(match[1]!==this.page.id&&!this.resource(match[1]))throw new Error(tText("federation:attachmentBelongsToAnInaccessiblePage"));
    const key=match[1]+':'+match[2];if(this.assets.has(key))return this.assets.get(key);
    const sequence=this.sequence,contact=this.contact,revision=this.resource(match[1])?.revision||this.page.revision;
    const work=(async()=>{const parts=[];let offset=0,mime='application/octet-stream';for(let count=0;count<200;count++){if(!this.current(sequence)||this.accessLost)throw new Error(tText("federation:pageIsAlreadyClosed"));const result=await operate(this.ctx,contact.peer_id,contact.actor_id,'asset_read',{note_id:match[1],revision,asset_id:match[2],offset,length:262144});if(!this.current(sequence)||this.accessLost)throw new Error(tText("federation:pageIsAlreadyClosed"));const bytes=unbase64(result.body);if(result.offset!==offset||result.more&&!bytes.length)throw new Error(tText("federation:invalidAttachmentSequence"));parts.push(bytes);mime=result.mime||mime;offset+=bytes.length;if(offset>50*1024*1024)throw new Error(tText("federation:attachmentExceeds50Mb"));if(!result.more)return URL.createObjectURL(new Blob(parts,{type:assetBlobMime(mime)}));}throw new Error(tText("federation:failedToRetrieveTheEntireAttachment"));})();this.assets.set(key,work);work.catch(()=>{if(this.assets.get(key)===work)this.assets.delete(key);});return work;
  }
  async upload(file){
    if(file.size>50*1024*1024)throw new Error(tText("federation:theFileMustBeNoMoreThan50"));
    const sequence=this.sequence,payload={note_id:this.page.id,revision:this.page.revision},started=await this.request('asset_upload_start',{...payload,name:file.name,mime:file.type||'application/octet-stream',size:file.size});let offset=0;
    while(offset<file.size){if(!this.current(sequence))throw new Error(tText("federation:pageIsAlreadyClosed"));const bytes=new Uint8Array(await file.slice(offset,offset+262144).arrayBuffer()),result=await this.request('asset_upload_chunk',{...payload,upload_id:started.upload_id,offset,body:base64(bytes)});if(result.offset!==offset+bytes.length)throw new Error(tText("federation:theServerHasNotConfirmedTheUploadOf"));offset=result.offset;}
    if(!this.current(sequence))throw new Error(tText("federation:pageIsAlreadyClosed"));const result=await this.request('asset_upload_finish',{...payload,upload_id:started.upload_id});return new URL(result.url,await peerOrigin(this.ctx,this.contact.peer_id)).href;
  }
  actions(){
    const database=this.page.kind==='database',dialog=this.ctx.modal('<h2>'+(database?tText("federation:mainNotesDatabase"):tText("federation:mainPage"))+'</h2><div class="page-action-list">'+(!database?`<button data-remote-copy>${tHtml("federation:copyText")}</button>`:'')+(this.page.role==='editor'?(!database?`<button data-remote-child>${tHtml("federation:createSubpage")}</button><button data-remote-database>${tHtml("federation:createNotesDatabase")}</button>`:'')+'<button class="danger-link" data-remote-delete>'+(database?tText("federation:deleteDatabase"):tText("federation:deletePage"))+'</button>':'')+'</div>');
    $('[data-remote-copy]',dialog)?.addEventListener('click',()=>navigator.clipboard.writeText(this.editor.markdown()).then(()=>{$('.modal-close',dialog).click();this.ctx.toast(tText("federation:textCopied"));}).catch(()=>this.ctx.toast(tText("federation:manuallySelectAndCopyTheText"))));
    $('[data-remote-child]',dialog)?.addEventListener('click',()=>{$('.modal-close',dialog).click();this.createChild();});
    $('[data-remote-database]',dialog)?.addEventListener('click',()=>{$('.modal-close',dialog).click();this.createDatabase();});
    $('[data-remote-delete]',dialog)?.addEventListener('click',async()=>{if(!confirm(tText("federation:deleteTheMainPageOnTheOwnerS")))return;try{await this.request('delete',{note_id:this.page.id,revision:this.page.revision});$('.modal-close',dialog).click();this.close();this.workspace.empty();await this.openContact(this.contact);}catch(error){this.ctx.toast(error.message);}});
  }
  close(){
    ++this.sequence;this.active=false;this.accessLost=false;clearTimeout(this.pollTimer);clearTimeout(this.saveTimer);this.database?.destroy();this.database=null;this.dbResources.clear();this.databaseTitleDirty=false;this.editor?.destroy();this.editor=null;this.awareness?.destroy();this.awareness=null;this.doc?.destroy();this.doc=null;this.pending=[];this.ctx.state.dirty=false;for(const value of this.assets.values())Promise.resolve(value).then(url=>URL.revokeObjectURL(url)).catch(()=>{});this.assets.clear();
  }
  destroy(){this.closed=true;this.close();}
}
