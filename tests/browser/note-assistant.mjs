import {expect} from '@playwright/test';
import {chromium, webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const setupToken = 'browser-test-setup-key';
const address = '127.0.0.1:' + (process.env.DRG_BROWSER_PORT || '8049');
const origin = 'http://' + address;

function totp(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bits = [...secret].map(character => alphabet.indexOf(character).toString(2).padStart(5, '0')).join('');
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const bytes = Buffer.from(bits.match(/.{8}/g).map(value => parseInt(value, 2)));
  const hash = createHmac('sha1', bytes).update(counter).digest();
  return String((hash.readUInt32BE(hash[19] & 15) & 0x7fffffff) % 1000000).padStart(6, '0');
}

async function verifyMobileComposer(page, noteId, standalone) {
  await page.setViewportSize({width: 390, height: 844});
  if (standalone) await page.context().addInitScript(() => {
    if (new URLSearchParams(location.search).has('standalone')) {
      Object.defineProperty(navigator, 'standalone', {configurable: true, value: true});
      document.addEventListener('DOMContentLoaded', () => {
        document.documentElement.style.setProperty('--app-safe-top', '59px');
        document.documentElement.style.setProperty('--app-safe-bottom', '34px');
      }, {once: true});
    }
    const viewport = new EventTarget();
    Object.assign(viewport, {height: 844, offsetTop: 0, scale: 1});
    Object.defineProperty(window, 'visualViewport', {configurable: true, value: viewport});
  });
  await page.goto('/page/' + noteId + (standalone ? '?standalone' : ''));
  const tabs = page.locator('.mobile-app-tabs');
  const note = page.locator('#notion-page');
  const chat = page.locator('#note-ai-panel');
  const input = chat.locator('textarea');
  if (standalone) await expect(tabs).toBeVisible();
  await page.locator('#note-ai').click();
  await expect(note).toBeVisible();
  await expect(note.locator('#page-title')).toBeVisible();
  await expect(input).toBeVisible();
  await expect(input).toHaveAttribute('rows', '1');
  const geometry = () => page.evaluate(() => {
    const bounds = selector => document.querySelector(selector)?.getBoundingClientRect();
    const viewport = window.visualViewport;
    const textarea = bounds('#note-ai-panel textarea');
    const tabs = bounds('.mobile-app-tabs');
    const note = bounds('#notion-page');
    const composerIsInteractive = document.querySelector('#note-ai-panel').contains(
      document.elementFromPoint(textarea.left + textarea.width / 2, textarea.top + textarea.height / 2),
    );
    return {
      aboveTabs: !tabs?.height || textarea.bottom <= tabs.top + 1,
      aboveKeyboard: textarea.bottom <= viewport.offsetTop + viewport.height + 1,
      insideViewport: textarea.top >= viewport.offsetTop && textarea.left >= 0 && textarea.right <= innerWidth,
      noteVisible: Math.min(note.bottom, textarea.top) > note.top + 48,
      composerIsInteractive,
    };
  });
  const expected = {aboveTabs: true, aboveKeyboard: true, insideViewport: true, noteVisible: true, composerIsInteractive: true};
  await expect.poll(geometry).toEqual(expected);
  await input.fill('Черновик запроса');
  const changeViewport = async (height, offsetTop) => {
    await page.evaluate(({height, offsetTop}) => {
      Object.assign(window.visualViewport, {height, offsetTop});
      window.visualViewport.dispatchEvent(new Event('resize'));
    }, {height, offsetTop});
  };
  // Model iOS keyboard pan and dismissal with the same visual viewport events as the app.
  await changeViewport(390, 120);
  if (standalone) await expect(page.locator('html')).toHaveClass(/mobile-keyboard/);
  await expect(tabs).toBeHidden();
  await expect.poll(geometry).toEqual(expected);
  await expect(input).toBeFocused();
  await expect(input).toHaveValue('Черновик запроса');
  await changeViewport(844, 0);
  await expect(page.locator('html')).not.toHaveClass(/mobile-keyboard/);
  await expect.poll(geometry).toEqual(expected);
  await chat.locator('[data-ai-close]').click();
  if (standalone) await expect(tabs).toBeVisible();
  await expect(note).toBeVisible();
}

async function scenario(page) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const context = page.context();
  const password = 'Notes-assistant-browser-password-123';
  let csrf = '';
  async function api(path, method = 'GET', data) {
    const response = await context.request.fetch(origin + '/api' + path, {
      method, data, headers: {Origin: origin, 'X-CSRF-Token': csrf},
    });
    expect(response.ok(), path + ' ' + await response.text()).toBeTruthy();
    return response.json();
  }

  csrf = (await api('/setup', 'POST', {login: 'assistantowner', password, token: setupToken})).csrf;
  const enrollment = await api('/2fa/enroll', 'POST', {password});
  await api('/2fa/confirm', 'POST', {code: totp(enrollment.secret)});
  csrf = (await api('/2fa/activate', 'POST', {saved_recovery_codes: true})).csrf;
  let note = await api('/notes', 'POST', {title: 'Терминал на Mac', body: 'Исходный текст'});
  await page.route('**/api/me', async route => {
    const response = await route.fetch();
    await route.fulfill({response, json: {...await response.json(), notes_assistant_enabled: true}});
  });
  await page.route('**/api/widgets/**/rss', route => route.fulfill({json: {items: [], errors: []}}));
  await page.route('**/api/notes-assistant/pages/*', async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1);
    const {title, version} = await api('/notes/' + id);
    await route.fulfill({json: {id, title, version, read_only: false}});
  });

  const focus = [['terminal', 'command line'], ['mac', 'macos', 'mac os x']];
  const searches = [];
  const firstQuery = 'terminal macos';
  const secondQuery = 'command line macos';
  const firstCover = 'Терминал на Mac';
  const secondCover = 'Командная строка macOS';
  const png = await readFile('tests/fixtures/blue.png');
  const photo = (id, title) => ({
    id, title, thumbnail: 'https://thumb.wikimedia.org/assistant-cover.png', selection_token: id,
    attribution: {author: 'Тестовый автор', license: 'CC BY', source_url: 'https://commons.wikimedia.org/'},
  });
  const firstPhoto = photo('terminal', firstCover);
  await page.route('https://thumb.wikimedia.org/assistant-cover.png', route => route.fulfill({contentType: 'image/png', body: png}));
  await page.route('**/api/note-cover-search?**', async route => {
    const params = new URL(route.request().url()).searchParams;
    const query = params.get('q');
    searches.push({query, focus: JSON.parse(params.get('focus'))});
    const items = query === firstQuery ? [firstPhoto] : [firstPhoto, photo('command-line', secondCover)];
    await route.fulfill({json: {items}});
  });

  let failContext = false;
  let failedContextRequests = 0;
  await page.route('**/api/notes-assistant/context', async route => {
    if (failContext) {
      failContext = false;
      failedContextRequests++;
      return route.fulfill({status: 503, json: {detail: 'Временный сбой загрузки контекста'}});
    }
    const {target} = route.request().postDataJSON();
    const current = target ? await api('/notes/' + target.id) : null;
    await route.fulfill({json: {target: current ? {title: current.title, excerpt: current.body} : null, source: null}});
  });

  const firstPrompt = 'Подбери обложку с терминалом на Mac';
  const secondPrompt = 'Покажи другие варианты';
  const savePrompt = 'Переименуй заметку в «Команды терминала»';
  const followUp = 'Как теперь называется заметка?';
  const updatedTitle = 'Команды терминала';
  const createPrompt = 'Создай здесь памятку для первой встречи';
  const createFollowUp = 'Как называется новая памятка?';
  const newTitle = 'Первая встреча';
  const newBody = 'Согласовать время и подготовить вопросы.';
  const proposals = [];
  const editDraft = {id: 'assistant-edit-fixture', target: {id: note.id, version: note.version}, changes: {title: updatedTitle}};
  const createDraft = {id: 'assistant-create-fixture', target: null, changes: {title: newTitle, append: newBody}};
  let createdNote;
  await page.route('**/api/notes-assistant/propose', async route => {
    const request = route.request().postDataJSON();
    proposals.push(request);
    let response;
    switch (request.prompt) {
      case firstPrompt: response = {cover_query: firstQuery, cover_focus: focus}; break;
      case secondPrompt: response = {cover_query: secondQuery, cover_focus: focus}; break;
      case savePrompt: response = {draft: editDraft, fields: ['title'], before: {title: note.title}}; break;
      case createPrompt: response = {draft: createDraft, fields: ['title', 'append'], before: {}}; break;
      case createFollowUp: response = {message: 'Новая памятка называется «' + newTitle + '».'}; break;
      default: response = {message: 'Теперь она называется «' + updatedTitle + '».'};
    }
    await route.fulfill({json: response});
  });
  await page.route('**/api/notes-assistant/apply', async route => {
    const {draft} = route.request().postDataJSON();
    if (draft.id === createDraft.id) {
      expect(draft).toEqual(createDraft);
      createdNote = await api('/notes', 'POST', {title: newTitle, body: newBody});
      return route.fulfill({json: {id: createdNote.id, updated_blocks: []}});
    }
    expect(draft).toEqual(editDraft);
    note = await api('/notes/' + note.id, 'PUT', {title: updatedTitle, body: note.body, version: note.version});
    failContext = true;
    await route.fulfill({json: {id: note.id, updated_blocks: []}});
  });

  await page.goto('/page/' + note.id);
  await expect(page.locator('#note-ai')).toBeVisible();
  const pageWidth = await page.locator('#notion-page').evaluate(el => el.getBoundingClientRect().width);
  await page.locator('#note-ai').click();
  const chat = page.locator('#note-ai-panel');
  const prompt = chat.locator('textarea[name=prompt]');
  const submit = chat.locator('[data-ai-input] button[type=submit]');
  await expect(page.locator('#notion-page')).toBeVisible();
  const expandedWidth = await page.locator('#notion-page').evaluate(el => el.getBoundingClientRect().width);
  expect(Math.abs(expandedWidth - pageWidth)).toBeLessThanOrEqual(1);
  await expect(prompt).toHaveAttribute('rows', '1');
  async function send(text) {
    await expect(submit).toBeEnabled();
    await prompt.fill(text);
    await submit.click();
  }

  await send(firstPrompt);
  await expect(chat.locator('[data-ai-cover-choice]')).toContainText(firstCover);
  const history = chat.locator('[data-ai-log]');
  const historyButton = chat.locator('[data-ai-toggle-log]');
  await expect(history).toBeVisible();
  await historyButton.click();
  await expect(history).toBeHidden();
  await expect(prompt).toBeVisible();
  await historyButton.click();
  await expect(history).toBeVisible();
  await expect(history.locator('[data-ai-cover-choice]')).toContainText(firstCover);
  await send(secondPrompt);
  await expect(chat.getByRole('button', {name: new RegExp(secondCover)})).toBeVisible();
  await expect(chat.getByRole('button', {name: new RegExp(firstCover)})).toHaveCount(1);
  expect(searches).toEqual([{query: firstQuery, focus}, {query: secondQuery, focus}]);
  expect(proposals[1].history).toEqual(expect.arrayContaining([
    {role: 'user', text: firstPrompt},
    {role: 'assistant', text: expect.stringMatching(/\S/)},
  ]));

  await send(savePrompt);
  await expect(page.locator('#page-title')).toHaveValue(updatedTitle);
  await expect.poll(() => failedContextRequests).toBe(1);
  await send(followUp);
  await expect(chat.locator('.notes-ai-message.assistant').last()).toContainText('Теперь она называется');
  expect(proposals.at(-1).target).toEqual({id: note.id, version: note.version});

  await page.locator('#add-root-page').click();
  await expect(page).toHaveURL(origin + '/');
  await page.locator('#note-ai').click();
  await send(createPrompt);
  await expect(page.locator('#page-title')).toHaveValue(newTitle);
  await expect(chat.getByText(createPrompt, {exact: true})).toBeVisible();
  expect(proposals.at(-1).target).toBeNull();
  await expect(page).toHaveURL(origin + '/page/' + createdNote.id);
  await send(createFollowUp);
  await expect(chat.locator('.notes-ai-message.assistant').last()).toContainText(newTitle);
  expect(proposals.at(-1).target).toEqual({id: createdNote.id, version: createdNote.version});
  await verifyMobileComposer(page, createdNote.id, true);
  await verifyMobileComposer(page, createdNote.id, false);
  expect(errors).toEqual([]);
}

const directory = await mkdtemp(join(tmpdir(), 'drg-note-assistant-'));
const server = spawn(process.env.DRG_PYTHON || '.venv/bin/python', ['-m', 'gunicorn', '--config', 'gunicorn.conf.py', '--bind', address, 'tests.browser_app:app'], {
  env: {
    ...process.env, DATA_DIR: directory, DATABASE_URL: '', DATA_ENCRYPTION_ENABLED: 'true',
    DATA_ENCRYPTION_KEY: 'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',
    APP_SECRET: 'isolated-notes-assistant-browser-secret-'.repeat(3), SETUP_TOKEN: setupToken,
    PUBLIC_URL: origin, CALL_STUN_URLS: '', CALL_TURN_URLS: '',
  },
  stdio: ['ignore', 'ignore', 'pipe'],
});
let output = '';
let browser;
server.stderr.on('data', data => { output = (output + data).slice(-4000); });
const stopped = new Promise(resolve => server.once('close', resolve));
try {
  await expect.poll(async () => {
    try { return (await fetch(origin + '/api/health')).ok; }
    catch { return false; }
  }, {timeout: 12000, message: 'Disposable browser server failed to start'}).toBe(true);
  browser = await (process.env.DRG_BROWSER_ENGINE === 'webkit' ? webkit : chromium).launch({headless: true});
  const context = await browser.newContext({locale: 'ru-RU', baseURL: origin, viewport: {width: 1280, height: 900}, reducedMotion: 'reduce'});
  await scenario(await context.newPage());
  console.log('Note assistant cover, edit, new note, bottom composer and mobile keyboard passed in ' + browser.browserType().name());
} catch (error) {
  if (!browser && output) console.error(output);
  throw error;
} finally {
  await browser?.close();
  server.kill('SIGTERM');
  await stopped;
  await rm(directory, {recursive: true, force: true});
}
