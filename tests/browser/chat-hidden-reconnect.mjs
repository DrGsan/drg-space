// Real HTTP and room sockets on disposable installations. Only visibility is simulated.
import {chromium, webkit, expect, request} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac, randomUUID} from 'node:crypto';
import {createServer} from 'node:net';
import {setTimeout as delay} from 'node:timers/promises';

function totp(secret) {
  const bits = [...secret].map(c => 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5, '0')).join('');
  const key = Buffer.from(bits.match(/.{8}/g).map(byte => parseInt(byte, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const hash = createHmac('sha1', key).update(counter).digest();
  return String((hash.readUInt32BE(hash.at(-1) & 15) & 0x7fffffff) % 1000000).padStart(6, '0');
}

async function fixture(page, origin) {
  const headers = {Origin: origin};
  async function api(path, data, method = 'POST') {
    const cookie = (await page.context().cookies()).find(cookie => cookie.name === 'drg_csrf');
    if (cookie) headers['X-CSRF-Token'] = cookie.value;
    const response = await page.request.fetch('/api' + path, {method, headers, data});
    expect(response.ok(), path + ' ' + response.status()).toBe(true);
    return response.json();
  }
  const password = 'Isolated-chat-history-test-123';
  headers['X-CSRF-Token'] = (await api('/setup', {login: 'history-owner', password, token: 'browser-test-setup-key'})).csrf;
  const enrollment = await api('/2fa/enroll', {password});
  await api('/2fa/confirm', {code: totp(enrollment.secret)});
  headers['X-CSRF-Token'] = (await api('/2fa/activate', {saved_recovery_codes: true})).csrf;
  const {id} = await api('/social/families', {name: 'History test'});
  const history = '/chat/' + id + '/messages';
  const send = body => api(history, {body, client_id: randomUUID()});
  const original = await send('Original');
  return {id, history, original, send, api};
}

async function check(engine, origin) {
  const browser = await engine.launch();
  try {
    const standalone = engine === webkit;
    const context = await browser.newContext({baseURL: origin, serviceWorkers: 'block', locale: 'ru-RU',
      viewport: standalone ? {width: 390, height: 844} : {width: 1280, height: 900}});
    await context.addInitScript(standalone => {
      if (standalone) Object.defineProperty(navigator, 'standalone', {value: true});
      let hidden = false;
      Object.defineProperty(document, 'hidden', {get: () => hidden});
      Object.defineProperty(document, 'visibilityState', {get: () => hidden ? 'hidden' : 'visible'});
      window.setChatHidden = value => { hidden = value; document.dispatchEvent(new Event('visibilitychange')); };
      window.chatReady = 0;
      const Native = WebSocket;
      window.WebSocket = class extends Native {
        constructor(...args) {
          super(...args);
          if (!new URL(args[0]).pathname.startsWith('/api/live/chat/')) return;
          window.chatSocket = this;
          this.addEventListener('message', event => {
            if (JSON.parse(event.data).type === 'ready') window.chatReady++;
          });
        }
      };
    }, standalone);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const {id, history, original, send, api} = await fixture(page, origin);
    const historyPath = '/api' + history;
    const reads = [], responses = [];
    page.on('request', r => {
      const url = new URL(r.url());
      if (r.method() === 'GET' && url.pathname === historyPath) reads.push(url.search);
    });
    page.on('response', r => {
      const path = new URL(r.url()).pathname;
      if (['/api/me', '/api/refresh'].includes(path)) responses.push([path, r.status()]);
    });
    await page.goto('/#chat/' + id);
    await page.waitForFunction(() => window.chatReady === 1);
    const messages = page.locator('#chat-messages');
    await expect(messages).toContainText('Original');
    await page.evaluate(() => window.setChatHidden(true));
    await delay(1100);
    const start = reads.length, authStart = responses.length;
    const ready = count => page.waitForFunction(count => window.chatReady >= count, count);
    const disconnect = () => page.evaluate(() => window.chatSocket.close());

    // Keep the socket offline while another HTTP client writes a message and edit.
    let release;
    const gate = new Promise(resolve => release = resolve);
    await page.route('**/api/me', async route => { await gate; await route.continue(); });
    const reconnecting = page.waitForRequest(r => new URL(r.url()).pathname === '/api/me');
    await disconnect();
    await reconnecting;
    let missed;
    try {
      missed = await send('Missed message');
      await api(history + '/' + original.id, {body: 'Edited offline', revision: original.revision}, 'PUT');
    } finally { release(); }
    await ready(2);
    await page.unroute('**/api/me');
    // Remove just the access cookie to exercise the normal refresh grant.
    await context.clearCookies({name: 'drg_session'});
    await disconnect(); await ready(3);
    await disconnect(); await ready(4);
    await delay(500);
    const auth = responses.slice(authStart);
    const hiddenReads = reads.length - start;
    console.log(engine.name(), {hiddenReads, hiddenReconnects: 3, auth});
    expect(auth.filter(([path, status]) => path === '/api/me' && status === 200).length).toBeGreaterThanOrEqual(3);
    expect(auth).toContainEqual(['/api/refresh', 200]);
    expect(hiddenReads).toBe(0);
    await expect(messages).toContainText('Original');
    await expect(messages).not.toContainText('Missed message');

    const beforeReturn = reads.length;
    await page.evaluate(() => {
      window.setChatHidden(false);
      window.dispatchEvent(new Event('focus'));
      window.dispatchEvent(new Event('online'));
    });
    await expect(messages).toContainText('Edited offline');
    await expect(messages).toContainText('Missed message');
    await delay(500);
    expect(await messages.locator('[data-message-id]').evaluateAll(rows => rows.map(r => Number(r.dataset.messageId)))).toEqual([original.id, missed.id]);
    expect(reads.slice(beforeReturn)).toHaveLength(1);
    expect(new URLSearchParams(reads[beforeReturn]).get('after')).toBe(String(original.id));

    // Return to the conversation list before a reconnect and visibility return.
    await page.setViewportSize({width: 390, height: 844});
    await page.locator('[data-chat-back]').click();
    await page.evaluate(() => window.setChatHidden(true));
    await disconnect(); await ready(5);
    const inList = reads.length;
    await page.evaluate(() => window.setChatHidden(false)); await delay(350);
    expect(reads.length).toBe(inList);
    await page.locator('[data-room="' + id + '"]').click();
    await expect.poll(() => reads.length).toBe(inList + 1);

    // Another room may become inaccessible without revoking the user's session.
    // Deleting a disposable workspace denies the upgrade until it is restored.
    const other = await context.newPage();
    const otherRoom = await api('/social/families', {name: 'Pending removal'});
    await other.goto('/#chat/' + otherRoom.id);
    await other.waitForFunction(() => window.chatReady === 1);
    await other.evaluate(() => window.setChatHidden(true));
    await delay(500);
    let deniedReads = 0;
    other.on('request', r => { if (new URL(r.url()).pathname.endsWith('/' + otherRoom.id + '/messages')) deniedReads++; });
    await api('/social/spaces/' + otherRoom.id, undefined, 'DELETE');
    await other.evaluate(() => window.chatSocket.close());
    await expect(other.locator('#message-body')).toHaveCount(0, {timeout: 10000});
    expect(deniedReads).toBe(0);
    expect(await other.evaluate(() => window.chatReady)).toBe(1);
    await other.close();

    // Revoke the server session from a copy of these synthetic credentials.
    // The browser keeps its cookies, so it must discover revocation on reconnect.
    await page.evaluate(() => window.setChatHidden(true));
    const storageState = await context.storageState();
    const secondDevice = await request.newContext({baseURL: origin, storageState});
    try {
      const csrf = storageState.cookies.find(cookie => cookie.name === 'drg_csrf').value;
      expect((await secondDevice.post('/api/logout', {headers: {Origin: origin, 'X-CSRF-Token': csrf}})).ok()).toBe(true);
    } finally { await secondDevice.dispose(); }
    const beforeRevoke = reads.length;
    await disconnect();
    await expect(page.locator('#message-body')).toBeDisabled();
    expect(responses).toContainEqual(['/api/refresh', 401]);
    expect(reads.length).toBe(beforeRevoke);
    expect(await page.evaluate(() => window.chatReady)).toBe(5);
    expect(errors).toEqual([]);
    console.log(engine.name(), {deniedReads, resumeReads: 1, messagesInOrder: 2, closedRoomReads: 0, revokedSession: 'rejected', standalone});
  } finally { await browser.close(); }
}

async function run(engine) {
  const listener = createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const {port} = listener.address();
  await new Promise(resolve => listener.close(resolve));
  const origin = 'http://127.0.0.1:' + port;
  const server = spawn(process.env.DRG_PYTHON || '.venv/bin/python', ['-m', 'tests.browser_server'], {
    env: {...process.env, DRG_BROWSER_PORT: String(port)}, stdio: ['ignore', 'ignore', 'pipe']});
  const stopped = new Promise(resolve => server.once('exit', resolve));
  let log = '';
  server.stderr.on('data', chunk => { log = (log + chunk).slice(-4000); });
  try {
    await expect.poll(async () => {
      if (server.exitCode !== null) throw Error(log);
      return fetch(origin + '/api/health').then(r => r.ok, () => false);
    }, {timeout: 30000}).toBe(true);
    await check(engine, origin);
  } finally { server.kill('SIGTERM'); await stopped; }
}

const results = await Promise.allSettled([run(chromium), run(webkit)]);
for (const result of results) if (result.status === 'rejected') { console.error(result.reason); process.exitCode = 1; }
