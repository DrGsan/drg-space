import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

async function scenario(page){
  const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  await page.setViewportSize({width:1280,height:900});
  await page.route('**/api/widgets/**/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  const context=page.context(),password='Paste-menu-password-123';
  async function api(path,method='GET',data,token=''){
    const response=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':token},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  let csrf=(await api('/setup','POST',{login:'pasteowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await api('/2fa/enroll','POST',{password},csrf);
  await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);
  csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
  const note=await api('/notes','POST',{title:'Вставка из Notion',body:'',blocks:[{id:'paste_empty',type:'paragraph',content:[],children:[]}]},csrf);
  await page.goto('/page/'+note.id);
  const paste=async html=>{
    await page.locator('#block-editor [contenteditable=true]').evaluate((el,html)=>{
      const data=new DataTransfer();data.setData('text/html',html);data.setData('text/plain','Markdown fallback must not replace HTML');
      el.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data}));
    },html);
  };
  await page.locator('[data-id=paste_empty] .bn-inline-content').click();
  await paste(`<!-- notionvc: fixture --><ol><li>Создаём файл <code>temp.sh</code><pre><code class="language-bash">nano /root/temp.sh</code></pre><pre><code class="language-bash">#!/bin/bash\napt-get update</code></pre></li></ol>
    <ul class="toggle"><li>SSH <strong>Phrase</strong><pre><code class="language-bash">ssh-add ~/.ssh/id_rsa</code></pre><ul class="toggle"><li>Внутренний toggle<blockquote>Настоящая цитата</blockquote><p>Описание</p></li></ul></li><li>Пустой toggle</li></ul>
    <ul class="toggle"><li><details><summary><h2>Раскрывающийся заголовок</h2></summary><p>Текст заголовка</p><ul class="toggle"><li><details><summary>Экспортированный toggle</summary><hr><p>После разделителя</p></details></li></ul></details></li></ul>
    <ul><li>Обычный пункт<ul><li>Подпункт</li></ul></li></ul>
    <ul class="toggle"><li><p>Заголовок в абзаце</p><p>Дочерний абзац</p></li><li><div>Заголовок в div</div><pre><code>wrapped</code></pre></li><li>Соседний список</li><ul><li>Дочерний сосед</li></ul></ul>
    <ol start="4"><li>Четвёртый<pre><code>four</code></pre></li></ol><ul><li><input type="checkbox" checked>Задача<pre><code>task</code></pre></li></ul><p>Конец вставки</p>`);
  await expect(page.locator('[data-content-type=toggleListItem]').filter({hasText:'SSH Phrase'})).toHaveCount(1);
  await expect.poll(async()=>((await api('/notes/'+note.id)).blocks||[]).some(block=>block.content?.some(part=>part.text==='Конец вставки'))).toBe(true);
  const blocks=(await api('/notes/'+note.id)).blocks;
  const numbered=blocks[0];expect(numbered.type).toBe('numberedListItem');
  expect(numbered.children.map(block=>block.type)).toEqual(['codeBlock','codeBlock']);
  expect(numbered.children[1].content.map(part=>part.text).join('')).toContain('apt-get update');
  expect(numbered.content.some(part=>part.text==='temp.sh'&&part.styles.code)).toBe(true);
  const toggle=blocks.find(block=>block.type==='toggleListItem');
  expect(toggle.content.some(part=>part.text==='Phrase'&&part.styles.bold)).toBe(true);
  expect(toggle.children.map(block=>block.type)).toEqual(['codeBlock','toggleListItem']);
  expect(toggle.children[1].children.map(block=>block.type)).toEqual(['quote','paragraph']);
  const heading=blocks.find(block=>block.type==='heading');expect(heading.props).toMatchObject({level:2,isToggleable:true});
  expect(heading.children.map(block=>block.type)).toEqual(['paragraph','toggleListItem']);
  expect(heading.children[1].children.map(block=>block.type)).toEqual(['divider','paragraph']);
  const bullet=blocks.find(block=>block.type==='bulletListItem');expect(bullet.children[0].type).toBe('bulletListItem');
  const byTitle=title=>blocks.find(block=>block.content?.some(part=>part.text===title));
  expect(byTitle('Заголовок в абзаце').children[0].content[0].text).toBe('Дочерний абзац');
  expect(byTitle('Заголовок в div').children[0].type).toBe('codeBlock');
  expect(byTitle('Соседний список').children[0].content[0].text).toBe('Дочерний сосед');
  expect(byTitle('Четвёртый').props.start).toBe(4);
  expect(byTitle('Задача')).toMatchObject({type:'checkListItem',props:{checked:true}});
  await page.reload();await expect(page.locator('#page-save-status')).toHaveText('Сохранено');
  const toggleNode=page.locator('.bn-block-outer[data-id="'+toggle.id+'"]');
  const toggleButton=toggleNode.locator('.bn-toggle-button').first();
  await toggleButton.click();await expect(toggleNode.locator('.bn-block-outer[data-id="'+toggle.children[0].id+'"]')).toBeVisible();
  await toggleButton.click();await expect(toggleNode.locator('.bn-block-outer[data-id="'+toggle.children[0].id+'"]')).toBeHidden();
  const code=page.locator('.bn-block-outer[data-id="'+numbered.children[0].id+'"]');
  expect(await code.evaluate(el=>getComputedStyle(el,'::before').borderLeftWidth)).toBe('0px');
  await toggleButton.click();
  const nestedToggle=page.locator('.bn-block-outer[data-id="'+toggle.children[1].id+'"]');await nestedToggle.locator('.bn-toggle-button').first().click();
  await expect(nestedToggle.locator('blockquote')).toHaveCSS('border-left-width','2px');
  await page.screenshot({path:'artifacts/notes-notion-paste.png',fullPage:true});
  async function openMenu(){
    await code.locator('[data-content-type=codeBlock]').hover();
    const handle=page.getByRole('button',{name:'Открыть меню блока',exact:true});
    await handle.click();
    await expect(page.getByLabel('Поиск действий блока')).toBeFocused();
  }
  const menu=page.locator('.drg-block-menu'),submenu=page.locator('.drg-block-submenu');
  for(const viewport of [{width:1280,height:900},{width:800,height:600},{width:390,height:844},{width:320,height:430}]){
    await page.setViewportSize({width:1280,height:900});await page.reload();await openMenu();
    await page.setViewportSize(viewport);await expect(menu).toBeVisible();
    const initial=await menu.boundingBox();
    const metrics=async locator=>locator.evaluate(el=>({x:el.scrollWidth-el.clientWidth,y:el.scrollHeight-el.clientHeight}));
    expect((await metrics(menu)).x).toBe(0);
    if(viewport.height>=600)expect((await metrics(menu)).y).toBe(0);
    await page.getByRole('menuitem',{name:'Преобразовать в',exact:true}).hover();
    await expect(submenu).toBeVisible();
    for(const node of [menu,submenu]){
      const rect=await node.boundingBox();expect(rect.x).toBeGreaterThanOrEqual(0);expect(rect.y).toBeGreaterThanOrEqual(0);
      expect(rect.x+rect.width).toBeLessThanOrEqual(viewport.width);expect(rect.y+rect.height).toBeLessThanOrEqual(viewport.height);
      expect((await metrics(node)).x).toBe(0);
      if(node===submenu){const item=submenu.getByRole('menuitem',{name:'Заголовок 1',exact:true});await expect(item).toBeVisible();expect(await item.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width-15,r.y+r.height/2));})).toBe(true);}
    }
    expect((await menu.boundingBox()).height).toBe(initial.height);
    if(viewport.height>=600)expect((await metrics(menu)).y).toBe(0);
    await expect(menu).toHaveCSS('opacity','1');await expect(submenu).toHaveCSS('opacity','1');
    await page.screenshot({path:'artifacts/notes-block-menu-'+viewport.width+'.png'});
    await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);
  }
  await page.setViewportSize({width:1280,height:900});await page.reload();await openMenu();
  await page.getByRole('menuitem',{name:'Преобразовать в',exact:true}).press('ArrowRight');
  await expect(submenu).toBeVisible();
  await page.getByRole('menuitem',{name:'Раскрывающийся список',exact:true}).click();
  await expect(code.locator('[data-content-type=toggleListItem]')).toBeVisible();
  await page.keyboard.press('Meta+z');await expect(code.locator('[data-content-type=codeBlock]')).toBeVisible();
  expect(errors).toEqual([]);
}
// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8031',directory=await mkdtemp(tmpdir()+'/drg-notes-paste-menu-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8031','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Notes paste and menus passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
