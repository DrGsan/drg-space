import {formatNumber} from '../../core/i18n.js';
import {locale,tText,tHtml} from "../../core/i18n.js";
import {$,esc} from '../../core/ui.js';
import {csrfCookie,refreshSession} from '../../core/auth.js';
import './import-progress.css';

function progress(status,text,busy=false,value=null){
  status.textContent=text;status.dataset.busy=String(busy);
  const bar=status.parentElement.querySelector('[data-import-progress]');
  if(bar){bar.hidden=!busy;if(value===null)bar.removeAttribute('value');else bar.value=value;}
}

async function upload(source,path,file,headers,status,retry=true){
  try{return await new Promise((resolve,reject)=>{
    const request=new XMLHttpRequest();
    request.open('POST','/api/account/'+source+'/'+path);
    request.setRequestHeader('Content-Type','application/zip');
    request.setRequestHeader('X-CSRF-Token',csrfCookie());
    for(const [key,value] of Object.entries({'Accept-Language':locale(),...headers}))request.setRequestHeader(key,value);
    request.upload.onprogress=event=>{
      if(event.lengthComputable)progress(status,tText("portability:uploadingZip", {value0:(Math.round(100*event.loaded/event.total))}),true,event.loaded/event.total);
    };
    request.upload.onload=()=>progress(status,path==='preview'?tText("portability:checkingTreeLinksAndAttachments"):tText("portability:transferringNotesDatabasesAndAttachmentsThisMayTake"),true);
    request.onload=()=>{
      let result;try{result=JSON.parse(request.responseText);}catch{}
      if(request.status>=200&&request.status<300&&result)resolve(result);
      else reject(Object.assign(new Error(typeof result?.detail==='string'?result.detail:tText("portability:zipCouldNotBeProcessed", {value0:(request.status)})),{status:request.status}));
    };
    request.onerror=()=>reject(new Error(path==='import'?tText(headers['X-Import-Operation']?'portability:importRetrySafe':'portability:connectionInterruptedCheckTheNotesSectionBeforeRe'):tText("portability:couldNotUploadZipCheckYourConnection")));
    request.send(file);
  });}catch(error){
    // Only an authentication rejection is replayed automatically. Network errors
    // leave the archive selected for an explicit retry; import keeps its receipt.
    if(error.status===401&&retry&&await refreshSession())return upload(source,path,file,headers,status,false);
    throw error;
  }
}

function summary(value,imported=false){
  const assets=Object.entries(value.page_images||{}).map(([kind,counts])=>`<p>${esc(tText(kind==='icon'?'portability:pageIcons':'portability:pageCovers'))}: ${esc(imported?`${counts.saved} / ${counts.expected}`:counts.expected)}</p>`).join('');
  const reasons={property_as_text:tText('portability:propertyAsText'),missing_from_archive:tText('portability:missingExportTarget'),unavailable_image:tText('portability:sourceUnavailable'),unsupported_image:tText('portability:unsupportedImage')};
  const issues=value.issues?.length?`<details class="import-issues"><summary>${tHtml('portability:importIssues',{count:value.issues.length})}</summary><ul>${value.issues.map(issue=>`<li><strong>${esc(issue.page)}</strong> — ${esc(reasons[issue.reason]||issue.reason)}<br><small>${esc(issue.source)}</small></li>`).join('')}</ul></details>`:'';

  return assets+issues+summaryCounts(value);
}

function summaryCounts(value){
  return `<dl class="transfer-counts">${[[tText("portability:pages"),value.notes],[tText("portability:ofWhichAreDatabases"),value.databases],[tText("portability:attachments"),value.files],[tText("portability:externalImages"),value.remote_images||0],[tText("portability:ofWhichAreSavedOnOurSide"),value.downloaded_images||0],[tText("portability:attachmentSize"),tText("portability:mb", {value0:(formatNumber((value.file_bytes/1048576),{minimumFractionDigits:1,maximumFractionDigits:1}))})]].map(([label,count])=>`<div><dt>${esc(label)}</dt><dd>${esc(count)}</dd></div>`).join('')}</dl><p>${tHtml("portability:rootPages", {value0:(value.roots.map(esc).join(', '))})}</p>${value.warnings.map(warning=>`<p class="hint">${esc(warning)}</p>`).join('')}${value.unavailable_images?.length?`<details><summary>${tHtml("portability:failedToSaveImages", {value0:(value.unavailable_images.length)})}</summary><ul>${value.unavailable_images.map(url=>`<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a></li>`).join('')}</ul><p>${tHtml("portability:theseImagesRemainAsExternalLinksAndMay")}</p></details>`:''}`;
}

export const notionImportDialog=(ctx,files)=>externalImportDialog(ctx,"notion",files);
export const outlineImportDialog=ctx=>externalImportDialog(ctx,"outline");
function externalImportDialog(ctx,source,initialFiles){
  const outline=source==="outline",title=outline?"Outline":"Notion";
  const el=ctx.modal(`<h2>${tHtml("portability:moveFrom", {value0:(title)})}</h2><p class="muted">${outline?tText("portability:inOutlineSelectTheExportOfTheCollection"):tText("portability:inNotionSelectHtmlOrMarkdownCsvExport")}</p><p class="hint">${tHtml("portability:pagesDatabasesAndFilesWillBeAddedTo", {value0:(outline?tText("portability:outlineParticipantsAccessRightsAndHistoryAreNot"):tText("portability:notionParticipantsAccessRightsVersionHistoryFormulasAnd"))})}</p><form data-notion-form data-import-source="${source}"><label>${tHtml("portability:exportZipUpTo512Mb", {value0:(title)})}<input name="file" type="file" accept=".zip,application/zip" multiple required></label><button class="primary" type="submit">${tHtml("portability:checkArchive")}</button><p class="hint" role="status" data-notion-status></p><div data-notion-preview></div></form>`);
  const form=$('[data-notion-form]',el),input=$('input',form),preview=$('[data-notion-preview]',el),status=$('[data-notion-status]',el);
  if(initialFiles?.length){const transfer=new DataTransfer();for(const file of initialFiles)transfer.items.add(file);input.files=transfer.files;}
  let generation=0,busy=false,importing=false;
  el.addEventListener('click',event=>{if(importing&&event.target===el){event.preventDefault();event.stopImmediatePropagation();}},true);
  el.addEventListener('keydown',event=>{if(importing&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();}},true);
  const bar=document.createElement('progress');bar.max=1;bar.hidden=true;bar.dataset.importProgress='';bar.setAttribute('aria-label',tText('portability:archiveProgress'));status.before(bar);
  const filesLabel=document.createElement('p');filesLabel.className='hint import-file-names';input.after(filesLabel);
  const selected=()=>{if(busy)return;generation++;preview.innerHTML='';filesLabel.textContent=[...input.files].map(file=>file.name).join(', ');progress(status,tText('portability:archiveSelected'),true);form.requestSubmit();};
  input.onchange=selected;
  form.onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    const files=[...input.files];if(!files.length)return;
    if(files.length>20){progress(status,tText("portability:selectNoMoreThan20ZipFiles"));return;}
    const file=new Blob(files,{type:'application/zip'}),headers={['X-'+title+'-Parts']:JSON.stringify(files.map(item=>item.size))};
    if(file.size>512*1048576){progress(status,tText("portability:theZipFileShouldNotExceed512Mb"));return;}
    const current=++generation;preview.innerHTML='';busy=true;
    progress(status,tText('portability:startingArchiveUpload'),true,0);
    form.querySelectorAll('input,button').forEach(node=>node.disabled=true);
    try{
      const result=await upload(source,'preview',file,headers,status);
      if(!el.isConnected||current!==generation)return;
      preview.innerHTML=`<h3>${tHtml("portability:archiveChecked")}</h3>${summary(result)}<p class="hint">${tHtml("portability:newPersonalPagesWillBeCreatedReUploading")}</p><button class="primary" type="button" data-notion-import>${tHtml("portability:addToMyNotes")}</button>`;
      const pendingKey=`drg-import-pending:${ctx.state?.me?.id||''}:${source}:${result.sha256}`;
      let operation;try{operation=sessionStorage.getItem(pendingKey);}catch{}
      operation ||= crypto.randomUUID();
      $('[data-notion-import]',el).onclick=async()=>{
        if(busy)return;busy=true;importing=true;
        const button=$('[data-notion-import]',el);button.textContent=tText('portability:addingToNotes');
        progress(status,tText('portability:addingToNotes'),true,0);
        form.querySelectorAll('input,button').forEach(node=>node.disabled=true);
        $('.modal-close',el).disabled=true;
        try{
          if(result.idempotent_import)try{sessionStorage.setItem(pendingKey,operation);}catch{}
          const value=await upload(source,'import',file,{...headers,'X-Import-Confirmation':'IMPORT','X-Import-Operation':operation,['X-'+title+'-Preview']:result.sha256},status);
          try{sessionStorage.removeItem(pendingKey);}catch{}
          preview.innerHTML=`<h3>${tHtml("portability:importComplete")}</h3>${summary(value.imported,true)}<button class="primary" type="button" data-open-notes>${tHtml("portability:openNotes")}</button>`;
          $('[data-open-notes]',el).onclick=()=>{$('.modal-close',el).click();ctx.navigate('notes');};
          ctx.toast(tText("portability:pagesTransferredAttachments", {value0:(value.imported.notes), value1:(value.imported.files)}));
          progress(status,tText("portability:done"));
          try{await ctx.loadWorkspace();}catch{progress(status,tText('portability:importSavedRefreshNeeded'));}
        }catch(error){progress(status,error.message);button.textContent=tText('portability:retryImport');}
        finally{busy=false;importing=false;$('.modal-close',el).disabled=false;form.querySelectorAll('input,button').forEach(node=>node.disabled=false);}
      };
      progress(status,tText('portability:readyToAddNotes'));
    }catch(error){progress(status,error.message);}
    finally{busy=false;form.querySelectorAll('input,button').forEach(node=>node.disabled=false);}
  };
  if(initialFiles?.length)queueMicrotask(selected);
}
