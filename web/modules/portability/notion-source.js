import {tText,tHtml} from '../../core/i18n.js';
import {$,esc} from '../../core/ui.js';
import './notion-sync.css';
import {statusBadges} from '../notes/status-badges.js';
const base='/account/notion-sync';
const label=status=>tText('portability:notionState_'+status);

export function mountNotionSource(host,page,ctx,{openPage,refresh,beforeCopy}={}){
  const sync=page.notion_sync;if(!sync)return;
  const previous=host.querySelector('.notion-source-banner'),signature=JSON.stringify([sync,page.manage,ctx.state.me?.role]);
  if(previous?.dataset.signature===signature)return;
  const opened=Boolean(previous?.open);previous?.remove();
  const stopped=['paused','conflict','unavailable'].includes(sync.status);
  const warning=sync.cover_warning&&sync.icon_warning?'notionDecorationsUnavailable':sync.cover_warning||sync.icon_warning;
  const banner=document.createElement('details');banner.className='notion-source-banner note-status-chip';banner.dataset.signature=signature;banner.open=opened;
  banner.innerHTML=`<div><strong>${esc(stopped?label(sync.status):tText(sync.mode==='duplex'&&!sync.read_only?'portability:notionExperimental':'portability:notionReadOnly'))}</strong><p>${esc(tText(stopped?'portability:notionSyncStopped':sync.transfer_pending?'portability:notionImportVisibility':'portability:notionSourceHint'))} ${stopped?'':`<span>${esc(label(sync.status))}</span>`}</p>${sync.error?`<p role="status">${esc(tText('server:'+sync.error))}</p>`:''}${warning?`<p class="${sync.cover_warning?'notion-cover-warning ':''}${sync.icon_warning?'notion-icon-warning':''}" role="status">⚠ ${esc(tText('server:'+warning))}</p>`:''}</div><div class="notion-sync-actions"><a class="text-link" href="${esc(sync.source_url)}" target="_blank" rel="noopener noreferrer">${tHtml('portability:notionOpenSource')}</a>${page.manage&&ctx.state.me?.role==='owner'?`${!sync.transfer_pending?`<button class="text-link" data-sync-copy>${tHtml('portability:notionIndependentCopy')}</button>`:''}<button class="text-link" data-sync-manage>${tHtml('portability:notionManage')}</button>`:''}</div>`;
  const content=document.createElement('div');content.className='note-status-popover';content.innerHTML=banner.innerHTML;
  banner.replaceChildren();const summary=document.createElement('summary');summary.textContent=(warning||stopped?'⚠ ':'')+'Notion '+(stopped?'· '+label(sync.status):sync.mode==='duplex'?'⇄':'↓');summary.dataset.badgeIcon=stopped||warning?'⚠':sync.mode==='duplex'?'⇄':'↓';summary.title=tText(sync.mode==='duplex'?'portability:notionExperimental':'portability:notionReadOnly');summary.setAttribute('aria-label',summary.title);banner.append(summary,content);
  statusBadges(host).append(banner);
  $('[data-sync-manage]',banner)?.addEventListener('click',()=>import('./notion-sync.js').then(module=>module.notionSyncDialog(ctx,{openPage})));
  $('[data-sync-copy]',banner)?.addEventListener('click',async event=>{
    const button=event.currentTarget;button.disabled=true;
    try{if(beforeCopy&&!await beforeCopy())return;const result=await ctx.api(base+'/copy','POST',{note_id:page.id});await refresh?.();await openPage?.(result.note_id);}catch(e){ctx.toast(e.message);}finally{button.disabled=false;}
  });
}
