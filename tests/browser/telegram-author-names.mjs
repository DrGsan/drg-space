// Render the actual family room in the browser and the standalone mobile layout.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium, webkit} from 'playwright';
import {expect} from '@playwright/test';

const bundle = await build({
  entryPoints: ['web/modules/family-photos/index.js'], bundle: true, format: 'iife',
  globalName: 'Family', write: false, loader: {'.css': 'empty'},
});
const avatar = await readFile('tests/fixtures/blue.png');
const engine = process.env.DRG_BROWSER_ENGINE || 'chromium';
const browser = await (engine === 'webkit' ? webkit : chromium).launch();
try {
  for (const standalone of [false, true]) {
    for (const [locale, unknown] of [['en-US', 'Telegram participant'], ['ru-RU', 'Участник Telegram']]) {
      const page = await browser.newPage({
        locale, isMobile: standalone, hasTouch: standalone,
        viewport: {width: standalone ? 390 : 1120, height: 844},
      });
      await page.addInitScript(value => Object.defineProperty(navigator, 'standalone', {value}), standalone);
      const paths = [];
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('https://authors.test/**', route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/') return route.fulfill({contentType: 'text/html',
          body: '<meta name="viewport" content="width=device-width"><main></main>'});
        paths.push(url.pathname);
        return route.fulfill({body: avatar, contentType: 'image/png'});
      });
      await page.goto('https://authors.test/');
      await page.addScriptTag({content: bundle.outputFiles[0].text});
      await page.evaluate(() => {
        const gid = '-1001';
        const group = {id:gid, title:'Synthetic', kind:'supergroup', history_complete:true};
        const people = [
          {sender_id:'42', sender:'Anna Example', sender_avatar_url:'/api/family-photos/groups/-1001/people/42/avatar?v=1'},
          {sender_id:'43', sender:'Автор не уточнён'},
          {sender_id:'44', sender:'@example'},
          {sender_id:'45', sender:'Участник Telegram', sender_avatar_url:'/api/family-photos/groups/-1001/people/45/avatar?v=2'},
        ];
        const items = people.map((person, i) => ({
          id:i+1, group_id:gid, created:1700000000+i, text:'Synthetic', media:[], ...person,
        }));
        items[3].reactions=[{source:'telegram', emoji:'👍', count:1, people:[{id:'43', name:'Участник Telegram'}]}];
        const ctx = {state:{me:{id:'owner', settings:{}}},toast(text){throw Error(text);},
          async api(path){
            if (path === '/family-photos/groups') return {items:[group], status:{worker_online:true}};
            if (path.endsWith('/messages')) return {items, more:false};
            if (path.endsWith('/outbox')) return {items:[]};
            throw Error('Unexpected path '+path);
          }};
        window.room = Family.mountTelegramChat(document.querySelector('main'), ctx, gid, ()=>{});
      });
      const messages = page.locator('[data-fp-message-id]');
      await expect(messages).toHaveCount(4);
      assert.deepEqual(await messages.locator('.chat-message-meta strong').allTextContents(),
        ['Anna Example', unknown, '@example', unknown]);
      await expect(messages.last().locator('[data-reaction-people]').first()).toHaveAttribute('title', unknown);
      await expect(messages.first().locator('.chat-message-avatar img')).toBeVisible();
      await expect(messages.last().locator('.chat-message-avatar img')).toBeVisible();
      assert.deepEqual([...new Set(paths)].sort(), [
        '/api/family-photos/groups/-1001/people/42/avatar', '/api/family-photos/groups/-1001/people/45/avatar',
      ]);
      assert.deepEqual(errors, []);
      await page.evaluate(()=>window.room.destroy());
      await page.close();
      console.log(engine, standalone ? 'standalone' : 'browser', locale, 'passed');
    }
  }
} finally { await browser.close(); }
