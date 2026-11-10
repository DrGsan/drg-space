import {tText,tHtml,locale} from "../../core/i18n.js";
import { esc, date } from '../../core/ui.js';

const fonts = new Set(['default', 'serif', 'mono']);
const defaults = () => ({ font: 'default', small_text: false });
function viewKey(userId, pageId) {
  if (![userId, pageId].every(id => typeof id === 'string' && /^[\w-]{1,200}$/.test(id))) return null;
  return `drg-note-view:${userId}:${pageId}`;
}
export function readPageView(userId, pageId) {
  try {
    const key = viewKey(userId, pageId), raw = key && localStorage.getItem(key);
    if (!raw || raw.length > 200) return defaults();
    const value = JSON.parse(raw);
    return { font: fonts.has(value?.font) ? value.font : 'default', small_text: value?.small_text === true };
  } catch { return defaults(); }
}
export function savePageView(userId, pageId, value) {
  const key = viewKey(userId, pageId);
  if (!key) return false;
  try {
    localStorage.setItem(key, JSON.stringify({ font: fonts.has(value.font) ? value.font : 'default', small_text: value.small_text === true }));
    return true;
  } catch { return false; }
}
export function transferPageView(userId, oldId, newId) {
  const oldKey = viewKey(userId, oldId);
  if (!oldKey) return;
  try {
    if (localStorage.getItem(oldKey) && savePageView(userId, newId, readPageView(userId, oldId))) localStorage.removeItem(oldKey);
  } catch { /* View preferences never prevent saving a document. */ }
}
export function applyPageView(host, value) {
  host.dataset.pageFont = fonts.has(value.font) ? value.font : 'default';
  host.classList.toggle('page-small-text', value.small_text === true);
}
const paths = {
  star: '<path d="m12 3 2.8 5.7 6.3.9-4.6 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3L3 9.6l6.2-.9Z"/>',
  link: '<path d="m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2-1 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0)"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4"/>',
  duplicate: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h1"/>',
  move: '<path d="M4 20v-6a4 4 0 0 1 4-4h12m-5-5 5 5-5 5"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  width: '<path d="M3 12h18m-4-4 4 4-4 4M7 8l-4 4 4 4"/>',
  small: '<path d="M3 17 8 5l5 12M5 12h6m5-4v12m-3-3 3 3 3-3"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 4v3"/>',
  unlock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 7-2m-3 10v3"/>',
  export: '<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  import: '<path d="M12 16V3m-5 5 5-5 5 5M4 17v4h16v-4"/>',
  history: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  child: '<path d="M12 5v14M5 12h14"/>',
  access: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-3a6 6 0 0 1 12 0v3m2-15a3 3 0 0 1 0 6m2 4a5 5 0 0 1 2 4"/>',
  settings:'<path d="M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6m-8 0v6"/>',
  dots: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  back: '<path d="m10 5-7 7 7 7m-7-7h18"/>',
};
export function pageMenuIcon(name, filled = false) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="${filled ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.dots}</svg>`;
}

/** A contained popover: search stays visible while its actions scroll. */
export function showPageMenu({ anchor, page, view, mode, editable, actions, words, onAction, onView, onMode, onClose, temporary=false }) {
  const menu = document.createElement('div');
  menu.className = 'note-page-menu';
  menu.id = 'note-page-menu';
  menu.setAttribute('role', 'dialog');
  menu.setAttribute('aria-label', tText("notes:pageActions"));
  const groups = [...new Set(actions.map(action => action.group))];
  const row = action => `<button type="button" ${action.disabled?'disabled':''} ${action.id ? `id="${esc(action.id)}"` : ''} ${typeof action.checked === 'boolean' ? `role="switch" aria-checked="${action.checked}"` : ''} data-page-action="${esc(action.name)}" data-search="${esc(action.label + ' ' + (action.aliases || ''))}" class="note-menu-row ${action.danger ? 'danger' : ''}">${pageMenuIcon(action.icon)}<span>${esc(action.label)}</span>${typeof action.checked === 'boolean' ? '<span class="note-menu-switch" aria-hidden="true"></span>' : ''}</button>`;
  menu.innerHTML = `<div class="note-menu-search"><input type="search" maxlength="100" placeholder="${tHtml("notes:searchingForActions")}" aria-label="${tHtml("notes:searchPageActions")}"></div><div class="note-menu-scroll"><div class="note-menu-section note-font-options" data-search="${tHtml('notes:fontSearchTerms')}"><div class="note-menu-fonts" role="group" aria-label="${tHtml("notes:pageFont")}">${[['default', tText("notes:normal")], ['serif', tText("notes:serif")], ['mono', tText("notes:mono")]].map(([font, label]) => `<button type="button" data-page-font="${font}" aria-pressed="${view.font === font}"><span class="note-font-sample note-font-${font}" aria-hidden="true">Ag</span><span>${label}</span></button>`).join('')}</div></div>${groups.map(group => `<div class="note-menu-section" data-menu-group>${actions.filter(action => action.group === group).map(row).join('')}</div>`).join('')}<div class="note-menu-section" data-menu-group><button type="button" class="note-menu-row" role="switch" aria-checked="${view.small_text}" data-page-small data-search="${tHtml('notes:smallTextSearchTerms')}">${pageMenuIcon('small')}<span>${tHtml("notes:smallText")}</span><span class="note-menu-switch" aria-hidden="true"></span></button></div>${editable && page.kind !== 'database' ? `<div class="note-menu-section note-editor-options" data-search="${tHtml('notes:editorSearchTerms')}"><span class="note-menu-label">${tHtml("notes:editor")}</span><div class="note-editor-modes" role="group" aria-label="${tHtml("notes:editorMode")}"><button type="button" id="mode-blocks" data-editor-mode="blocks" aria-pressed="${mode === 'blocks'}">${tHtml("notes:blocks")}</button><button type="button" id="mode-markdown" data-editor-mode="markdown" aria-pressed="${mode === 'markdown'}">Markdown</button></div></div>` : ''}<p class="note-menu-empty muted" hidden>${tHtml("notes:nothingFound")}</p><div class="note-menu-footer"><span>${tHtml("notes:words", {value0:(esc(words))})}</span>${page.updated ? `<span>${tHtml("notes:modified", {value0:(esc(date(page.updated)))})}</span>` : ''}<span>${temporary?tText("notes:fontAndTextSizeBeforeSectionLock"):tText("notes:fontAndTextSizeOnThisDevice")}</span></div></div>`;
  document.body.append(menu);
  anchor.setAttribute('aria-expanded', 'true');
  anchor.setAttribute('aria-controls', menu.id);
  const position = () => {
    const rect = anchor.getBoundingClientRect(), margin = 8;
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    menu.style.maxWidth = `${Math.max(120, width - margin * 2)}px`;
    const below = top + height - rect.bottom - margin, above = rect.top - top - margin;
    const placeBelow = below >= Math.min(menu.scrollHeight, 320) || below >= above;
    menu.style.maxHeight = `${Math.max(100, Math.min(height - margin * 2, placeBelow ? below - 4 : above - 4))}px`;
    menu.style.left = `${Math.max(left + margin, Math.min(rect.right - menu.offsetWidth, left + width - menu.offsetWidth - margin))}px`;
    menu.style.top = `${placeBelow ? rect.bottom + 4 : Math.max(top + margin, rect.top - menu.offsetHeight - 4)}px`;
  };
  const outside = event => { if (!menu.contains(event.target) && !anchor.contains(event.target)) close(false); };
  const keys = event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    if (event.key === 'Tab') { close(); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    if (event.target.matches('input') && event.key !== 'ArrowDown') return;
    event.preventDefault();
    const buttons = [...menu.querySelectorAll('button')].filter(button => !button.hidden && !button.closest('[hidden]'));
    const current = buttons.indexOf(document.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  };
  let closed = false;
  const close = (focus = true) => {
    if (closed) return;
    closed = true;
    menu.remove();
    anchor.setAttribute('aria-expanded', 'false');
    anchor.removeAttribute('aria-controls');
    document.removeEventListener('pointerdown', outside);
    menu.removeEventListener('keydown', keys);
    window.removeEventListener('resize', position);
    window.removeEventListener('scroll', position, true);
    window.visualViewport?.removeEventListener('resize', position);
    window.visualViewport?.removeEventListener('scroll', position);
    if (focus && anchor.isConnected) anchor.focus();
    onClose?.();
  };
  const search = menu.querySelector('input');
  search.oninput = () => {
    const query = search.value.trim().toLocaleLowerCase(locale());
    menu.querySelectorAll('[data-search]').forEach(item => { item.hidden = !item.dataset.search.toLocaleLowerCase(locale()).includes(query); });
    menu.querySelectorAll('[data-menu-group]').forEach(group => { group.hidden = ![...group.children].some(child => !child.hidden); });
    menu.querySelector('.note-menu-footer').hidden = !!query;
    menu.querySelector('.note-menu-empty').hidden = [...menu.querySelectorAll('[data-search]')].some(item => !item.hidden);
    position();
  };
  menu.querySelectorAll('[data-page-action]').forEach(button => button.onclick = () => { close(); onAction(button.dataset.pageAction); });
  menu.querySelectorAll('[data-page-font]').forEach(button => button.onclick = () => {
    view.font = button.dataset.pageFont;
    onView(view);
    menu.querySelectorAll('[data-page-font]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  });
  const small = menu.querySelector('[data-page-small]');
  small.onclick = () => { view.small_text = !view.small_text; small.setAttribute('aria-checked', String(view.small_text)); onView(view); };
  menu.querySelectorAll('[data-editor-mode]').forEach(button => button.onclick = () => { close(); onMode(button.dataset.editorMode); });
  document.addEventListener('pointerdown', outside);
  menu.addEventListener('keydown', keys);
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, true);
  window.visualViewport?.addEventListener('resize', position);
  window.visualViewport?.addEventListener('scroll', position);
  position();
  search.focus();
  return close;
}

export function pageActions(page,editable,{secret=false}={}){
  const actions=[
      {name:'copy-link',label:tText("notes:copyLink2"),icon:'link',group:'copy'},
      ...(page.kind!=='database'?[{name:'copy-content',label:tText("notes:copyContent"),icon:'copy',group:'copy'}]:[]),
      ...(editable?[
        {name:'duplicate',label:tText("notes:createCopy"),icon:'duplicate',group:'copy'},
        {name:'move',label:tText("notes:move"),icon:'move',group:'copy'},
        {name:'trash',label:tText("notes:toTrash"),icon:'trash',group:'copy',danger:true},
        {name:'child',label:tText("notes:addSubpage"),icon:'child',group:'page'},
        {name:'width',label:tText("notes:fullWidth"),icon:'width',group:'page',checked:!!page.full_width},
      ]:[]),
      ...(page.notion_sync?.can_reorder?[
        {name:'move-up',label:tText('portability:notionMoveUp'),icon:'move',group:'copy'},
        {name:'move-down',label:tText('portability:notionMoveDown'),icon:'move',group:'copy'},
      ]:[]),
      {name:'permissions',id:'page-permissions',label:tText("notes:accessToPage"),icon:'access',group:'access'},
      ...(page.manage?[{name:'lock',label:page.locked?tText("notes:unlockPage"):tText("notes:lockPage"),icon:page.locked?'unlock':'lock',group:'access',checked:!!page.locked}]:[]),
      ...(page.kind!=='database'?[
        ...(editable?[{name:'import',label:tText("notes:importMarkdownNotionZip"),icon:'import',group:'transfer'}]:[]),
        {name:'export',label:tText("notes:exportMarkdown"),icon:'export',group:'transfer'},
        ...(editable?[{name:'history',label:tText("notes:versionHistory"),icon:'history',group:'history'}]:[]),
      ]:[]),
    ];
  if(!secret)return actions;
  const forbidden=new Set(['copy-link','permissions','lock','history','export']);
  return actions.filter(action=>!forbidden.has(action.name)&&!(action.name==='child'&&page.kind==='database')).map(action=>action.name==='trash'?{...action,label:tText("notes:deletePermanently")}:action).concat([{name:'move-out',label:tText("notes:moveOut"),icon:'move',group:'outside'}]);
}

export function pageWordCount(blocks,body=''){
  const text=value=>typeof value==='string'?value:Array.isArray(value)?value.map(text).join(' '):value&&typeof value==='object'?('text' in value?String(value.text):Object.values(value).map(text).join(' ')):'';
  const plain=items=>(items||[]).map(block=>text(block.content)+' '+plain(block.children)).join(' ');
  return ((blocks?plain(blocks):body).match(/[\p{L}\p{N}]+(?:[’'-][\p{L}\p{N}]+)*/gu)||[]).length;
}
