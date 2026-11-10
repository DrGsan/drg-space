import {tHtml} from "../../../core/i18n.js";
import { esc } from "../../../core/ui.js";
export function mountBookmarks(host,ctx) {
  const links=ctx.instance.config.links || [];
  host.innerHTML=links.length?`<div class="bookmark-grid">${links.map((link,index)=>`<a class="bookmark-tile color-${esc(link.color)}" href="${esc(link.url)}" target="_blank" rel="noopener noreferrer"><span class="bookmark-icon">${!link.icon||['auto','🔗'].includes(link.icon)?`🔗<img src="/api/widgets/${encodeURIComponent(ctx.instance.id)}/site-icon/${index}" alt="" loading="lazy">`:esc(link.icon)}</span><strong>${esc(link.title)}</strong><span class="small muted">${esc(new URL(link.url).hostname)}</span></a>`).join('')}</div>`:`<p class="muted">${tHtml("dashboard:addYourFavoriteWebsitesInTheWidgetSettings")}</p>`;
  host.querySelectorAll('.bookmark-icon img').forEach(img=>img.onerror=()=>img.remove());
}
