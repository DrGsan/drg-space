import {tText,tHtml} from '../../core/i18n.js';
import {$,esc} from '../../core/ui.js';
import './notion-sync.css';
import {pagePicker} from './notion-tree.js';

const base='/account/notion-sync';
const label=status=>tText('portability:notionState_'+status);

async function notionConflict(ctx,id,changed){
  const el=ctx.modal(`<div class="notion-sync"><h2>${tHtml('portability:notionRecovery')}</h2><p>${tHtml('portability:notionRecoveryIntro')}</p><div data-conflict-content></div><p class="error" role="alert" data-conflict-error></p></div>`);
  const content=$('[data-conflict-content]',el),error=$('[data-conflict-error]',el);
  content.innerHTML=`<p role="status">${tHtml('portability:notionReviewLoading')}</p>`;
  let review,busy=false,keepCopy=true,done=false;
  function controls(){
    for(const control of content.querySelectorAll('button,select,input'))control.disabled=busy||done;
    const push=content.querySelector('[data-send-local]');if(push)push.disabled=busy||done||!review?.can_push;
    const pull=content.querySelector('[data-accept-source]');if(pull)pull.disabled=busy||done||!review;
  }
  const action=fn=>async()=>{
    if(busy)return;busy=true;error.textContent='';controls();
    try{await fn();}catch(e){error.textContent=e.message;}finally{busy=false;controls();}
  };
  async function resolve(direction){
    const result=await ctx.api(base+'/roots/'+id+'/recovery','POST',{note_id:review.note_id,review_hash:review.hash,version:review.version,note_version:review.note_version,local_hash:review.local_hash,direction,keep_copy:keepCopy});
    done=true;await changed();
    content.innerHTML=`<p role="status">${tHtml('portability:notionRecoveryDone')}</p>${result.independent_copy?`<a class="secondary" href="/page/${esc(result.independent_copy)}">${tHtml('portability:notionIndependentCopy')}</a>`:''}`;
  }
  async function load(nid=review?.note_id){
    // A failed refresh must not leave an old, apparently actionable review.
    review=null;controls();
    const next=await ctx.api(base+'/roots/'+id+'/recovery'+(nid?'?note_id='+encodeURIComponent(nid):''));if(!el.isConnected)return;review=next;
    content.innerHTML=`<label>${tHtml('portability:notionRecoveryPage')}<select data-recovery-page>${review.pages.map(page=>`<option value="${esc(page.note_id)}" ${page.note_id===review.note_id?'selected':''}>${esc(page.title)}</option>`).join('')}</select></label><div class="notion-conflict-columns"><section><h3>${tHtml('portability:notionLocalVersion')}</h3><pre>${esc(review.local)}</pre></section><section><h3>${tHtml('portability:notionRemoteVersion')}</h3><pre>${esc(review.remote)}</pre></section></div><div class="notion-recovery-copy"><label><input type="checkbox" data-keep-copy ${keepCopy?'checked':''}>${tHtml('portability:notionRecoveryKeepCopy')}</label><p class="hint">${tHtml('portability:notionRecoveryCopyHint')}</p></div><div class="notion-recovery-directions"><button class="primary" data-accept-source>${tHtml('portability:notionRecoveryPull')}</button><button class="secondary" data-send-local>${tHtml('portability:notionRecoveryPush')}</button></div>${review.push_reason?`<p class="hint" role="status">${tHtml('portability:notionRecoveryPushBlocked')} ${esc(tText('server:'+review.push_reason))}</p>`:''}<button class="text-link" data-reload-review>${tHtml('portability:notionReloadReview')}</button>`;
    $('[data-keep-copy]',el).onchange=event=>{keepCopy=event.target.checked;};
    $('[data-recovery-page]',el).onchange=action(()=>load($('[data-recovery-page]',el).value));
    $('[data-reload-review]',el).onclick=action(()=>load());
    $('[data-accept-source]',el).onclick=action(()=>resolve('pull'));
    $('[data-send-local]',el).onclick=action(()=>resolve('push'));controls();
  }
  try{await action(()=>load())();}catch(e){error.textContent=e.message;}
  if(!review){content.innerHTML=`<button class="secondary" data-review-retry>${tHtml('portability:notionReloadReview')}</button>`;$('[data-review-retry]',el).onclick=action(()=>load());}
}

export async function notionSyncDialog(ctx,{openPage}={}){
  if(ctx.state.me?.role!=='owner')return;
  const el=ctx.modal(`<div class="notion-sync"><p class="eyebrow">Notion → DrG</p><h2>${tHtml('portability:notionSync')}</h2><p class="muted">${tHtml('portability:notionSyncIntro')}</p><div class="notion-sync-safe"><strong>${tHtml('portability:notionReadOnly')}</strong><span>${tHtml('portability:notionReadOnlyHint')}</span></div><div data-sync-setup></div><div data-sync-links></div><p class="error" role="alert" data-sync-error></p></div>`);
  let timer,closed=false,busy=false,state,setupKey,generation=0;
  const cards=new Map();
  const text=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
  const error=$('[data-sync-error]',el),links=$('[data-sync-links]',el),setup=$('[data-sync-setup]',el);
  const act=fn=>async event=>{
    if(busy)return;busy=true;const button=event?.submitter||event?.currentTarget;button?.setAttribute('disabled','');error.textContent='';
    try{await fn();await refresh();}catch(e){error.textContent=e.message;}finally{busy=false;button?.removeAttribute('disabled');}
  };
  function renderSetup(){
    if(!state.configured){setup.innerHTML=`<p class="hint">${tHtml('portability:notionServerSetup')}</p>`;return;}
    const connections=state.connections.filter(c=>c.kind==='read'&&c.status==='active');
    setup.innerHTML=`<div class="notion-sync-heading"><h3>${tHtml('portability:notionConnection')}</h3><button class="secondary compact" data-sync-connect>${tHtml('portability:notionConnect')}</button></div>${connections.length?`<form data-sync-preview><label>${tHtml('portability:notionWorkspace')}<select name="connection">${connections.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></label><label>${tHtml('portability:notionSourcePage')}<input name="source" autocomplete="off" maxlength="2000" placeholder="https://www.notion.so/…" required></label><p class="hint">${tHtml('portability:notionSubtreeHint')}</p><button type="submit" class="primary">${tHtml('portability:notionPreview')}</button></form>`:`<p class="hint">${tHtml('portability:notionConsentHint')}</p>`}`;
    const form=$('[data-sync-preview]',setup);
    if(form){
      const source=$('[name=source]',form),label=source.closest('label'),fallback=document.createElement('details');fallback.className='notion-source-fallback';
      fallback.innerHTML=`<summary>${tHtml('portability:notionPasteLink')}</summary>`;label.before(fallback);fallback.append(label);source.required=false;
      const picker=document.createElement('div');picker.className='notion-page-picker';
      picker.innerHTML=`<label>${tHtml('portability:notionPage')}</label><button type="button" class="secondary" aria-haspopup="tree" aria-expanded="false" data-source-picker>${tHtml('portability:notionChoosePage')}</button><div class="notion-source-options" data-source-options hidden><input type="search" data-source-search aria-label="${tHtml('portability:notionPageSearch')}" placeholder="${tHtml('portability:notionPageSearch')}"><div class="notion-source-list" role="tree" aria-label="${tHtml('portability:notionPage')}" data-source-list></div><p class="hint" role="status" data-source-status></p><button type="button" class="text-link" data-source-more hidden>${tHtml('portability:notionPagesMore')}</button></div>`;
      fallback.before(picker);$('[type=submit]',form).disabled=true;pagePicker(form,ctx,el);
    }
    $('[data-sync-connect]',el).onclick=act(async()=>{
      const result=await ctx.api(base+'/oauth','POST',{kind:'read',platform:'web'}),url=new URL(result.url);
      if(url.origin!=='https://api.notion.com'||url.pathname!=='/v1/oauth/authorize')throw new Error(tText('portability:notionConnectionInvalid'));
      location.assign(url.href);
    });
    $('[data-sync-preview]',el)?.addEventListener('submit',event=>{
      event.preventDefault();const form=new FormData(event.currentTarget);
      act(()=>ctx.api(base+'/preview','POST',{connection_id:form.get('connection'),source:form.get('source').trim()}))(event);
    });
  }
  function renderLinks(shown){
    if(!links.firstChild)links.innerHTML=`<h3 data-links-title>${tHtml('portability:notionLinkedPages')}</h3><p class="hint" data-links-empty>${tHtml('portability:notionNoLinks')}</p>`;
    $('[data-links-title]',links).hidden=!shown.length;$('[data-links-empty]',links).hidden=Boolean(shown.length);
    const present=new Set(shown.map(root=>root.id));
    for(const [id,card] of cards)if(!present.has(id)){card.element.remove();cards.delete(id);}
    for(const root of shown){
      let card=cards.get(root.id);
      if(!card){
        const element=document.createElement('article');element.className='notion-sync-link';element.dataset.syncRoot=root.id;
        element.innerHTML=`<div class="notion-sync-heading"><strong data-link-title></strong><span class="notion-sync-status" role="status" data-link-status></span></div><p class="hint" data-link-summary hidden></p><div data-link-issues></div><p class="hint" data-sync-progress hidden></p><p class="error" data-link-error hidden></p><p class="hint" data-link-visibility hidden>${tHtml('portability:notionImportVisibility')}</p><div data-link-controls></div>`;
        card={element,groups:new Map(),controlsKey:null};cards.set(root.id,card);links.append(element);
      }
      const element=card.element,summary=root.summary,details=$('[data-link-issues]',element);
      text($('[data-link-title]',element),root.title);text($('[data-link-status]',element),label(root.status));
      const summaryNode=$('[data-link-summary]',element);summaryNode.hidden=!summary;
      if(summary)text(summaryNode,tText('portability:notionSummary',{pages:summary.pages,files:summary.files,databases:summary.databases}));
      const groups=summary?.issue_groups||((summary?.issue_count)?[{kind:'content',count:summary.issue_count,issues:summary.issues}]:[]);
      const activeGroups=new Set(groups.map(group=>group.kind));
      for(const [kind,saved] of card.groups)if(!activeGroups.has(kind)){saved.element.remove();card.groups.delete(kind);}
      for(const group of groups){
        let saved=card.groups.get(group.kind);
        if(!saved){const element=document.createElement('details');element.dataset.issueGroup=group.kind;element.innerHTML='<summary></summary><ul></ul>';saved={element,items:new Map()};card.groups.set(group.kind,saved);details.append(element);}
        text($('summary',saved.element),tText('portability:notionIssues_'+group.kind,{count:group.count}));
        const list=$('ul',saved.element),seen=new Map(),presentIssues=new Set();
        for(const [index,issue] of group.issues.entries()){
          const base=JSON.stringify([issue.notion_id,issue.id,issue.page,issue.reason]),occurrence=seen.get(base)||0,key=base+':'+occurrence;
          seen.set(base,occurrence+1);presentIssues.add(key);
          let item=saved.items.get(key);
          if(!item){item=document.createElement('li');saved.items.set(key,item);}
          text(item,issue.page+' — '+tText('server:'+issue.reason));
          if(list.children[index]!==item)list.insertBefore(item,list.children[index]||null);
        }
        for(const [key,item] of saved.items)if(!presentIssues.has(key)){item.remove();saved.items.delete(key);}
      }
      const progress=$('[data-sync-progress]',element);progress.hidden=!root.progress;
      if(root.progress)text(progress,root.status==='scanning'?tText('portability:notionCheckedPages',{count:root.progress.checked_pages}):tText('portability:notionCopiedPages',{count:root.progress.copied_pages,total:root.progress.total_pages}));
      const failures=root.asset_failures||[];
      let assetDetails=$('[data-asset-failures]',element);
      if(!assetDetails){assetDetails=document.createElement('details');assetDetails.dataset.assetFailures='';assetDetails.innerHTML='<summary></summary><ul></ul>';element.insertBefore(assetDetails,$('[data-link-error]',element));}
      assetDetails.hidden=!failures.length;
      text($('summary',assetDetails),tText('portability:notionAssetFailures',{count:failures.length}));
      const assetKey=JSON.stringify(failures);
      if(card.assetKey!==assetKey){
        $('ul',assetDetails).innerHTML=failures.map(item=>`<li><strong>${esc(item.page)}</strong><div class="hint">${esc(item.file)} · ${esc(item.host)}</div><div>${esc(tText('server:'+item.reason))}</div><p class="hint">${tHtml(item.blocked?'portability:notionAssetBlocked':'portability:notionAssetRetrying')}</p></li>`).join('');card.assetKey=assetKey;
      }
      const failure=$('[data-link-error]',element);failure.hidden=!root.error||(root.error==='notionAssetUnavailable'&&failures.length>0);if(root.error)text(failure,tText('server:'+root.error,root.error_params||{}));
      $('[data-link-visibility]',element).hidden=root.status!=='importing'&&(!root.note_id||root.copy_available);
      const controlsKey=JSON.stringify([root.status,root.mode,root.note_id,root.copy_available,root.has_conflict,state.write_configured,root.has_asset_errors]);
      if(card.controlsKey!==controlsKey){
        $('[data-link-controls]',element).innerHTML=`${root.note_id&&root.status==='active'?`<div class="notion-sync-experiment"><label><input type="checkbox" data-sync-experiment="${esc(root.id)}" ${root.mode==='duplex'?'checked':''} ${state.write_configured||root.mode==='duplex'?'':'disabled'}>${tHtml('portability:notionExperimental')}</label><p class="hint">${tHtml(state.write_configured?'portability:notionExperimentalHelp':'portability:notionWriteSetup')}</p></div>`:''}<div class="notion-sync-actions">${root.copy_available&&(root.status==='paused'||root.status==='conflict'||root.has_conflict)?`<button class="primary" data-sync-conflict="${esc(root.id)}">${tHtml('portability:notionRecovery')}</button>`:''}${root.status==='ready'?`<button class="primary" data-sync-confirm="${esc(root.id)}">${tHtml('portability:notionCreateCopy')}</button>`:''}${root.copy_available?`<button class="secondary compact" data-sync-open="${esc(root.note_id)}">${tHtml('portability:notionOpenCopy')}</button>`:''}${root.status==='active'?`<button class="secondary compact" data-sync-action="pause" data-root="${esc(root.id)}">${tHtml('portability:notionPause')}</button>`:''}${root.status==='paused'?`<button class="secondary compact" data-sync-action="resume" data-root="${esc(root.id)}">${tHtml('portability:notionResume')}</button>`:''}<button class="text-link" data-sync-action="${root.note_id?'disconnect':'cancel'}" data-root="${esc(root.id)}">${tHtml(root.note_id?'portability:notionDisconnect':'common:cancel')}</button></div>`;
        card.controlsKey=controlsKey;
        if(root.has_asset_errors&&['active','importing'].includes(root.status)){
          const retry=document.createElement('button');retry.className='secondary compact';retry.dataset.syncAction='retry_assets';retry.dataset.root=root.id;text(retry,tText('portability:notionRetryAssets'));
          $('.notion-sync-actions',element).prepend(retry);
        }
      }
    }
  }
  async function refresh(){
    clearTimeout(timer);if(closed||!el.isConnected||document.hidden)return;
    const turn=++generation;const next=await ctx.api(base);if(closed||turn!==generation)return;state=next;
    const key=JSON.stringify([state.configured,state.connections]);
    if(setupKey!==key){setupKey=key;renderSetup();}
    const shown=state.roots.filter(root=>root.status!=='detached');
    renderLinks(shown);
    for(const button of el.querySelectorAll('[data-sync-confirm]'))button.onclick=act(()=>{const root=state.roots.find(r=>r.id===button.dataset.syncConfirm);return ctx.api(base+'/roots/'+root.id+'/confirm','POST',{preview_hash:root.preview_hash});});
    for(const button of el.querySelectorAll('[data-sync-action]'))button.onclick=act(()=>{
      const root=state.roots.find(r=>r.id===button.dataset.root),action=button.dataset.syncAction;
      if(action==='disconnect'&&!confirm(tText('portability:notionDisconnectConfirm')))return;
      return ctx.api(base+'/roots/'+root.id+'/action','POST',{action,version:root.version});
    });
    for(const button of el.querySelectorAll('[data-sync-conflict]'))button.onclick=()=>notionConflict(ctx,button.dataset.syncConflict,refresh);
    for(const input of el.querySelectorAll('[data-sync-experiment]'))input.onchange=act(async()=>{
      const root=state.roots.find(r=>r.id===input.dataset.syncExperiment),enabled=input.checked;
      if(enabled&&!confirm(tText('portability:notionExperimentalConfirm'))){input.checked=false;return;}
      await ctx.api(base+'/roots/'+root.id+'/experimental','POST',{enabled,version:root.version});
    });
    for(const button of el.querySelectorAll('[data-sync-open]'))button.onclick=()=>{
      $('.modal-close',el).click();if(openPage)openPage(button.dataset.syncOpen);else location.assign('/page/'+button.dataset.syncOpen);
    };
    if(shown.some(root=>['scanning','importing'].includes(root.status)))timer=setTimeout(()=>refresh().catch(e=>{error.textContent=e.message;}),2000);
  }
  const visible=()=>{if(!document.hidden)refresh().catch(e=>{error.textContent=e.message;});else clearTimeout(timer);};
  document.addEventListener('visibilitychange',visible);
  el.addEventListener('close',()=>{closed=true;clearTimeout(timer);document.removeEventListener('visibilitychange',visible);},{once:true});
  try{await refresh();}catch(e){error.textContent=e.message;}
  return el;
}
