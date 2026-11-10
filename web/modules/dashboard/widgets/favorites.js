import {tText,tHtml} from "../../../core/i18n.js";
import {pageIcon} from '../../../core/page-assets.js';
import {esc} from '../../../core/ui.js';
export function mountFavorites(host,ctx){
  if(!ctx.moduleEnabled('notes')){host.textContent=tText("dashboard:enableTheNotesModule");return;}
  host.innerHTML=`<p class="muted">${tHtml("dashboard:loadingFavorites")}</p>`;
  ctx.api('/favorites').then(items=>{if(!host.isConnected)return;const columns=ctx.instance.config.columns||'auto';host.innerHTML='<div class="favorite-links" data-columns="'+columns+'">'+items.map(n=>`<button class="recent-note" data-favorite-id="${esc(n.id)}"><span>${pageIcon(n)}</span><strong>${esc(n.title)}</strong><span>↗</span></button>`).join('')+'</div>';if(!items.length)host.innerHTML=`<p class="muted">${tHtml("dashboard:clickTheOnTheNotePageToAdd")}</p>`;host.querySelectorAll('[data-favorite-id]').forEach(b=>b.onclick=()=>ctx.openPage(b.dataset.favoriteId));}).catch(e=>{if(host.isConnected)host.textContent=e.message;});
}
