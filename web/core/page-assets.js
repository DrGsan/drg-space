import {tText,tHtml} from "./i18n.js";
import {esc} from './ui.js';
const asset=value=>/^image:([a-f0-9]{32})$/.exec(value||'')?.[1];
export function pageIconUrl(page){const id=asset(page.icon);return id?(page.icon_url||'/api/notes/'+(page.id||page.note_id)+'/assets/'+id):null;}
export function pageIcon(page){const url=pageIconUrl(page);return url?`<img class=page-custom-icon src="${esc(url)}" alt="${tHtml("common:pageIcon")}">`:esc(page.icon||'📄');}
export function pageCover(page){return page.cover_url?`<img src="${esc(page.cover_url)}" alt="" style="object-position:center ${Math.min(100,Math.max(0,page.cover_position??50))}%">`:'';}
export function coverAttribution(page){const a=page.cover_attribution;if(!a)return '';const safe=/^https:\/\//.test(a.source_url||'');return `<p class=cover-attribution>${safe?`<a href="${esc(a.source_url)}" target=_blank rel="noopener noreferrer">${esc(a.author||tText("common:imageSource"))}</a>`:esc(a.author||'')} · ${esc(a.license||'')}</p>`;}
