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
  const paragraph=(id,url,mode,label=url)=>({id,type:'paragraph',props:mode?{linkDisplay:mode}:{},content:[{type:'link',href:url,content:[{type:'text',text:label,styles:{}}]}],children:[]});
  const original='https://example.com/imported',url='https://example.com/pasted';
  const savedCard=paragraph('saved-card','https://example.com/saved','bookmark','Saved title\nSaved description');
  Object.assign(savedCard.props,{title:'Saved title',description:'Saved description',url:''});
  const note=await api('/notes','POST',{title:'Превью ссылок',blocks:[paragraph('imported',original,undefined,'Imported titleDescription '+original+'Изображение'),paragraph('plain','https://example.com/plain','url'),savedCard,{id:'empty',type:'paragraph',content:[],children:[]}]},csrf);
  await page.route('**/api/note-link-preview',route=>route.fulfill({json:{title:'Красивое превью',description:'Описание страницы',available:true}}));
  await page.goto('/page/'+note.id);
  const card=id=>page.locator('.note-link-card-wrap[data-link-block="'+id+'"]');
  await expect(card('imported').locator('.note-link-card')).toContainText('Красивое превью');
  await expect(page.locator('[data-id=imported] .note-link-source')).toBeHidden();
  await expect(card('plain')).toHaveCount(0);
  await expect(card('saved-card').locator('strong')).toHaveText('Saved title');
  await expect(card('saved-card').locator('.note-link-description')).toHaveText('Saved description');
  await expect(page.locator('[data-id=plain] .bn-inline-content')).toContainText('https://example.com/plain');
  await page.locator('[data-id=empty] .bn-inline-content').click();
  await page.keyboard.press('ArrowDown');
  await expect.poll(()=>page.evaluate(()=>getSelection()?.anchorNode?.parentElement?.closest('[data-id]')?.dataset.id)).toBe('empty');
  await page.locator('#block-editor [contenteditable=true]').evaluate((el,url)=>{
    const data=new DataTransfer();data.setData('text/plain',url);el.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data}));
  },url);
  const menu=page.getByRole('menu',{name:'Вставить ссылку как'});
  await expect(menu).toBeVisible();
  await menu.getByRole('menuitemradio',{name:'Ссылка Обычная текстовая ссылка',exact:true}).click();
  await expect(card('empty')).toHaveCount(0);
  await expect.poll(async()=>(await api('/notes/'+note.id)).blocks.find(b=>b.id==='empty')?.props?.linkDisplay).toBe('url');
  // Ordinary inline URL pastes must retain the surrounding prose.
  await page.keyboard.type(' after');
  await expect(page.locator('[data-id=empty] .bn-inline-content')).toContainText(url+' after');
  await page.keyboard.press('End');await page.keyboard.press('Enter');
  await page.keyboard.type('https://example.com/typed-address');
  await expect(page.locator('#block-editor .bn-inline-content').filter({hasText:'https://example.com/typed-address'})).toBeVisible();
  await expect.poll(async()=>(await api('/notes/'+note.id)).body).toContain('https://example.com/typed-address');
  await card('imported').hover();
  await card('imported').getByLabel('Отображение ссылки',{exact:true}).click();
  await page.getByRole('menuitemradio',{name:/^Упоминание /}).click();
  await expect(card('imported')).toHaveClass(/note-link-mention/);
  await expect.poll(async()=>(await api('/notes/'+note.id)).blocks.find(b=>b.id==='imported')?.props?.linkDisplay).toBe('mention');
  await page.reload();await expect(card('imported')).toHaveClass(/note-link-mention/);
  expect((await api('/notes/'+note.id)).blocks.find(b=>b.id==='saved-card').props).toMatchObject({title:'Saved title',description:'Saved description',linkDisplay:'bookmark'});
  await expect(card('saved-card').locator('strong')).toHaveText('Saved title');
  await card('imported').hover();
  await card('imported').getByLabel('Изменить адрес ссылки').click();
  await page.getByRole('dialog',{name:'Изменить ссылку'}).getByLabel('Адрес ссылки',{exact:true}).fill('https://example.com/edited');
  await page.getByRole('dialog',{name:'Изменить ссылку'}).getByRole('button',{name:'Сохранить',exact:true}).click();
  await expect(card('imported').locator('a')).toHaveAttribute('href','https://example.com/edited');
  await expect(card('imported')).toHaveClass(/note-link-mention/);
  await card('imported').hover();await card('imported').getByLabel('Отображение ссылки',{exact:true}).click();
  await page.getByRole('menuitemradio',{name:/^Превью /}).click();
  await expect(card('imported')).not.toHaveClass(/note-link-mention/);
  // Escape restores editor navigation: Enter creates an adjacent paragraph.
  await page.keyboard.press('Enter');await page.keyboard.type('После карточки');
  await expect(page.locator('#block-editor')).toContainText('После карточки');
  await expect(card('imported')).toHaveCount(1);
  await expect.poll(async()=>(await api('/notes/'+note.id)).body).toContain('После карточки');
  const stored=await api('/notes/'+note.id);
  expect(stored.body).toContain('https://example.com/edited');
  expect(stored.body).not.toContain('Красивое превью');
  await page.keyboard.press('End');await page.keyboard.press('Enter');
  await page.locator('#block-editor [contenteditable=true]').evaluate(el=>{
    const data=new DataTransfer();data.setData('text/plain','https://example.com/new-bookmark');el.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data}));
  });
  await page.getByRole('menuitemradio',{name:/^Превью /}).click();
  const pastedCard=page.locator('.note-link-card-wrap').filter({has:page.locator('a[href="https://example.com/new-bookmark"]')});
  await expect(pastedCard).toBeVisible();
  await page.keyboard.press('Backspace');await expect(pastedCard).toHaveCount(0);
  await page.keyboard.press('ControlOrMeta+z');await expect(pastedCard).toBeVisible();
  await page.screenshot({path:'artifacts/notes-link-display.png',fullPage:true});
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});
    await card('imported').getByLabel('Отображение ссылки',{exact:true}).click();
    const popup=page.getByRole('menu',{name:'Отображение ссылки'});await expect(popup).toBeVisible();
    const box=await popup.boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);
    await page.screenshot({path:'artifacts/notes-link-display-'+width+'.png'});
    await page.keyboard.press('Escape');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
  }
  await page.evaluate(()=>{document.documentElement.dataset.scheme='dark';window.dispatchEvent(new Event('drg-appearance'));});
  await expect.poll(()=>card('imported').locator('a').evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgb(29, 39, 54)');
  await page.screenshot({path:'artifacts/notes-link-display-dark.png'});
  // The same paragraph property survives the collaborative document projection.
  const family=await api('/social/families','POST',{name:'Семья'},csrf);
  const shared=await api('/notes','POST',{title:'Общая карточка',space_id:family.id,blocks:[paragraph('sharedlink','https://example.com/shared')]},csrf);
  await page.setViewportSize({width:1280,height:900});await page.goto('/page/'+shared.id);
  await card('sharedlink').hover();await card('sharedlink').getByLabel('Отображение ссылки',{exact:true}).click();
  await page.getByRole('menuitemradio',{name:/^Упоминание /}).click();
  await expect.poll(async()=>(await api('/notes/'+shared.id)).blocks.find(b=>b.id==='sharedlink')?.props?.linkDisplay).toBe('mention');
  await page.reload();await expect(card('sharedlink')).toHaveClass(/note-link-mention/);
  const share=await api('/notes/'+shared.id+'/shares','POST',{mode:'public'},csrf);
  const anonymous=await browser.newContext({locale:'ru-RU',baseURL:origin}),publicPage=await anonymous.newPage();
  await publicPage.goto('/share/'+share.token);await expect(publicPage.locator('.note-link-card')).toBeVisible();
  await expect(publicPage.locator('.note-link-options')).toHaveCount(0);await expect(publicPage.locator('.note-link-source')).toBeHidden();
  await anonymous.close();expect(errors).toEqual([]);
}
// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8034',directory=await mkdtemp(tmpdir()+'/drg-notes-link-display-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8034','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  console.log('Notes link display passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
