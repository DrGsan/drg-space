import {tHtml, tText} from '../../core/i18n.js';
import {$, esc} from '../../core/ui.js';
import {pageCover, pageIcon} from '../../core/page-assets.js';
import {sidebarIcon} from '../../core/sidebar-icons.js';
import {mobileApp} from '../../core/mobile.js';
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

export function mountNoteAssistant(host, ctx, {target, initialSource, layout, refreshTarget, beforeSave = async () => true}, onSaved) {
  const standalone = layout === 'workspace';
  const chat = document.createElement('section');
  chat.className = `notes-ai notes-ai-${layout}`;
  chat.setAttribute('aria-label', tText('notes:assistantChat'));
  chat.innerHTML = `<div class="notes-ai-intro"><div class="notes-ai-emblem">${sidebarIcon('sparkles')}</div><h2>${tHtml(standalone ? 'notes:assistantWelcome' : 'notes:assistantPanelWelcome')}</h2>${standalone ? `<p>${tHtml('notes:assistantWelcomeDescription')}</p>` : ''}</div>
    <div class="notes-ai-log" data-ai-log role="log" aria-live="polite" hidden></div>
    <div class="notes-ai-compose"><div data-ai-actions class="notes-ai-actions" hidden><button type="button" class="secondary compact" data-ai-reject>${tHtml('notes:assistantReject')}</button><button type="button" class="primary compact" data-ai-apply>${tHtml('notes:assistantSave')}</button></div>
    <form data-ai-input><textarea name="prompt" aria-label="${tHtml('notes:assistantPrompt')}" placeholder="${tHtml('notes:assistantPlaceholder')}" maxlength="4000" rows="2" required></textarea>
    <div class="notes-ai-toolbar"><details data-ai-details><summary data-ai-context-label></summary><div class="notes-ai-context-popover"><label>${tHtml('notes:assistantFindSource')}<input type="search" placeholder="${tHtml('notes:searchPages')}" maxlength="200" data-ai-search></label><label>${tHtml(standalone ? 'notes:assistantSelectNote' : 'notes:assistantSource')}<select data-ai-source></select></label><div data-ai-context></div></div></details><button type="submit" class="primary compact" aria-label="${tHtml('notes:assistantSend')}" title="${tHtml('notes:assistantSend')}"><span data-ai-send-icon>${sidebarIcon('arrow-up')}</span><span data-ai-spinner hidden aria-hidden="true" class="notes-ai-spinner"></span></button></div></form>
    <p class="muted notes-ai-disclosure">${tHtml('notes:assistantDisclosure')}</p><p data-ai-status class="notes-ai-screen-reader" role="status"></p><p data-ai-error role="alert"></p></div>`;
  host.append(chat);
  const form = $('[data-ai-input]', chat);
  const log = $('[data-ai-log]', chat);
  const detail = $('[data-ai-details]', chat);
  const error = $('[data-ai-error]', chat);
  const submit = $('button', form);
  const source = $('[data-ai-source]', chat);
  const search = $('[data-ai-search]', chat);
  const prompt = form.elements.prompt;
  const actions = $('[data-ai-actions]', chat);
  const apply = $('[data-ai-apply]', actions);
  const reject = $('[data-ai-reject]', actions);
  const history = [];
  let items = [], sourcePage = initialSource, searchGeneration = 0, contextGeneration = 0, timer;
  let destroyed = false;
  const selectedPage = () => standalone ? target ?? sourcePage : sourcePage;
  let latest, card, busy = false, applying = false, contextReady = false, loadedSources = false;
  const call = (path, method = 'GET', data) => ctx.api(apiRoot + path, method, data, true, {}, {timeoutMs: 65000});
  const discard = draft => call('/draft/' + draft.id, 'DELETE').catch(() => {});
  const input = () => ({target: reference(target), source: reference(sourcePage), include_target_text: !!target});
  const scrollLog = () => { log.scrollTop = log.scrollHeight; };
  function controls() {
    submit.disabled = busy || !contextReady;
    $('[data-ai-send-icon]', submit).hidden = busy && !applying;
    $('[data-ai-spinner]', submit).hidden = !busy || applying;
    $('[data-ai-status]', chat).textContent = busy && !applying ? tText('notes:assistantGenerating') : '';
    log.hidden = !log.firstElementChild;
    chat.classList.toggle('notes-ai-has-messages', !log.hidden);
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
    controls();
    if (remember) {
      history.push({role, text: [...text].slice(0, 1600).join('')});
      if (history.length > 6) history.shift();
    }
    scrollLog();
    return node;
  }
  function setSources(next) {
    const selected = selectedPage();
    items = next.filter(page => page.id !== selected?.id);
    if (selected) items.unshift(selected);
    source.innerHTML = `<option value="">${tHtml('notes:assistantNoSource')}</option>`;
    for (const page of items) source.add(new Option(page.title + (page.read_only ? ' · Notion' : ''), page.id));
    source.value = selected?.id || '';
  }
  async function refreshContext() {
    const sequence = ++contextGeneration;
    contextReady = false;
    controls();
    const refs = input();
    const page = standalone ? selectedPage() : target ?? sourcePage;
    $('[data-ai-context-label]', chat).innerHTML = `${sidebarIcon(page ? 'notebook-text' : 'plus')}<span>${page ? esc(page.title) : tHtml(standalone ? 'notes:assistantSelectNote' : 'notes:assistantContext')}</span>`;
    try {
      const context = refs.target || refs.source ? await call('/context', 'POST', refs) : {};
      if (!chat.isConnected || sequence !== contextGeneration) return;
      $('[data-ai-context]', chat).innerHTML = excerpt('notes:assistantTarget', context.target) + excerpt('notes:assistantSource', context.source);
      contextReady = true;
    } catch (failure) {
      if (chat.isConnected && sequence === contextGeneration) error.textContent = failure.message;
    } finally { if (sequence === contextGeneration) controls(); }
  }
  async function loadSources(query) {
    const sequence = ++searchGeneration;
    try {
      const {items: next} = await call('/pages?q=' + encodeURIComponent(query));
      if (chat.isConnected && sequence === searchGeneration) setSources(standalone ? next : next.filter(page => page.id !== target?.id));
    } catch (failure) { if (chat.isConnected && sequence === searchGeneration) error.textContent = failure.message; }
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
    const selection = items.find(page => page.id === source.value);
    if (standalone) { target = selection?.read_only ? null : selection; sourcePage = selection?.read_only ? selection : null; }
    else sourcePage = selection;
    if (standalone) {
      history.length = 0;
      log.replaceChildren();
    }
    detail.open = false;
    error.textContent = '';
    clearDraft();
    refreshContext();
  };
  chat.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !detail.open) return;
    event.stopPropagation();
    detail.open = false;
    $('summary', detail).focus();
  });
  chat.addEventListener('pointerdown', event => {
    if (detail.open && !detail.contains(event.target)) detail.open = false;
  });
  setSources([]);
  refreshContext();
  if (!standalone && !matchMedia('(max-width: 900px)').matches) prompt.focus();
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
      if (target && !latest && refreshTarget) {
        const current = await refreshTarget(target);
        if (!chat.isConnected) return;
        if (current) {
          target = current;
          await refreshContext();
          if (!contextReady) throw new Error(tText('notes:assistantPageChanged'));
        }
      }
      const result = await call('/propose', 'POST', {...input(), prompt: instruction, previous: latest ?? null, history: messages});
      if (!chat.isConnected) { if (result.draft) discard(result.draft); return; }
      if (result.draft) showReview(result);
      else if (result.message.trim()) addMessage('assistant', result.message);
    } catch (failure) {
      if (!chat.isConnected) return;
      error.textContent = failure.message;
      node.remove();
      history.splice(0, history.length, ...messages);
      prompt.value = instruction;
      if (failure.status === 409) clearDraft();
    } finally { busy = false; controls(); if (chat.isConnected) prompt.focus(); }
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
      latest = null;
      card?.remove();
      card = null;
      if (!chat.isConnected) return;
      const page = await onSaved(saved.id);
      if (!chat.isConnected) return;
      const confirmation = addMessage('assistant', tText('notes:assistantSaved'), false);
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'text-link notes-ai-note-link';
      link.textContent = tText('notes:assistantOpenNote');
      link.onclick = () => ctx.openPage(saved.id);
      confirmation.append(link);
      if (target?.id === saved.id && page) { target = page; await refreshContext(); }
    } catch (failure) {
      if (chat.isConnected) {
        error.textContent = failure.message;
        if (failure.status === 409) clearDraft();
      } else ctx.toast(failure.message);
    } finally { applying = busy = false; controls(); }
  }
  apply.onclick = save;
  return () => {
    if (destroyed) return;
    destroyed = true;
    clearTimeout(timer);
    if (latest && !applying) discard(latest);
    chat.remove();
  };
}


export function mountAssistantWorkspace(host, ctx) {
  host.innerHTML = `<section class="assistant-workspace"><header class="assistant-workspace-header"><div>${mobileApp() ? `<button class="icon-btn" data-ai-back aria-label="${tHtml('app:backToSettings')}">${sidebarIcon('chevron-left')}</button>` : ''}<h1>${tHtml('notes:assistantSection')}</h1></div><button type="button" class="secondary compact" data-ai-new>${sidebarIcon('plus')}${tHtml('notes:assistantNewChat')}</button></header><div class="assistant-workspace-body" data-ai-workspace></div></section>`;
  const container = $('[data-ai-workspace]', host);
  const newChat = $('[data-ai-new]', host);
  $('[data-ai-back]', host)?.addEventListener('click', () => ctx.navigate('settings'));
  let dispose;
  const open = () => {
    dispose?.();
    dispose = mountNoteAssistant(container, ctx, {layout: 'workspace'}, async id => {
      const page = await ctx.api('/notes/' + id);
      if (!container.isConnected) return null;
      const index = ctx.state.notes.findIndex(note => note.id === id);
      if (index < 0) ctx.state.notes.push(page); else ctx.state.notes[index] = page;
      return page;
    });
  };
  newChat.onclick = open;
  open();
  return () => dispose?.();
}
