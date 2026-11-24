import {tHtml, tText} from '../../core/i18n.js';
import {$, esc, md} from '../../core/ui.js';
import {pageCover, pageIcon} from '../../core/page-assets.js';
import {sidebarIcon} from '../../core/sidebar-icons.js';
import './assistant.css';

const apiRoot = '/notes-assistant';
const labels = {
  title: 'notes:noteTitle', icon: 'common:pageIcon', cover: 'notes:pageCover',
  replace: 'notes:assistantReplaceText',
  rewrite: 'notes:assistantRewrite',
};
const reference = page => page ? {id: page.id, version: page.version} : null;

function cover(page) {
  if (!page.cover) return tHtml('notes:withoutCover');
  return `<div class="notes-ai-cover cover-${esc(page.cover)}">${pageCover(page)}</div>`;
}

function reviewField(field, draft, before, coverUrl, formatted) {
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
    proposed = formatted && (field === 'append' || field === 'rewrite')
      ? `<div data-ai-writing class="notes-ai-formatted">${md(formatted)}</div>`
      : `<p${field === 'title' ? '' : ' data-ai-writing'}>${esc(field === 'replace' ? draft.changes.replace_after : next)}</p>`;
  }
  const label = field === 'append' ? draft.target ? 'notes:assistantAppendToEnd' : 'notes:assistantBody' : labels[field];
  return `<section class="notes-ai-change"><h4>${tHtml(label)}</h4>${previous ? `<div class="notes-ai-before"><span>${tHtml('notes:assistantBefore')}</span>${previous}</div>` : ''}<div class="notes-ai-after">${previous ? `<span>${tHtml('notes:assistantAfter')}</span>` : ''}${proposed}</div></section>`;
}

function textRuns(roots) {
  const runs = [];
  let length = 0;
  for (const root of roots) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let textNode; (textNode = walker.nextNode());) {
      const characters = Array.from(textNode.data);
      if (!characters.length) continue;
      runs.push({textNode, characters, start: length});
      length += characters.length;
    }
  }
  return {runs, length};
}

function animateText(node, {runs, length}, paint) {
  const duration = Math.min(2200, 480 + length * 9);
  return new Promise(resolve => {
    const start = performance.now();
    let active = 0;
    let animation;
    const finish = () => {
      cancelAnimationFrame(animation);
      document.removeEventListener('visibilitychange', finish);
      resolve();
    };
    function frame(now) {
      const count = Math.min(length, Math.ceil(length * (now - start) / duration));
      for (; active < runs.length; active++) {
        const run = runs[active];
        if (count <= run.start) break;
        paint(run, count - run.start);
        if (count < run.start + run.characters.length) break;
      }
      if (count === length || !node.isConnected) finish();
      else animation = requestAnimationFrame(frame);
    }
    document.addEventListener('visibilitychange', finish);
    animation = requestAnimationFrame(frame);
  });
}

const canAnimate = () => !matchMedia('(prefers-reduced-motion: reduce)').matches && !document.hidden;

async function revealText(node) {
  if (!node || !canAnimate()) return;
  const text = textRuns([node]);
  if (text.length < 2) return;
  for (const {textNode} of text.runs) textNode.data = '';
  node.setAttribute('aria-hidden', 'true');
  await animateText(node, text, ({textNode, characters}, count) => { textNode.data = characters.slice(0, count).join(''); });
  for (const {textNode, characters} of text.runs) textNode.data = characters.join('');
  node.removeAttribute('aria-hidden');
}

// Highlight a diminishing range instead of editing the text nodes owned by ProseMirror.
export async function revealNoteBlocks(editor, ids) {
  if (!editor || !ids?.length || !editor.getClientRects().length || !canAnimate()) return;
  const blocks = ids.map(id => editor.querySelector('.bn-block-outer[data-id="' + CSS.escape(id) + '"]'))
    .filter(Boolean);
  if (!blocks.length) return;
  const roots = blocks.map(block => block.querySelector('.bn-inline-content, pre code')).filter(Boolean);
  const text = textRuns(roots);
  if (text.length < 2) return;
  if (!globalThis.Highlight || !CSS.highlights) {
    blocks[0].scrollIntoView({block: 'nearest'});
    await Promise.all(blocks.map(block => block.animate([{opacity: .2}, {opacity: 1}], 650).finished.catch(() => {})));
    return;
  }
  const highlight = new Highlight();
  const key = 'note-ai-unwritten';
  for (const run of text.runs) {
    const range = new Range();
    range.selectNodeContents(run.textNode);
    run.range = range;
    highlight.add(range);
  }
  CSS.highlights.set(key, highlight);
  blocks[0].scrollIntoView({block: 'nearest'});
  try {
    await animateText(editor, text, ({textNode, characters, range}, count) => {
      if (count >= characters.length) highlight.delete(range);
      else range.setStart(textNode, characters.slice(0, count).join('').length);
    });
  } finally {
    if (CSS.highlights.get(key) === highlight) CSS.highlights.delete(key);
  }
}

function excerpt(label, note) {
  return note ? `<section class="notes-ai-context"><h3>${tHtml(label)} · ${esc(note.title)}</h3>${note.excerpt ? `<p>${esc(note.excerpt)}</p>` : ''}</section>` : '';
}

export function mountNoteAssistant(host, ctx, {target, initialSource, refreshTarget, beforeSave = async () => true}, onSaved) {
  const chat = document.createElement('section');
  chat.className = 'notes-ai';
  chat.setAttribute('aria-label', tText('notes:assistantChat'));
  chat.innerHTML = `<div class="notes-ai-intro"><div class="notes-ai-emblem">${sidebarIcon('sparkles')}</div><h2>${tHtml('notes:assistantPanelWelcome')}</h2></div>
    <div class="notes-ai-log" data-ai-log role="log" aria-live="polite" hidden></div>
    <div class="notes-ai-compose">
    <form data-ai-input><textarea name="prompt" aria-label="${tHtml('notes:assistantPrompt')}" placeholder="${tHtml('notes:assistantPlaceholder')}" maxlength="4000" rows="2" required></textarea>
    <div class="notes-ai-toolbar"><details data-ai-details><summary data-ai-context-label></summary><div class="notes-ai-context-popover"><label>${tHtml('notes:assistantFindSource')}<input type="search" placeholder="${tHtml('notes:searchPages')}" maxlength="200" data-ai-search></label><label>${tHtml('notes:assistantSource')}<select data-ai-source></select></label><div data-ai-context></div></div></details><button type="submit" class="primary compact" aria-label="${tHtml('notes:assistantSend')}" title="${tHtml('notes:assistantSend')}"><span data-ai-send-icon>${sidebarIcon('arrow-up')}</span><span data-ai-spinner hidden aria-hidden="true" class="notes-ai-spinner"></span></button></div></form>
    <p class="muted notes-ai-disclosure">${tHtml('notes:assistantDisclosure')}</p><p data-ai-status class="notes-ai-status" role="status" hidden><span class="notes-ai-status-dot" aria-hidden="true"></span><span data-ai-phase></span></p><p data-ai-error role="alert"></p></div>`;
  host.append(chat);
  const form = $('[data-ai-input]', chat);
  const log = $('[data-ai-log]', chat);
  const detail = $('[data-ai-details]', chat);
  const error = $('[data-ai-error]', chat);
  const status = $('[data-ai-status]', chat);
  const phaseLabel = $('[data-ai-phase]', status);
  const submit = $('button', form);
  const source = $('[data-ai-source]', chat);
  const search = $('[data-ai-search]', chat);
  const prompt = form.elements.prompt;
  const history = [];
  let items = [], sourcePage = initialSource, searchGeneration = 0, contextGeneration = 0, timer;
  let destroyed = false;
  let busy = false, phase = 'assistantGenerating', contextReady = false, loadedSources = false;
  const call = (path, method = 'GET', data) => ctx.api(apiRoot + path, method, data, true, {}, {timeoutMs: 65000});
  const discard = draft => call('/draft/' + draft.id, 'DELETE').catch(() => {});
  const input = () => ({target: reference(target), source: reference(sourcePage), include_target_text: !!target});
  const scrollLog = () => { log.scrollTop = log.scrollHeight; };
  function controls() {
    submit.disabled = busy || !contextReady;
    $('[data-ai-send-icon]', submit).hidden = busy;
    $('[data-ai-spinner]', submit).hidden = !busy;
    status.hidden = !busy;
    const caption = busy ? tText('notes:' + phase) : '';
    if (phaseLabel.textContent !== caption) phaseLabel.textContent = caption;
    chat.querySelectorAll('[data-ai-cover-choice]').forEach(button => { button.disabled = busy; });
    log.hidden = !log.firstElementChild;
    chat.classList.toggle('notes-ai-has-messages', !log.hidden);
    prompt.readOnly = busy;
    source.disabled = busy;
    search.disabled = busy;
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
    const selected = sourcePage;
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
    const page = target ?? sourcePage;
    $('[data-ai-context-label]', chat).innerHTML = `${sidebarIcon(page ? 'notebook-text' : 'plus')}<span>${page ? esc(page.title) : tHtml('notes:assistantContext')}</span>`;
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
      if (chat.isConnected && sequence === searchGeneration) setSources(next.filter(page => page.id !== target?.id));
    } catch (failure) { if (chat.isConnected && sequence === searchGeneration) error.textContent = failure.message; }
  }
  detail.ontoggle = () => {
    if (!detail.open || loadedSources) return;
    loadedSources = true;
    loadSources('');
  };
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(() => loadSources(search.value.trim()), 200); };
  source.onchange = () => {
    sourcePage = items.find(page => page.id === source.value);
    detail.open = false;
    error.textContent = '';
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
  if (!matchMedia('(max-width: 900px)').matches) prompt.focus();
  prompt.onkeydown = event => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    if (!submit.disabled) form.requestSubmit();
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const instruction = prompt.value.trim();
    if (!instruction || busy || !contextReady) return;
    const messages = history.slice();
    const node = addMessage('user', instruction);
    prompt.value = '';
    error.textContent = '';
    busy = true;
    setPhase('assistantGenerating');
    try {
      if (target && refreshTarget) {
        const current = await refreshTarget(target);
        if (!chat.isConnected) return;
        if (current) {
          target = current;
          await refreshContext();
          if (!contextReady) throw new Error(tText('notes:assistantPageChanged'));
        }
      }
      const result = await call('/propose', 'POST', {...input(), prompt: instruction, history: messages});
      if (result.draft) await save(result);
      else if (!chat.isConnected) return;
      else if (result.cover_query) await showCovers(result.cover_query);
      else if (result.message.trim()) {
        setPhase('assistantTyping');
        await revealText(addMessage('assistant', result.message));
      }
    } catch (failure) {
      if (!chat.isConnected) return;
      error.textContent = failure.message;
      node.remove();
      history.splice(0, history.length, ...messages);
      prompt.value = instruction;
    } finally { busy = false; controls(); if (chat.isConnected) prompt.focus(); }
  };
  function showChange({draft, fields, before, cover_url: coverUrl, formatted}) {
    const card = document.createElement('section');
    card.className = 'notes-ai-preview';
    card.setAttribute('data-ai-change', '');
    card.innerHTML = `<h3>${tHtml(draft.target ? 'notes:assistantEditedNote' : 'notes:assistantCreatedNote')}</h3>${fields.map(field => reviewField(field, draft, before, coverUrl, formatted)).join('')}`;
    log.append(card);
    log.scrollTop = card.offsetTop - log.offsetTop;
    return revealText($('[data-ai-writing]', card));
  }
  function setPhase(next) {
    phase = next;
    controls();
  }
  async function showCovers(query) {
    setPhase('assistantFindingCovers');
    const {items: photos} = await ctx.api('/note-cover-search?provider=commons&q=' + encodeURIComponent(query));
    if (!chat.isConnected) return;
    if (!photos.length) {
      addMessage('assistant', tText('notes:noPhotosFoundTryADifferentRequest'));
      return;
    }
    const ref = reference(target);
    const gallery = document.createElement('section');
    gallery.className = 'notes-ai-preview';
    gallery.innerHTML = `<h3>${tHtml('notes:assistantCoverOptions')}</h3><div class="notes-ai-cover-options">${photos.slice(0, 3).map((photo, index) => `<button type="button" data-ai-cover-choice="${index}"><img src="${esc(photo.thumbnail)}" alt="" referrerpolicy="no-referrer"><span>${esc(photo.title.replace(/^File:/i, ''))}</span><small>${esc(photo.attribution.author)} · ${esc(photo.attribution.license)}</small></button>`).join('')}</div>`;
    gallery.querySelectorAll('[data-ai-cover-choice]').forEach(button => {
      button.onclick = async () => {
        if (busy) return;
        busy = true;
        error.textContent = '';
        setPhase('assistantApplying');
        try {
          if (!await beforeSave()) throw new Error(tText('notes:assistantPageChanged'));
          if (!chat.isConnected) return;
          await ctx.api('/notes/' + encodeURIComponent(ref.id) + '/select-cover', 'POST',
            {token: photos[Number(button.dataset.aiCoverChoice)].selection_token, assistant_version: ref.version});
          await savedMessage(ref.id, true, await refreshSaved(ref.id));
          gallery.remove();
        } catch (failure) {
          if (chat.isConnected) error.textContent = failure.message;
        } finally { busy = false; controls(); }
      };
    });
    log.append(gallery);
    scrollLog();
  }
  const refreshSaved = (id, changes) => onSaved(id, changes).catch(failure => { ctx.toast(failure.message); return null; });
  async function savedMessage(id, showHistory, page) {
    if (!chat.isConnected) return;
    const confirmation = addMessage('assistant', tText('notes:assistantSaved'), false);
    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'text-link notes-ai-note-link';
    link.textContent = tText('notes:assistantOpenNote');
    link.onclick = () => ctx.openPage(id);
    confirmation.append(link);
    if (showHistory) {
      const historyLink = document.createElement('a');
      historyLink.className = 'text-link notes-ai-note-link';
      historyLink.href = '/page/' + encodeURIComponent(id) + '/history';
      historyLink.textContent = tText('notes:pageHistory');
      confirmation.append(historyLink);
    }
    if (page && target?.id === id) { target = page; await refreshContext(); }
  }
  async function save(result) {
    const {draft} = result;
    setPhase('assistantApplying');
    let saved;
    try {
      if (!chat.isConnected) return;
      if (!await beforeSave()) throw new Error(tText('notes:assistantPageChanged'));
      if (!chat.isConnected) return;
      saved = await call('/apply', 'POST', {draft});
      if (!chat.isConnected) return;
      setPhase('assistantTyping');
      const reveal = showChange(result);
      let page;
      if (draft.target?.id === saved.id) {
        [page] = await Promise.all([refreshSaved(saved.id, saved), reveal]);
      } else {
        await reveal;
        page = await refreshSaved(saved.id);
      }
      await savedMessage(saved.id, Boolean(draft.target), page);
    } finally { if (!saved) discard(draft); }
  }
  return () => {
    if (destroyed) return;
    destroyed = true;
    clearTimeout(timer);
    chat.remove();
  };
}
