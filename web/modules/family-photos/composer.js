import {tText,tHtml} from "../../core/i18n.js";
import {$,esc} from '../../core/ui.js';
import {keepComposerFocus,lockComposer} from '../chat/composer.js';
import {attachmentMenu} from '../chat/attachment-menu.js';
import {cameraCapture} from '../chat/camera.js';
import {voiceRecording,recordingActions} from '../chat/voice-recording.js';
import {previewFile} from '../chat/uploads.js';
import {prepareVideos} from '../chat/media.js';
import {attachmentSize} from '../chat/message-layout.js';
import {personName} from '../../core/people.js';

export function telegramComposer(host,ctx,gid,{sent,changed,closeMenus=()=>{}}){
  const form=$('[data-fp-compose]',host),input=$('#fp-message',form),attach=$('[data-attach-chat]',form),submit=$('[type=submit]',form),fileInput=$('[data-chat-files]',form),status=$('[data-chat-upload-status]',form),cancel=$('[data-cancel-chat-upload]',form);
  const base=`/family-photos/groups/${encodeURIComponent(gid)}`;
  let files=[],reply=null,editing=null,sending=null,uploading=null,closed=false,menu,camera,voice,retry=null,mode='preview';
  const abort=new AbortController();
  const request=(path,method='GET',body,options={},headers={})=>ctx.api(path,method,body,true,headers,{signal:abort.signal,...options});
  if(ctx.state.telegramDrafts?.userId!==ctx.state.me.id)ctx.state.telegramDrafts={userId:ctx.state.me.id,items:new Map()};
  const drafts=ctx.state.telegramDrafts.items;const saved=drafts.get(gid);if(saved)input.value=saved.text||'';
  const resize=()=>{input.style.height='auto';input.style.height=Math.min(150,Math.max(24,input.scrollHeight))+'px';drafts.set(gid,{text:input.value});form.dispatchEvent(new CustomEvent('drg-composer-change'));};
  keepComposerFocus(form,input);input.oninput=resize;resize();
  const controls=()=>{attach.disabled=submit.disabled=!!uploading||!!sending;cancel.hidden=!uploading;};
  const deleteFile=file=>request(`${base}/site-uploads/${file.id}`,'DELETE').catch(()=>{});
  const detach=removed=>{const ids=new Set(removed.map(file=>file.id));files=files.filter(file=>!ids.has(file.id));for(const file of removed)deleteFile(file);retry=null;drawFiles();};
  const drawFiles=()=>{
    const target=$('[data-chat-drafts]',form);
    target.innerHTML=files.map(file=>`<span>${(file.kind==='photo'||file.kind==='animation')?`<img src="${esc(file.thumbnail_url||file.url)}" alt="">`:['video','video_note'].includes(file.kind)?`<video muted playsinline preload="metadata" src="${esc(file.url)}" aria-label="${tHtml("family_photos:previewVideoBeforeSending")}"></video>`:''}<b>${esc(file.name)}</b><small>${attachmentSize(file.size)}</small><button type="button" data-remove-draft="${file.id}" aria-label="${tHtml("family_photos:removeAttachment", {value0:(esc(file.name))})}">×</button></span>`).join('')+(files.length>1?`<button type="button" class="text-link" data-clear-drafts>${tHtml("family_photos:removeAllAttachments")}</button>`:'');
    prepareVideos(target);target.querySelectorAll('[data-remove-draft]').forEach(button=>button.onclick=()=>{if(!sending)detach(files.filter(file=>file.id===button.dataset.removeDraft));});
    $('[data-clear-drafts]',target)?.addEventListener('click',()=>{if(!sending)detach([...files]);});
  };
  const drawReply=()=>{
    const target=$('[data-chat-reply]',form);target.hidden=!reply||!!editing;
    target.innerHTML=reply?`<div><strong>${tHtml("family_photos:reply", {value0:(esc(personName(reply.site_profile||{login:reply.sender})))})}</strong><span>${esc(reply.text||tText("family_photos:attachment"))}</span></div><button type="button" class="chat-icon-button" data-cancel-reply aria-label="${tHtml("family_photos:cancelReply")}">×</button>`:'';
    $('[data-cancel-reply]',target)?.addEventListener('click',()=>{reply=null;retry=null;drawReply();});
  };
  const drawEdit=()=>{
    const target=$('[data-chat-edit]',form);target.hidden=!editing;
    target.innerHTML=editing?`<div><strong>${tHtml("family_photos:editing")}</strong><span>${esc(editing.message.text||tText("family_photos:attachment"))}</span></div><button type="button" class="chat-icon-button" data-cancel-edit aria-label="${tHtml("family_photos:cancelEditing")}">×</button>`:'';
    submit.setAttribute('aria-label',editing?tText("family_photos:saveChanges"):tText("family_photos:send"));submit.title=editing?tText("family_photos:saveChanges"):tText("family_photos:send");
    $('[data-cancel-edit]',target)?.addEventListener('click',()=>{if(!sending){input.value=editing.draft;editing=null;drawEdit();drawReply();resize();}});
    drawReply();
  };
  async function upload(selected,uploadMode='preview',recording=null){
    if(closed||form.hidden||sending||uploading||!selected.length)return;
    if(editing)return ctx.toast(tText("family_photos:firstSaveOrCancelEditingTheMessage"));
    if(files.length+selected.length>10)return ctx.toast(tText("family_photos:upTo10AttachmentsInOneMessage"));
    if(selected.some(file=>file.size>50*1024*1024))return ctx.toast(tText("family_photos:eachAttachmentShouldNotExceed50Mb"));
    const batch=new AbortController();uploading=batch;controls();cancel.textContent=tText("family_photos:cancelUpload");
    try{
      for(const [index,file] of selected.entries()){
        status.textContent=tText("family_photos:uploadingFrom", {value0:(index+1), value1:(selected.length), value2:(file.name)});
        const prepared=uploadMode==='file'||recording?file:await previewFile(file,{signal:batch.signal});
        if(closed||batch.signal.aborted)return;
        const id=crypto.randomUUID().replaceAll('-','');
        // UUID is chosen before the request so an interrupted upload can be discarded.
        const removeUnknown=()=>ctx.api(`${base}/site-uploads/${id}`,'DELETE').catch(()=>{});
        try{
          const stored=await request(`${base}/site-uploads/${id}?name=${encodeURIComponent(prepared.name)}&mode=${uploadMode}`,'PUT',prepared,{signal:batch.signal,timeoutMs:120000},recording?{'X-DrG-Recording':JSON.stringify(recording)}:{});
          if(recording&&stored.kind!==recording.kind){await removeUnknown();throw Error(tText('chat:recordingServerUpdateRequired'));}
          if(closed||batch.signal.aborted){await removeUnknown();return;}
          files.push(stored);retry=null;drawFiles();
        }catch(error){removeUnknown();throw error;}
      }
      status.textContent=tText("family_photos:attachmentsAreReadyToSend");return true;
    }catch(error){if(!closed)status.textContent=batch.signal.aborted?tText("family_photos:uploadCancelledYouCanSendTheText"):error.message;return false;}
    finally{if(uploading===batch){uploading=null;controls();fileInput.value='';}}
  }
  const actions=recordingActions(form,ctx,{isCurrent:()=>!closed&&!form.hidden,canRecord:()=>!closed&&!form.hidden&&!sending&&!uploading&&!editing,onOpen:()=>{menu?.close();camera?.close();voice?.close();},onFile:(file,recording)=>upload([file],'preview',recording)});
  cancel.onclick=()=>uploading?.abort();
  attach.setAttribute('aria-haspopup','menu');attach.setAttribute('aria-expanded','false');
  attach.onclick=()=>{
    if(menu?.element.isConnected){menu.close(true);menu=null;return;}
    closeMenus();$('[data-chat-emoji-panel]',form).hidden=true;
    menu=attachmentMenu(attach,choice=>{
      mode=choice==='file'?'file':'preview';
      if(choice==='voice'||choice==='video_note'){actions.close();voice?.close();voice=voiceRecording(ctx,{kind:choice,isCurrent:()=>!closed&&!form.hidden,onFile:(file,recording)=>upload([file],'preview',recording)});return;}
      if(choice==='camera'&&!matchMedia('(pointer:coarse)').matches){camera?.close();voice?.close();camera=cameraCapture(ctx,{isCurrent:()=>!closed&&!form.hidden,onFile:file=>upload([file])});return;}
      fileInput.accept=choice==='file'?'':'image/*,video/*';fileInput.multiple=choice!=='camera';
      if(choice==='camera')fileInput.setAttribute('capture','environment');else fileInput.removeAttribute('capture');fileInput.click();
    });
  };
  fileInput.onchange=()=>upload([...fileInput.files],mode);
  input.onpaste=event=>{if(event.clipboardData.files.length){event.preventDefault();upload([...event.clipboardData.files]);}};
  $('[data-chat-emoji]',form).onclick=()=>{menu?.close();const panel=$('[data-chat-emoji-panel]',form);panel.hidden=!panel.hidden;};
  form.querySelectorAll('[data-insert-emoji]').forEach(button=>button.onclick=()=>{if(sending)return;input.setRangeText(button.dataset.insertEmoji,input.selectionStart,input.selectionEnd,'end');$('[data-chat-emoji-panel]',form).hidden=true;input.focus();resize();});
  input.onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}if(event.key==='Escape'){menu?.close();$('[data-chat-emoji-panel]',form).hidden=true;}};
  const drop=document.createElement('div');drop.className='chat-drop-overlay';drop.hidden=true;drop.innerHTML=`<div data-drop-mode="file"><span>${tHtml("family_photos:dragFilesHereToSend")}<strong>${tHtml("family_photos:withoutCompressionAsFiles")}</strong></span></div><div data-drop-mode="preview"><span>${tHtml("family_photos:dragPhotosAndVideosHere")}<strong>${tHtml("family_photos:quickWayWithPreview")}</strong></span></div>`;host.append(drop);
  const hideDrop=()=>{drop.hidden=true;};
  const drag=event=>{if(form.hidden||!event.dataTransfer?.types.includes('Files'))return;event.preventDefault();drop.hidden=false;};
  const leave=event=>{if(!host.contains(event.relatedTarget))hideDrop();};
  const dropped=event=>{if(!event.dataTransfer?.files.length)return;event.preventDefault();hideDrop();upload([...event.dataTransfer.files],event.target.closest('[data-drop-mode]')?.dataset.dropMode||'preview');};
  host.addEventListener('dragenter',drag);host.addEventListener('dragover',drag);host.addEventListener('dragleave',leave);host.addEventListener('drop',dropped);
  form.onsubmit=async event=>{
    event.preventDefault();if(closed||form.hidden||sending||uploading)return;
    const text=input.value.trim();if(!text&&!files.length&&!editing)return;
    const payload=editing?{text,revision:editing.message.site_revision,client_id:retry?.client_id||crypto.randomUUID()}:{text,attachments:files.map(file=>file.id),reply_to:reply?.id??null,client_id:retry?.client_id||crypto.randomUUID()};
    // Changing an ambiguous request is a new action; unchanged retries keep its ID.
    if(retry&&JSON.stringify({...retry,client_id:null})!==JSON.stringify({...payload,client_id:null}))payload.client_id=crypto.randomUUID();
    retry=payload;const unlock=lockComposer(input);controls();
    sending=(async()=>{
      try{
        const edited=!!editing;
        const result=await request(edited?`${base}/messages/${editing.message.id}`:`${base}/messages`,edited?'PUT':'POST',payload,{timeoutMs:30000});
        if(closed)return;
        if(edited){input.value=editing.draft;editing=null;drawEdit();status.textContent=tText("family_photos:changesAreSentToTelegram");changed?.();}
        else{input.value='';files=[];reply=null;drawFiles();drawReply();status.textContent='';sent(result);}
        retry=null;resize();
      }catch(error){if(!closed)status.textContent=error.message;}
      finally{unlock();sending=null;if(!closed)controls();}
    })();controls();await sending;
  };
  return {
    reply(message){if(form.hidden||sending)return;reply=message;retry=null;drawReply();input.focus();},
    edit(message){if(form.hidden||sending||uploading)return;if(files.length)return ctx.toast(tText("family_photos:firstSendOrRemoveAttachedFiles"));editing={message,draft:input.value};input.value=message.text;retry=null;drawEdit();resize();input.focus();},
    writable(allowed){form.hidden=!allowed;actions.update();if(!allowed){actions.close();menu?.close();camera?.close();voice?.close();uploading?.abort();}},
    async flush(){if(sending)await sending;return true;},
    close(){actions.close();menu?.close();camera?.close();voice?.close();$('[data-chat-emoji-panel]',form).hidden=true;},
    destroy(){actions.destroy();closed=true;uploading?.abort();abort.abort();menu?.close();camera?.close();voice?.close();for(const file of files)ctx.api(`${base}/site-uploads/${file.id}`,'DELETE').catch(()=>{});drop.remove();host.removeEventListener('dragenter',drag);host.removeEventListener('dragover',drag);host.removeEventListener('dragleave',leave);host.removeEventListener('drop',dropped);},
  };
}
