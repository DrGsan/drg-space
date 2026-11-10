import {tText,tHtml} from "../../core/i18n.js";
import {$,esc,date} from '../../core/ui.js';
import {mountBlockEditor,pageBlocks} from './editor-runtime.js';
import {compareBlocks} from './history-diff.js';
import './history.css';

export function mountHistory(host,ctx,pageId,onClose,{trashPage=null}={}){
  const screen=document.createElement('section');screen.className='note-history';screen.setAttribute('aria-label',tText("notes:pageHistory"));
  screen.innerHTML=`<aside class="history-sidebar"><header><span class="muted small">${tHtml("notes:pageHistory")}</span><h1></h1><label>${tHtml("notes:group")} <select aria-label="${tHtml("notes:groupVersions")}"><option value="0">${tHtml("notes:ungrouped")}</option><option value="60">${tHtml("notes:byMinutes")}</option><option value="3600">${tHtml("notes:byHours")}</option><option value="86400">${tHtml("notes:byDays")}</option></select></label><p class="small muted">${tHtml("notes:versionsInTheLast90Days")}</p></header><nav aria-label="${tHtml("notes:savedVersions")}"></nav><button class="text-link history-more" hidden>${tHtml("notes:showMore")}</button></aside><main class="history-main"><header class="history-toolbar"><button class="text-link history-list-toggle" aria-expanded="false">${tHtml("notes:versions")}</button><strong class="history-date"></strong><label class="history-compare"><input type="checkbox"> ${tHtml("notes:compareWithCurrent")}</label><button class="primary history-restore" disabled>${tHtml("notes:restore")}</button><button class="icon-btn history-close" aria-label="${tHtml("notes:closeHistory")}">✕</button></header><div class="history-status" role="status"></div><div class="history-confirm" hidden><p>${tHtml("notes:restoreSelectedVersionTheCurrentContentWillBe")}</p><button class="primary" data-confirm>${tHtml("notes:restoreVersion")}</button><button class="secondary" data-cancel>${tHtml("notes:cancel")}</button></div><div class="history-scroll"><article class="history-document"><h2></h2><div class="history-content rich-editor"></div></article></div></main>`;
  host.append(screen);
  let current,revisions=[],selected=null,detail=null,preview,observer,disposed=false,sequence=0,loading=false,more=false,restoring=false;
  const $s=selector=>$(selector,screen),status=$s('.history-status'),compare=$s('.history-compare input'),restore=$s('.history-restore'),confirm=$s('.history-confirm');
  const base='/notes/'+pageId,path='/page/'+pageId+'/history';
  function notice(message){status.textContent=message;}
  function controls(){restore.hidden=!!trashPage;restore.disabled=!!trashPage||!detail||restoring;compare.disabled=!detail||restoring;$s('.history-more').disabled=loading||restoring;}
  function timeline(){
    const group=Number($s('select').value),groups=[];
    for(const revision of revisions){const key=group?Math.floor(revision.created/group):revision.id;let entry=groups.at(-1);if(!entry||entry.key!==key)groups.push(entry={key,items:[]});entry.items.push(revision);}
    const row=r=>`<button data-revision="${esc(r.id)}" aria-current="${selected===r.id?'true':'false'}"><time>${esc(date(r.created))}</time><span>${esc(r.title)}</span></button>`;
    $s('nav').innerHTML=(current?`<button data-current aria-current="${selected===null?'true':'false'}"><time>${esc(date(current.updated))}</time><span>${tHtml("notes:currentVersion")}</span></button>`:'')+groups.map(g=>g.items.length===1?row(g.items[0]):`<details ${g.items.some(r=>r.id===selected)?'open':''}><summary>${tHtml("notes:versions2", {value0:(esc(date(g.items[0].created))), value1:(g.items.length)})}</summary>${g.items.map(row).join('')}</details>`).join('')+(!revisions.length&&!loading?`<p class="muted small history-empty">${tHtml("notes:noPreviousVersionsYetTheyWillAppearAfter")}</p>`:'');
    $s('[data-current]')?.addEventListener('click',()=>select(null));
    screen.querySelectorAll('[data-revision]').forEach(button=>button.onclick=()=>select(button.dataset.revision));
    $s('.history-more').hidden=!more;
  }
  function paint(){
    preview?.destroy();observer?.disconnect();$s('.history-content').replaceChildren();
    const page=detail||current;if(!page)return;
    $s('.history-date').textContent=detail?date(detail.created):tText("notes:currentVersion");
    $s('.history-document h2').textContent=page.title;
    let blocks=pageBlocks(page),marks=new Map();
    if(detail&&compare.checked){
      const diff=compareBlocks(blocks,pageBlocks(current));blocks=diff.document;marks=diff.marks;
      const c=diff.counts;notice(tText("notes:fromSelectedVersionToCurrentAddedRemovedChanged", {value0:(c.added), value1:(c.removed), value2:(c.changed), value3:(page.title!==current.title?(" "+tText("notes:name2", {value0:(page.title), value1:(current.title)})+""):'')}));
    }else notice(detail?tText("notes:savedContentOnlyViewing"):tText("notes:currentContentOnlyViewing"));
    if(trashPage)blocks=JSON.parse(JSON.stringify(blocks).replace(/(\/api\/notes\/[A-Za-z0-9_-]+\/files\/[A-Za-z0-9_-]{16})(?![A-Za-z0-9_?-])/g,'$1?trash=true'));
    preview=mountBlockEditor($s('.history-content'),{...page,id:pageId,blocks},{readOnly:true,getPage:id=>ctx.state.notes.find(p=>p.id===id)});
    const decorate=()=>{for(const [id,kind] of marks){const node=$s('.history-content').querySelector('.bn-block-outer[data-id="'+id+'"]');if(node&&!node.dataset.historyChange){node.dataset.historyChange=kind;node.dataset.historyLabel={added:tText("notes:added"),removed:tText("notes:removed"),before:tText("notes:was"),after:tText("notes:became")}[kind];}}};
    observer=new MutationObserver(decorate);observer.observe($s('.history-content'),{childList:true,subtree:true});decorate();controls();
  }
  async function select(id){
    if(restoring)return;const token=++sequence;selected=id;detail=null;confirm.hidden=true;controls();timeline();notice(tText("notes:loadingVersion"));
    try{const value=id?await ctx.api(base+'/history/'+encodeURIComponent(id)+(trashPage?'?trash=true':'')):null;if(disposed||token!==sequence)return;detail=value;paint();timeline();screen.classList.remove('show-history-list');$s('.history-list-toggle').setAttribute('aria-expanded','false');$s('.history-scroll').scrollTop=0;}
    catch(error){if(!disposed&&token===sequence){notice(error.message);controls();}}
  }
  async function load(append=false){
    if(loading)return;loading=true;controls();notice(tText("notes:loadingHistory"));
    try{
      const entries=await ctx.api(base+'/history?limit=100'+(trashPage?'&trash=true':'')+(append?'&before='+encodeURIComponent(revisions.at(-1).id):''));
      if(disposed)return;revisions=append?[...revisions,...entries]:entries;more=entries.length===100;
      if(!append){current=trashPage||await ctx.api(base);if(disposed)return;await select(null);}else notice(tText("notes:versionsLoaded", {value0:(revisions.length)}));
    }catch(error){if(!disposed){notice(error.message);const retry=document.createElement('button');retry.className='text-link';retry.textContent=tText("notes:retry");retry.onclick=()=>{retry.remove();load(false);};status.append(' ',retry);}}
    finally{loading=false;if(!disposed){timeline();controls();}}
  }
  $s('select').onchange=timeline;compare.onchange=()=>{confirm.hidden=true;paint();};
  $s('.history-more').onclick=()=>load(true);
  restore.onclick=()=>{confirm.hidden=false;$s('[data-confirm]').focus();};$s('[data-cancel]').onclick=()=>{confirm.hidden=true;restore.focus();};
  $s('[data-confirm]').onclick=async()=>{
    if(trashPage||!detail||restoring)return;restoring=true;controls();$s('[data-confirm]').disabled=true;
    try{await ctx.api(base+'/history/'+encodeURIComponent(detail.id)+'/restore?version='+current.version,'POST');if(!disposed){ctx.toast(tText("notes:versionRestoredPreviousContentSavedInHistory"));await onClose();}}
    catch(error){if(!disposed){confirm.hidden=true;notice(error.status===409?tText("notes:thePageHasBeenChangedAfterOpeningThe"):error.message);if(error.status===409){detail=null;const refresh=document.createElement('button');refresh.className='text-link';refresh.textContent=tText("notes:refreshHistory");refresh.onclick=()=>load(false);status.append(' ',refresh);}}}
    finally{restoring=false;if(!disposed){$s('[data-confirm]').disabled=false;controls();}}
  };
  $s('.history-close').onclick=()=>{if(!restoring)onClose();};
  $s('.history-list-toggle').onclick=()=>{const shown=screen.classList.toggle('show-history-list');$s('.history-list-toggle').setAttribute('aria-expanded',String(shown));};
  const pop=()=>{if(location.pathname!==path&&!disposed)onClose(false);};window.addEventListener('popstate',pop);
  const key=event=>{if(event.key==='Escape'&&!restoring){if(!confirm.hidden){confirm.hidden=true;restore.focus();}else onClose();}};screen.addEventListener('keydown',key);
  load();$s('.history-close').focus();
  return {destroy(){disposed=true;sequence++;preview?.destroy();observer?.disconnect();window.removeEventListener('popstate',pop);screen.remove();}};
}
