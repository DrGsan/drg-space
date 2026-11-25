import {createHmac} from 'node:crypto';
import {test, expect} from './installed-test.mjs';

test.use({serviceWorkers: 'block'});

function totp(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bits = [...secret].map(letter => alphabet.indexOf(letter).toString(2).padStart(5, '0')).join('');
  const key = Buffer.from(bits.match(/.{8}/g).map(byte => parseInt(byte, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const hash = createHmac('sha1', key).update(counter).digest();
  return String((hash.readUInt32BE(hash[19] & 15) & 0x7fffffff) % 1000000).padStart(6, '0');
}

async function setupNotes(page) {
  let csrf = '';
  async function post(path, data) {
    const result = await page.request.post('/api' + path, {
      headers: {Origin: 'http://127.0.0.1:8001', 'X-CSRF-Token': csrf},
      data,
    });
    expect(result.ok(), path + ' ' + await result.text()).toBe(true);
    return result.json();
  }
  const password = 'Isolated-notes-recovery-owner-123';
  csrf = (await post('/setup', {login: 'notesowner', password, token: 'browser-test-setup-key'})).csrf;
  const enrollment = await post('/2fa/enroll', {password});
  await post('/2fa/confirm', {code: totp(enrollment.secret)});
  csrf = (await post('/2fa/activate', {saved_recovery_codes: true})).csrf;
  const user = await (await page.request.get('/api/me')).json();
  const create = (title, body) => post('/notes', {title, body});
  const note = await create('First notebook', 'Original body stays visible');
  return {note, user, create};
}

function busy(route, code = 'operationWithDataIsInProgressRetryLater') {
  return route.fulfill({
    status: 503,
    headers: {'Retry-After': '1', 'Cache-Control': 'no-store'},
    json: {code, detail: 'Storage temporarily busy'},
  });
}

const row = (page, note) => page.locator(`[data-tree-row="${note.id}"]`);
const catalog = url => url.pathname === '/api/notes' && url.searchParams.has('catalog_revision');
const focusCatalog = page => page.evaluate(() => {
  window.dispatchEvent(new Event('focus'));
  window.dispatchEvent(new Event('online'));
  document.dispatchEvent(new Event('visibilitychange'));
});

test('standalone Notes recovers its initial catalog without reloading the document', async ({page}) => {
  const {note, user} = await setupNotes(page);
  await page.setViewportSize({width: 390, height: 844});
  await page.addInitScript(userId => {
    Object.defineProperty(navigator, 'standalone', {value: true});
    sessionStorage.setItem('drg-mobile-view:' + userId, 'notes');
  }, user.id);

  let documents = 0;
  page.on('request', request => { if (request.isNavigationRequest()) documents++; });
  let reads = 0;
  await page.route('**/api/notes', route => {
    expect(route.request().method()).toBe('GET');
    reads++;
    if (reads === 1) return busy(route);
    if (reads === 2) return busy(route, 'storage_busy');
    return route.continue();
  });
  await page.goto('/');
  await expect(row(page, note)).toBeVisible({timeout: 15000});
  await expect(page.locator('.mobile-app-tabs [data-mobile-view=notes]')).toHaveAttribute('aria-current', 'page');
  await expect(row(page, note)).toHaveCount(1);
  expect(reads).toBe(3);
  expect(documents).toBe(1);
});

test('an unrelated 503 keeps the normal workspace error and manual retry', async ({page}) => {
  const {note} = await setupNotes(page);
  let reads = 0;
  await page.route('**/api/notes', route => {
    if (++reads > 1) return route.continue();
    return route.fulfill({status: 503, headers: {'Retry-After': '1'}, json: {detail: 'Unrelated outage'}});
  });
  await page.goto('/page/' + note.id);
  await expect(page.locator('.workspace-error-message')).toContainText('Unrelated outage');
  expect(reads).toBe(1);
  await page.locator('[data-workspace-retry]').click();
  await expect(row(page, note)).toBeVisible();
  await expect(page.locator('#notion-page')).toContainText(note.body);
  expect(reads).toBe(2);
});

test('an open catalog adds notes automatically after a short storage lock', async ({page}) => {
  const {note, create} = await setupNotes(page);
  let reads = 0;
  let recovered = false;
  await page.route(catalog, route => {
    reads++;
    return recovered ? route.continue() : busy(route);
  });
  await page.goto('/page/' + note.id);
  const editor = page.locator('#notion-page');
  await expect(editor).toContainText(note.body);
  const originalEditor = await editor.elementHandle();
  const failure = page.waitForResponse(response => catalog(new URL(response.url())) && response.status() === 503);
  await focusCatalog(page);
  await failure;
  const added = await create('Second notebook', 'Visible after the lock');
  await expect(row(page, note)).toBeVisible();
  await expect(row(page, added)).toHaveCount(0);
  recovered = true;
  await expect(row(page, added)).toBeVisible({timeout: 10000});
  await expect(row(page, added)).toHaveCount(1);
  await expect(row(page, note)).toHaveCount(1);
  await expect(editor).toContainText(note.body);
  expect(await originalEditor.evaluate(node => node.isConnected)).toBe(true);
  expect(reads).toBe(2);
});

test('a busy catalog keeps its editor, bounds retries and merges recovered rows once', async ({page}) => {
  const {note, create} = await setupNotes(page);
  await page.clock.install();
  let reads = 0;
  let recovered = false;
  let release;
  const firstRead = new Promise(resolve => { release = resolve; });
  await page.route(catalog, async route => {
    expect(route.request().method()).toBe('GET');
    if (++reads === 1) await firstRead;
    return recovered ? route.continue() : busy(route);
  });
  await page.goto('/page/' + note.id);
  const editor = page.locator('#notion-page');
  await expect(editor).toContainText(note.body);
  const originalEditor = await editor.elementHandle();
  await focusCatalog(page);
  try {
    await expect.poll(() => reads).toBe(1);
    await focusCatalog(page);
    await expect(row(page, note)).toBeVisible();
    expect(reads).toBe(1);
  } finally {
    release();
  }
  const retry = page.locator('[data-notes-catalog-retry]');
  await expect(retry).toBeVisible({timeout: 30000});
  await expect(row(page, note)).toHaveCount(1);
  await expect(editor).toContainText(note.body);
  expect(reads).toBe(5);

  // Focus, online events and the periodic timer must not start another batch.
  await focusCatalog(page);
  await page.clock.fastForward(60000);
  const added = await create('Arrived during maintenance', 'A second body');
  expect(reads).toBe(5);
  recovered = true;
  await retry.click();
  await expect(row(page, added)).toBeVisible();
  await expect(row(page, added)).toHaveCount(1);
  await expect(row(page, note)).toHaveCount(1);
  await expect(row(page, note)).toHaveClass(/selected/);
  await expect(retry).toBeHidden();
  await expect(editor).toContainText(note.body);
  expect(await originalEditor.evaluate(node => node.isConnected)).toBe(true);
  expect(reads).toBe(6);
});
