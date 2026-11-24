import {tHtml, tText} from '../../core/i18n.js';
import {$, esc} from '../../core/ui.js';
import {pageCover, pageIcon} from '../../core/page-assets.js';
import './assistant.css';

const apiRoot = '/notes-assistant';
const labels = {
  title: 'notes:noteTitle', icon: 'common:pageIcon', cover: 'notes:pageCover',
  append: 'notes:assistantAddText', replace: 'notes:assistantReplaceText',
  rewrite: 'notes:assistantRewrite',
};
const reference = page => page ? {id: page.id, version: page.version} : null;

function cover(page) {
  if (!page.cover) return tHtml('notes:withoutCover');
  return `<div class="notes-ai-cover cover-${esc(page.cover)}">${pageCover(page)}</div>`;
}

function reviewField(field, draft, before, coverUrl) {
  const next = draft.changes[field];
  let previous = '';
  let proposed;
  if (field === 'cover') {
    previous = draft.target ? cover(before) : '';
    proposed = cover({cover: next, cover_url: coverUrl});
  } else if (field === 'icon') {
    previous = draft.target ? pageIcon(before) : '';
    proposed = pageIcon({icon: next});
  } else {
    const old = field === 'replace' ? draft.changes.replace_before : before[field];
    previous = old == null ? '' : `<p>${esc(old)}</p>`;
    proposed = `<p>${esc(field === 'replace' ? draft.changes.replace_after : next)}</p>`;
  }
  const label = field === 'append' ? draft.target ? 'notes:assistantAppendToEnd' : 'notes:assistantBody' : labels[field];
  return `<section class="notes-ai-change"><h4>${tHtml(label)}</h4>${previous ? `<div class="notes-ai-before"><span>${tHtml('notes:assistantBefore')}</span>${previous}</div>` : ''}<div class="notes-ai-after">${previous ? `<span>${tHtml('notes:assistantAfter')}</span>` : ''}${proposed}</div></section>`;
}

function excerpt(label, note) {
  return note ? `<section class="notes-ai-context"><h3>${tHtml(label)} · ${esc(note.title)}</h3>${note.excerpt ? `<p>${esc(note.excerpt)}</p>` : ''}</section>` : '';
}

export function openNoteAssistant(ctx, {target, initialSource, beforeSave}, onSaved, onClose) {
  const overlay = ctx.modal(`<h2>${tHtml('notes:assistantChat')}</h2><div class="notes-ai"><details data-ai-details><summary data-ai-context-label>${tHtml('notes:assistantContext')}</summary><div data-ai-context></div><div class="notes-ai-source"><label>${tHtml('notes:assistantFindSource')}<input type="search" placeholder="${tHtml('notes:searchPages')}" maxlength="200" data-ai-search></label><label>${tHtml('notes:assistantSource')}<select data-ai-source></select></label></div></details><div class="notes-ai-log" data-ai-log role="log" aria-live="polite"></div><div data-ai-actions class="notes-ai-actions" hidden><button type="button" class="secondary" data-ai-reject>${tHtml('notes:assistantReject')}</button><button type="button" class="primary" data-ai-apply>${tHtml('notes:assistantSave')}</button></div><form data-ai-input><textarea name="prompt" aria-label="${tHtml('notes:assistantPrompt')}" placeholder="${tHtml('notes:assistantPlaceholder')}" maxlength="4000" rows="2" required></textarea><button type="submit" class="primary">${tHtml('notes:assistantSend')}</button></form><p class="muted notes-ai-disclosure">${tHtml('notes:assistantDisclosure')}</p><p data-ai-error role="alert"></p></div>`);
  overlay.classList.add('notes-assistant-overlay');
  const form = $('[data-ai-input]', overlay);
  const log = $('[data-ai-log]', overlay);
  const detail = $('[data-ai-details]', overlay);
  const error = $('[data-ai-error]', overlay);
  const submit = $('button', form);
  const source = $('[data-ai-source]', overlay);
  const search = $('[data-ai-search]', overlay);
  const prompt = form.elements.prompt;
  const actions = $('[data-ai-actions]', overlay);
  const apply = $('[data-ai-apply]', actions);
  const reject = $('[data-ai-reject]', actions);
  const history = [];
  let items = [], sourcePage = initialSource, searchGeneration = 0, contextGeneration = 0, timer;
  let latest, card, busy = false, applying = false, contextReady = false, loadedSources = false;
  const call = (path, method = 'GET', data) => ctx.api(apiRoot + path, method, data, true, {}, {timeoutMs: 65000});
  const discard = draft => call('/draft/' + draft.id, 'DELETE').catch(() => {});
  const input = () => ({target: reference(target), source: reference(sourcePage), include_target_text: !!target});
  const scrollLog = () => { log.scrollTop = log.scrollHeight; };
  function controls() {
    submit.disabled = busy || !contextReady;
    submit.textContent = tText(busy && !applying ? 'notes:assistantGenerating' : 'notes:assistantSend');
    prompt.readOnly = busy;
    source.disabled = busy;
    search.disabled = busy;
    actions.hidden = !latest;
    apply.disabled = reject.disabled = busy;
  }
  function addMessage(role, text, remember = true) {
    const node = document.createElement('p');
    node.className = 'notes-ai-message ' + (role === 'user' ? 'user' : 'assistant');
    node.textContent = text;
    log.append(node);
    if (remember) {
      history.push({role, text: [...text].slice(0, 1600).join('')});
      if (history.length > 6) history.shift();
    }
    scrollLog();
    return node;
  }
  function setSources(next) {
    items = next.filter(page => page.id !== sourcePage?.id);
    if (sourcePage) items.unshift(sourcePage);
    source.innerHTML = `<option value="">${tHtml('notes:assistantNoSource')}</option>`;
    for (const page of items) source.add(new Option(page.title + (page.read_only ? ' · Notion' : ''), page.id));
    source.value = sourcePage?.id || '';
  }
  async function refreshContext() {
    const sequence = ++contextGeneration;
    contextReady = false;
    controls();
    const refs = input();
    try {
      const context = refs.target || refs.source ? await call('/context', 'POST', refs) : {};
      if (!overlay.isConnected || sequence !== contextGeneration) return;
      $('[data-ai-context]', overlay).innerHTML = excerpt('notes:assistantTarget', context.target) + excerpt('notes:assistantSource', context.source);
      $('[data-ai-context-label]', overlay).textContent = sourcePage ? `${tText('notes:assistantSource')} · ${sourcePage.title}` : tText('notes:assistantContext');
      contextReady = true;
    } catch (failure) {
      if (overlay.isConnected && sequence === contextGeneration) error.textContent = failure.message;
    } finally { if (sequence === contextGeneration) controls(); }
  }
  async function loadSources(query) {
    const sequence = ++searchGeneration;
    try {
      const {items: next} = await call('/pages?q=' + encodeURIComponent(query));
      if (overlay.isConnected && sequence === searchGeneration) setSources(next.filter(page => page.id !== target?.id));
    } catch (failure) { if (overlay.isConnected && sequence === searchGeneration) error.textContent = failure.message; }
  }
  function clearDraft() {
    if (latest) discard(latest);
    latest = null;
    card?.remove();
    card = null;
    controls();
  }
  detail.ontoggle = () => {
    if (!detail.open || loadedSources) return;
    loadedSources = true;
    loadSources('');
  };
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(() => loadSources(search.value.trim()), 200); };
  source.onchange = () => {
    sourcePage = items.find(page => page.id === source.value);
    error.textContent = '';
    clearDraft();
    refreshContext();
  };
  setSources([]);
  refreshContext();
  prompt.focus();
  overlay.addEventListener('beforeclose', event => { if (applying) event.preventDefault(); });
  overlay.addEventListener('close', () => { clearTimeout(timer); if (latest) discard(latest); onClose(); });
  prompt.onkeydown = event => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    if (!submit.disabled) form.requestSubmit();
  };
  reject.onclick = () => {
    clearDraft();
    addMessage('assistant', tText('notes:assistantRejected'), false);
    prompt.focus();
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const instruction = prompt.value.trim();
    if (!instruction || busy || !contextReady) return;
    const command = instruction.toLocaleLowerCase().replace(/[.!?]+$/, '').trim();
    if (latest && tText('notes:assistantAcceptCommands').split('|').includes(command)) { prompt.value = ''; await save(); return; }
    if (latest && tText('notes:assistantRejectCommands').split('|').includes(command)) { prompt.value = ''; reject.click(); return; }
    const messages = history.slice();
    const node = addMessage('user', instruction);
    prompt.value = '';
    error.textContent = '';
    busy = true;
    controls();
    try {
      const result = await call('/propose', 'POST', {...input(), prompt: instruction, previous: latest ?? null, history: messages});
      if (!overlay.isConnected) { if (result.draft) discard(result.draft); return; }
      if (result.draft) showReview(result);
      else if (result.message.trim()) addMessage('assistant', result.message);
    } catch (failure) {
      if (!overlay.isConnected) return;
      error.textContent = failure.message;
      node.remove();
      history.splice(0, history.length, ...messages);
      prompt.value = instruction;
      if (failure.status === 409) clearDraft();
    } finally { busy = false; controls(); if (overlay.isConnected) prompt.focus(); }
  };
  function showReview({draft, fields, before, cover_url: coverUrl, retains_attachments: retains}) {
    latest = draft;
    card?.remove();
    card = document.createElement('section');
    card.className = 'notes-ai-preview';
    card.setAttribute('data-ai-preview', '');
    card.innerHTML = `<h3>${tHtml(draft.target ? 'notes:assistantPreview' : 'notes:assistantNewPreview')}</h3>${retains ? `<p class="muted">${tHtml('notes:assistantKeepsAttachments')}</p>` : ''}${fields.map(field => reviewField(field, draft, before, coverUrl)).join('')}`;
    log.append(card);
    controls();
    // Show the start of a long rewrite instead of scrolling past it.
    log.scrollTop = card.offsetTop - log.offsetTop;
  }
  async function save() {
    if (!latest || busy) return;
    applying = busy = true;
    error.textContent = '';
    controls();
    try {
      if (!await beforeSave()) return;
      const saved = await call('/apply', 'POST', {draft: latest});
      applying = false;
      latest = null;
      $('.modal-close', overlay).click();
      ctx.toast(tText('notes:assistantSaved'));
      await onSaved(saved.id);
    } catch (failure) {
      if (overlay.isConnected) {
        error.textContent = failure.message;
        if (failure.status === 409) clearDraft();
      } else ctx.toast(failure.message);
    } finally { applying = busy = false; controls(); }
  }
  apply.onclick = save;
  return () => $('.modal-close', overlay).click();
}
