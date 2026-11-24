import {tHtml, tText} from '../../core/i18n.js';
import {$, esc} from '../../core/ui.js';
import {pageCover, pageIcon} from '../../core/page-assets.js';
import './assistant.css';

const apiRoot = '/notes-assistant';
const labels = {
  title: 'notes:noteTitle', icon: 'common:pageIcon', cover: 'notes:pageCover',
  append: 'notes:assistantAddText', replace: 'notes:assistantReplaceText',
};
const reference = ({id, version}) => ({id, version});

function cover(page) {
  if (!page.cover) return tHtml('notes:withoutCover');
  return `<div class="notes-ai-cover cover-${esc(page.cover)}">${pageCover(page)}</div>`;
}

function reviewField(field, draft, before, coverUrl) {
  const next = draft.changes[field];
  const fixed = !draft.target && ['title', 'append'].includes(field);
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
  const operation = draft.target && field === 'append' ? 'notes:assistantAppendToEnd' : labels[field];
  return `<section class="notes-ai-change"><label><input type="checkbox" value="${field}" checked ${fixed ? 'disabled' : ''}>${tHtml(operation)}</label>${previous ? `<div class="notes-ai-before"><span>${tHtml('notes:assistantBefore')}</span>${previous}</div>` : ''}<div class="notes-ai-after"><span>${tHtml('notes:assistantAfter')}</span>${proposed}</div></section>`;
}

function excerpt(label, note) {
  return note ? `<section class="notes-ai-context"><h3>${tHtml(label)} · ${esc(note.title)}</h3>${note.excerpt ? `<p>${esc(note.excerpt)}</p>` : ''}</section>` : '';
}

export function openNoteAssistant(ctx, {target, initialSource}, onSaved, onClose) {
  const overlay = ctx.modal(`<h2>${tHtml(target ? 'notes:assistantEdit' : 'notes:assistantCreate')}</h2><div class="notes-ai"><form data-ai-input><label>${tHtml('notes:assistantPrompt')}<textarea name="prompt" maxlength="4000" rows="4" required></textarea></label>${target ? `<label class="notes-ai-option"><input type="checkbox" name="include_target_text">${tHtml('notes:assistantIncludeText')}</label>` : ''}<label>${tHtml('notes:assistantFindSource')}<input type="search" placeholder="${tHtml('notes:searchPages')}" maxlength="200" data-ai-search></label><label>${tHtml('notes:assistantSource')}<select data-ai-source><option value="">${tHtml('notes:assistantNoSource')}</option></select></label><button type="submit" class="primary">${tHtml('notes:assistantReviewInput')}</button></form><div data-ai-stage></div><p data-ai-error role="alert"></p></div>`);
  const form = $('[data-ai-input]', overlay);
  const stage = $('[data-ai-stage]', overlay);
  const error = $('[data-ai-error]', overlay);
  const submit = form.querySelector('button');
  const source = $('[data-ai-source]', form);
  const search = $('[data-ai-search]', form);
  const prompt = form.elements.prompt;
  let items = [], generation = 0, timer, latest, applying = false;
  function setSources(next, selected) {
    items = next;
    source.innerHTML = `<option value="">${tHtml('notes:assistantNoSource')}</option>`;
    for (const item of items) source.add(new Option(item.title + (item.read_only ? ' · Notion' : ''), item.id));
    source.value = selected || '';
  }
  if (initialSource) setSources([initialSource], initialSource.id);
  const call = (path, method = 'GET', data) => ctx.api(apiRoot + path, method, data, true, {}, {timeoutMs: 45000});
  const discard = draft => call('/draft/' + draft.id, 'DELETE').catch(() => {});
  overlay.addEventListener('beforeclose', event => { if (applying) event.preventDefault(); });
  overlay.addEventListener('close', () => { clearTimeout(timer); if (latest) discard(latest); onClose(); });
  async function sources(query) {
    const current = ++generation;
    try {
      const result = await call('/pages?q=' + encodeURIComponent(query));
      if (!overlay.isConnected || current !== generation) return;
      const selected = items.find(page => page.id === source.value);
      const next = result.items.filter(page => page.id !== target?.id);
      if (selected && !next.some(page => page.id === selected.id)) next.unshift(selected);
      setSources(next, selected?.id);
    } catch (failure) { if (overlay.isConnected && current === generation) error.textContent = failure.message; }
  }
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(() => sources(search.value.trim()), 200); };
  sources('');
  prompt.focus();
  function back() {
    if (latest) discard(latest);
    latest = null;
    stage.replaceChildren();
    form.hidden = false;
    error.textContent = '';
    prompt.focus();
  }
  form.onsubmit = async event => {
    event.preventDefault();
    error.textContent = '';
    if (!prompt.value.trim()) { prompt.focus(); return; }
    const chosen = items.find(page => page.id === source.value);
    const input = {target: target ? reference(target) : null, source: chosen ? reference(chosen) : null, include_target_text: !!form.elements.include_target_text?.checked};
    const instruction = prompt.value.trim();
    submit.disabled = true;
    try {
      const context = await call('/context', 'POST', input);
      if (!overlay.isConnected) return;
      form.hidden = true;
      stage.innerHTML = `<h3>${tHtml('notes:assistantReviewInput')}</h3><p class="muted">${tHtml('notes:assistantExternal')}</p><section class="notes-ai-context"><h3>${tHtml('notes:assistantPrompt')}</h3><p>${esc(instruction)}</p></section>${excerpt('notes:assistantTarget', context.target)}${excerpt('notes:assistantSource', context.source)}<div class="notes-ai-actions"><button class="secondary" data-ai-back>${tHtml('notes:back')}</button><button class="primary" data-ai-generate>${tHtml('notes:assistantGenerate')}</button></div>`;
      $('[data-ai-back]', stage).onclick = back;
      const generate = $('[data-ai-generate]', stage);
      generate.focus();
      generate.onclick = async () => {
        generate.disabled = true;
        $('[data-ai-back]', stage).disabled = true;
        error.textContent = '';
        generate.textContent = tText('notes:assistantGenerating');
        try {
          const preview = await call('/propose', 'POST', {...input, prompt: instruction});
          if (!overlay.isConnected) { discard(preview.draft); return; }
          showReview(preview);
        } catch (failure) {
          if (!overlay.isConnected) return;
          error.textContent = failure.message;
          generate.disabled = false;
          generate.textContent = tText('notes:assistantGenerate');
          $('[data-ai-back]', stage).disabled = false;
        }
      };
    } catch (failure) { if (overlay.isConnected) error.textContent = failure.message; }
    finally { submit.disabled = false; }
  };
  function showReview({draft, fields, before, cover_url: coverUrl}) {
    latest = draft;
    stage.innerHTML = `<h3 tabindex="-1" data-ai-preview>${tHtml('notes:assistantPreview')}</h3>${fields.map(field => reviewField(field, draft, before, coverUrl)).join('')}<div class="notes-ai-actions"><button class="secondary" data-ai-back>${tHtml('notes:back')}</button><button class="primary" data-ai-apply>${tHtml(draft.target ? 'notes:assistantApply' : 'notes:assistantSave')}</button></div>`;
    $('[data-ai-preview]', stage).focus();
    const apply = $('[data-ai-apply]', stage);
    const goBack = $('[data-ai-back]', stage);
    const choices = [...stage.querySelectorAll('input[type="checkbox"]')];
    goBack.onclick = back;
    for (const choice of choices) choice.onchange = () => { apply.disabled = !choices.some(item => item.checked); };
    apply.onclick = async () => {
      applying = true;
      apply.disabled = true;
      goBack.disabled = true;
      error.textContent = '';
      try {
        const saved = await call('/apply', 'POST', {draft, fields: choices.filter(item => item.checked).map(item => item.value)});
        applying = false;
        latest = null;
        $('.modal-close', overlay).click();
        ctx.toast(tText('notes:assistantSaved'));
        await onSaved(saved.id);
      } catch (failure) {
        if (overlay.isConnected) error.textContent = failure.message;
        else ctx.toast(failure.message);
      } finally {
        applying = false;
        apply.disabled = false;
        goBack.disabled = false;
      }
    };
  }
  return () => $('.modal-close', overlay).click();
}
