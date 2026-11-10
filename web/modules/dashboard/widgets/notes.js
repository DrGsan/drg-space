import {tHtml} from "../../../core/i18n.js";
import {pageIcon} from '../../../core/page-assets.js';
import { esc } from "../../../core/ui.js";
import {widgetSize} from '../sizes.js';
export function mountRecentNotes(host,ctx){
  const [columns,rows]=widgetSize(ctx.instance.size).split('x').map(Number),limit=Math.min(48,6*columns*rows);
  host.innerHTML=`<div class="panel-head"><button class="text-link" data-all-notes>${tHtml("dashboard:allNotes")}</button></div><div class="recent-notes-list">${ctx.state.notes.slice().sort((a,b)=>b.updated-a.updated).slice(0,limit).map(n=>`<button class="recent-note" data-note="${esc(n.id)}"><span>${pageIcon(n)}</span><strong>${esc(n.title)}</strong><span>↗</span></button>`).join('')||`<p class="muted">${tHtml("dashboard:createTheFirstPage")}</p>`}</div>`;
  host.querySelector('[data-all-notes]').onclick=()=>ctx.navigate('notes');
  host.querySelectorAll('[data-note]').forEach(b=>b.onclick=()=>ctx.openPage(b.dataset.note));
}
