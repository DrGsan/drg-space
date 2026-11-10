import {tText,tHtml} from "../../core/i18n.js";
import {$,esc,formBind} from '../../core/ui.js';
import {bindDownload} from './download.js';

export async function fileEditor(file,ctx,changed,options={}){
  const prefix=options.prefix||'/files',url=prefix+'/'+file.id,write=options.write!==false;
  const el=ctx.modal(`<h2>${esc(file.name)}</h2><div data-file-preview>${tHtml("files:readingFile")}</div><a class=secondary href="/api${url}/content">${tHtml("files:downloadFile")}</a>`);el.classList.add('file-editor-dialog');
  if(options.download){const link=el.querySelector('a');link.removeAttribute('href');link.setAttribute('role','button');link.onclick=options.download;}
  else bindDownload(el.querySelector('a'),ctx);
  try{
    const data=await ctx.api(url+'/text');if(!el.isConnected)return;
    $('[data-file-preview]',el).innerHTML=`<form data-file-edit><label>${tHtml("files:fileContent")}<textarea name=text rows=16 spellcheck=false ${write?'':'readonly'}>${esc(data.text)}</textarea></label>${write?`<button class=primary type=submit>${tHtml("files:saveFile")}</button>`:`<p class=hint>${tHtml("files:readOnlyAccess")}</p>`}<p role=status data-file-save-status></p></form>`;
    let version=data.version;
    if(write)formBind($('[data-file-edit]',el),async values=>{const headers=options.refreshGrant?await options.refreshGrant():options.headers||{};const saved=await ctx.api(url+'/text','PUT',{text:values.get('text'),version},true,headers);version=saved.version;$('[data-file-save-status]',el).textContent=tText("files:fileSaved");changed?.();});
  }catch(e){if(!el.isConnected)return;$('[data-file-preview]',el).innerHTML='<p class=muted>'+esc(e.message)+'</p>';}
}
