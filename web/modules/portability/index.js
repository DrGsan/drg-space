import {formatNumber} from '../../core/i18n.js';
import {tText,tHtml} from "../../core/i18n.js";
import {sectionHeader} from '../../core/section-header.js';
import { $,esc,formBind } from '../../core/ui.js';
import { csrfCookie,refreshSession } from '../../core/auth.js';
import {notionImportDialog,outlineImportDialog} from './notion.js';

const encoded=value=>btoa(String.fromCharCode(...new TextEncoder().encode(value)));
const accountPassword=`<label>${tHtml("portability:currentAccountPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label>`;
async function transfer(path,body,extra={},retry=true){
  const response=await fetch('/api/account/data/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':body instanceof Blob?'application/octet-stream':'application/json','X-CSRF-Token':csrfCookie(),...extra},body:body instanceof Blob?body:JSON.stringify(body)});
  if(response.status===401&&retry&&await refreshSession())return transfer(path,body,extra,false);
  if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(typeof data?.detail==='string'?data.detail:tText("portability:unableToAccessPersonalArchive"));}
  return response;
}
function counts(s){
  return `<dl class="transfer-counts">${[[tText("tracker:projects"),s.tracker_projects||0],[tText("tracker:groups"),s.tracker_groups||0],[tText("tracker:title"),s.tracker_issues||0],[tText("portability:label2faServices"),s.authenticators||0],[tText("portability:eveningsAndExpenses"),s.expenses||0],[tText("portability:notes"),s.notes],[tText("portability:fromThemPersonal"),s.shared_notes],[tText("portability:fromThemInTheTrash"),s.trash],[tText("portability:noteDatabases"),s.databases],[tText("portability:noteVersions"),s.revisions],[tText("portability:myComments"),s.comments],[tText("portability:reminderLists"),s.task_lists||0],[tText("portability:fromThemPersonalLists"),s.shared_task_lists||0],[tText("portability:remindersAndNestedReminders"),s.tasks],[tText("portability:files"),s.files||0],[tText("portability:folders"),s.folders||0],[tText("portability:coversAndIcons"),s.page_images||0],[tText("portability:imagesAndGifs"),s.images],[tText("portability:fileSize"),tText("portability:mb", {value0:(formatNumber(((s.media_bytes+(s.file_bytes||0))/1048576),{minimumFractionDigits:1,maximumFractionDigits:1}))})]].map(([name,value])=>`<div><dt>${esc(name)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
}
export function mountPersonalData(host,ctx){
  host.innerHTML=`<div class="page-heading dashboard-heading"><div><p class="eyebrow">${tHtml("portability:yourData")}</p><h1>${tHtml("portability:exportAndImport")}<span class="hello-dot">.</span></h1></div><button class="secondary" data-back>${tHtml("portability:toSettings")}</button></div><section class="panel personal-transfer"><h2>${tHtml("portability:personalArchive")}</h2><p class="muted">${tHtml("portability:yourPersonalAndSharedNotesAndDatabasesPage")}</p><p class="hint">${tHtml("portability:whenImportingFromAPreviousArchiveAllNotes")}</p><p class="hint">${tHtml("portability:widgetsWithTheSelectedReminderListWhichHas")}</p><div class="admin-backup-actions"><button class="primary" data-export>${tHtml("portability:exportMyData")}</button><button class="secondary" data-import>${tHtml("portability:importMyData")}</button></div><p class="hint">${tHtml("portability:archiveLimitIsSetByTheServerImporting")}</p></section><div data-transfer-result role="status"></div>`;
  sectionHeader(host,ctx);
  const notionSection=document.createElement('section');notionSection.className='panel personal-transfer';notionSection.innerHTML=`<h2>${tHtml("portability:migratingFromNotion")}</h2><p class="muted">${tHtml("portability:transferPagesDatabasesAndNestedFilesFromA")}</p><div class="personal-transfer-actions"><button class="secondary" type="button">${tHtml("portability:importFromNotion")}</button></div>`;$('button',notionSection).onclick=()=>notionImportDialog(ctx);host.append(notionSection);
  if(ctx.state.me?.role==='owner'){const syncButton=document.createElement('button');syncButton.className='secondary';syncButton.type='button';syncButton.textContent=tText('portability:notionSync');syncButton.onclick=()=>import('./notion-sync.js').then(module=>module.notionSyncDialog(ctx));$('.personal-transfer-actions',notionSection).append(syncButton);}
  const outlineSection=document.createElement('section');outlineSection.className='panel personal-transfer';outlineSection.innerHTML=`<h2>${tHtml("portability:migratingFromOutline")}</h2><p class="muted">${tHtml("portability:transferDocumentsAndCollectionsFromAMarkdownZip")}</p><button class="secondary" type="button" data-outline-import>${tHtml("portability:transferFromOutline")}</button>`;$('button',outlineSection).onclick=()=>outlineImportDialog(ctx);host.append(outlineSection);
  $('[data-back]',host).onclick=()=>ctx.navigate('settings');
  if(ctx.state.personalImportSummary?.userId===ctx.state.me.id)$('[data-transfer-result]',host).innerHTML=`<section class="panel"><h2>${tHtml("portability:importComplete")}</h2>${counts(ctx.state.personalImportSummary.summary)}</section>`;
  $('[data-export]',host).onclick=async()=>{
    let warning='';try{warning=(await ctx.api('/secret-vault')).backup_warning;}catch(error){ctx.toast(error.message);return;}
    const el=ctx.modal(`<h2>${tHtml("portability:exportPersonalData")}</h2><p class="muted">${tHtml("portability:theFileContainsYourPersonalDataAsWell")}</p><form data-export-form>${accountPassword}<label>${tHtml("portability:archivePassword")}<input name="archive_password" type="password" autocomplete="new-password" minlength="12" maxlength="256" required></label><label>${tHtml("portability:reEnterArchivePassword")}<input name="repeat" type="password" autocomplete="new-password" minlength="12" maxlength="256" required></label><button class="primary" type="submit">${tHtml("portability:downloadPersonalArchive")}</button><p data-status class="hint" role="status"></p></form>`);
    if(warning)$('[data-export-form]',el).insertAdjacentHTML('beforebegin','<p class="vault-warning">'+esc(warning)+'</p>');
    formBind($('[data-export-form]',el),async form=>{
      if(form.get('archive_password')!==form.get('repeat'))throw new Error(tText("portability:archivePasswordsDoNotMatch"));
      $('[data-status]',el).textContent=tText("portability:creatingAnEncryptedArchive");
      try{
        const response=await transfer('export',{password:form.get('password'),archive_password:form.get('archive_password')});
        const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='drg-personal-'+new Date().toISOString().slice(0,10)+'.drgpersonal';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
        $('.modal-close',el).click();ctx.toast(tText("portability:personalArchiveDownloadedSaveThePasswordSeparately"));
      }finally{if(el.isConnected)$('[data-status]',el).textContent='';}
    });
  };
  $('[data-import]',host).onclick=()=>{
    const el=ctx.modal(`<h2>${tHtml("portability:importPersonalData")}</h2><p class="muted">${tHtml("portability:firstWeWillCheckTheFileAndShow")}</p><form data-import-form><label>${tHtml("portability:personalArchive")}<input name="file" type="file" accept=".drgpersonal" required></label>${accountPassword}<label>${tHtml("portability:archivePassword")}<input name="archive_password" type="password" autocomplete="off" minlength="12" maxlength="256" required></label><label class="check-label"><input name="settings" type="checkbox" checked>${tHtml("portability:transferLayoutModulesAndWidgets")}</label><p class="hint">${tHtml("portability:whenSelectingTheMainPageSettingTheSettings")}</p><button class="primary" type="submit">${tHtml("portability:checkArchive")}</button><p data-status class="hint" role="status"></p><div data-preview></div></form>`);
    const form=$('[data-import-form]',el);let staged,headers,generation=0;
    const reset=()=>{generation++;staged=null;headers=null;$('[data-preview]',el).innerHTML='';};
    form.querySelectorAll('input').forEach(input=>input.addEventListener('input',reset));
    formBind(form,async data=>{
      reset();const requestGeneration=generation,file=data.get('file');if(!file?.size)throw new Error(tText("portability:selectPersonalArchive"));
      const nextHeaders={'X-Account-Password-B64':encoded(data.get('password')),'X-Archive-Password-B64':encoded(data.get('archive_password')),'X-Import-Settings':data.has('settings')?'true':'false'};
      $('[data-status]',el).textContent=tText("portability:checkingArchiveAndItsContents");
      try{
        const result=await (await transfer('preview',file,nextHeaders)).json();
        if(!el.isConnected||generation!==requestGeneration)return;
        staged=file;headers=nextHeaders;
        $('[data-preview]',el).innerHTML=`<h3>${tHtml("portability:archiveChecked")}</h3>${counts(result.summary)}<p class="hint">${tHtml("portability:newCopiesOfPersonalDataWillBeAdded", {value0:(data.has('settings')?tText("portability:settingsForModulesLayoutAndWidgetsFromThe"):tText("portability:currentSettingsWillRemain"))})}</p><label class="check-label"><input data-confirm type="checkbox">${tHtml("portability:iUnderstandThatImportingWillAddCopiesOf")}</label><button type="button" class="primary" data-confirm-import disabled>${tHtml("portability:addDataToMyAccount")}</button>`;
        const button=$('[data-confirm-import]',el);$('[data-confirm]',el).onchange=e=>button.disabled=!e.target.checked;
        button.onclick=async()=>{
          button.disabled=true;$('[data-status]',el).textContent=tText("portability:importingData");
          form.querySelectorAll('input,button').forEach(field=>field.disabled=true);
          try{
            const r=await (await transfer('import',staged,{...headers,'X-Import-Confirmation':'IMPORT'})).json();
            $('.modal-close',el).click();ctx.state.personalImportSummary={userId:ctx.state.me.id,summary:r.imported};ctx.state.me=await ctx.api('/me');await ctx.loadWorkspace();
            if(host.isConnected)$('[data-transfer-result]',host).innerHTML=`<section class="panel"><h2>${tHtml("portability:importComplete")}</h2>${counts(r.imported)}</section>`;
            ctx.toast(tText("portability:importCompleteNotesRemindersFiles", {value0:(r.imported.notes), value1:(r.imported.tasks), value2:(r.imported.images)}));
          }catch(error){ctx.toast(error.message);if(el.isConnected){form.querySelectorAll('input,button').forEach(field=>field.disabled=false);button.disabled=!$('[data-confirm]',el).checked;}}
          finally{if(el.isConnected)$('[data-status]',el).textContent='';}
        };
      }finally{if(el.isConnected)$('[data-status]',el).textContent='';}
    });
  };
}
