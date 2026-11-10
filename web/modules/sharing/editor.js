import {tText,tHtml} from "../../core/i18n.js";
import { $,esc } from '../../core/ui.js';
import { mountBlockEditor } from '../notes/editor-runtime.js';

export function mountSharedEditor(host,page,token,ctx){
  host.innerHTML=`<form data-share-edit-form><label>${tHtml("sharing:publishedPageTitle")}<input name="title" maxlength="200" value="${esc(page.title)}" required></label><div class="tabs"><button type="button" class="tab active" data-rich>${tHtml("sharing:blocks")}</button><button type="button" class="tab" data-markdown>Markdown</button></div><div data-editor></div><label data-source hidden>${tHtml("sharing:pageContentInMarkdown")}<textarea name="body" rows="12" maxlength="200000"></textarea></label><div class="share-editor-actions"><button class="secondary" type="button" data-copy-draft>${tHtml("sharing:copyMyText")}</button><button class="text-link" type="button" data-retry-save hidden>${tHtml("sharing:reSave")}</button><span class="hint" data-save-status role="status">${tHtml("sharing:changesAreSavedAutomatically")}</span></div><p class="error" data-edit-error role="alert"></p></form>`;
  let mode='blocks',version=page.version,disposed=false,dirty=false,generation=0,observedDocument,timer,maxTimer,pending;
  const status=text=>{if(!disposed)$('[data-save-status]',host).textContent=text;};
  const dirtyChange=value=>{if(value)generation++;dirty=value;ctx.onDirty(value);};
  const changed=()=>{
    if(disposed)return;dirtyChange(true);status(tText("sharing:changesPending"));clearTimeout(timer);
    timer=setTimeout(flush,350);maxTimer??=setTimeout(()=>{maxTimer=null;flush();},2000);
  };
  const handle=mountBlockEditor($('[data-editor]',host),page,{fileUrl:fid=>location.origin+'/api/shared/'+encodeURIComponent(token)+'/files/'+encodeURIComponent(fid),onChange:()=>{
    const document=JSON.stringify(handle.document());
    if(document!==observedDocument){observedDocument=document;changed();}
  }});
  observedDocument=JSON.stringify(handle.document());
  $('[name=title]',host).oninput=changed;
  $('[name=body]',host).oninput=changed;
  $('[data-rich]',host).onclick=()=>{
    if(mode==='markdown'){handle.replaceMarkdown($('[name=body]',host).value);observedDocument=JSON.stringify(handle.document());}
    mode='blocks';$('[data-source]',host).hidden=true;$('[data-editor]',host).hidden=false;
    $('[data-rich]',host).classList.add('active');$('[data-markdown]',host).classList.remove('active');
  };
  $('[data-markdown]',host).onclick=()=>{
    if(mode==='blocks')$('[name=body]',host).value=handle.markdown();
    mode='markdown';$('[data-source]',host).hidden=false;$('[data-editor]',host).hidden=true;
    $('[data-rich]',host).classList.remove('active');$('[data-markdown]',host).classList.add('active');
  };
  $('[data-copy-draft]',host).onclick=async()=>{
    try{await navigator.clipboard.writeText(mode==='markdown'?$('[name=body]',host).value:handle.markdown());ctx.toast(tText("sharing:textCopied"));}
    catch{if(mode==='blocks')$('[data-markdown]',host).onclick();$('[name=body]',host).select();ctx.toast(tText("sharing:copyTheSelectedText"));}
  };
  async function flush(){
    clearTimeout(timer);clearTimeout(maxTimer);maxTimer=null;
    if(disposed)return false;
    if(pending){const ok=await pending;return ok&&dirty?flush():ok;}
    if(!dirty)return true;
    status(tText("sharing:saving"));$('[data-edit-error]',host).textContent='';$('[data-retry-save]',host).hidden=true;
    if(mode==='markdown')handle.replaceMarkdown($('[name=body]',host).value);
    const title=$('[name=title]',host).value.trim()||tText("sharing:untitled"),blocks=handle.document(),savedGeneration=generation;
    observedDocument=JSON.stringify(blocks);
    const body=handle.markdown();
    pending=(async()=>{try{
      const access=await ctx.api('/shared/'+token);
      if(access.permission!=='edit'||!access.edit_token)throw new Error(tText("sharing:editingPermissionsHaveBeenRevokedYourTextRemains"));
      const saved=await ctx.api('/shared/'+token,'PUT',{title,body,blocks,version},true,{'X-Share-Edit-Token':access.edit_token});
      version=saved.version;
      if(disposed)return true;
      if(generation===savedGeneration){dirtyChange(false);status(tText("sharing:changesSaved"));}
      else status(tText("sharing:savingNewChanges"));
      return true;
    }catch(error){if(!disposed){status(tText("sharing:changesNotSaved"));$('[data-edit-error]',host).textContent=error.message;$('[data-retry-save]',host).hidden=false;}return false;}
    finally{pending=null;}})();
    const ok=await pending;return ok&&dirty&&!disposed?flush():ok;
  }
  $('[data-retry-save]',host).onclick=flush;
  $('[data-share-edit-form]',host).onsubmit=e=>{e.preventDefault();flush();};
  const online=()=>{if(dirty)flush();};window.addEventListener('online',online);
  return {flush,destroy(){disposed=true;clearTimeout(timer);clearTimeout(maxTimer);window.removeEventListener('online',online);handle.destroy();dirtyChange(false);},get dirty(){return dirty;}};
}
