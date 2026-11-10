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
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const password='Formatting-test-password-123';
 const settle=()=>page.evaluate(async()=>{await document.fonts.ready;await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));});
 async function api(path,method='GET',data,csrf=''){const r=await page.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();}
 const setScheme=async mode=>{const config=(await api('/appearance')).preferences;await api('/appearance','PUT',{...config,mode},csrf);await page.reload();await expect(page.locator('html')).toHaveAttribute('data-scheme',mode);await expect(page.locator('.bn-editor')).toBeVisible();await settle();};
 let csrf=(await api('/setup','POST',{login:'formatowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password},csrf);await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
 const note=await api('/notes','POST',{title:'Проверка оформления',blocks:[{id:'imported',type:'paragraph',content:[{type:'text',text:'Обычный текст. ',styles:{}},{type:'text',text:'docker stats',styles:{code:true}},{type:'text',text:' и жирный код',styles:{code:true,bold:true}}]},{id:'newtext',type:'paragraph',content:'Новый код с нуля'},{id:'command',type:'paragraph',content:''},{id:'docker',type:'codeBlock',props:{language:'docker'},content:'FROM python:3.14\nWORKDIR /app\nRUN python --version'}]},csrf);
 await page.setViewportSize({width:1200,height:850});await page.goto('/page/'+note.id);await expect(page.locator('[data-id=imported] .bn-inline-content')).toBeVisible();
 const ink=()=>page.locator('[data-id=imported] code').first().evaluate(el=>({color:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,font:getComputedStyle(el).fontFamily}));
 expect(await ink()).toMatchObject({color:'rgb(235, 87, 87)',background:'rgba(135, 131, 120, 0.15)'});
 expect((await ink()).font).toContain('Menlo');
 expect(await page.locator('[data-id=imported] strong code').evaluate(el=>Number(getComputedStyle(el).fontWeight))).toBeGreaterThanOrEqual(600);
 const field=page.locator('[data-id=newtext] .bn-inline-content');await field.evaluate(el=>{el.focus();const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);document.dispatchEvent(new Event('selectionchange'));});
 await expect(page.locator('.bn-formatting-toolbar')).toBeVisible();await page.locator('[data-test=code]').click();await page.waitForTimeout(600);
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+note.id)).blocks.find(b=>b.id==='newtext').content)).toContain('"code":true');
 // The shortcut toggles the same selection, without losing the text.
 await field.evaluate(el=>{el.focus();const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);document.dispatchEvent(new Event('selectionchange'));});
 await page.keyboard.press(process.platform==='darwin'?'Meta+e':'Control+e');
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+note.id)).blocks.find(b=>b.id==='newtext').content)).not.toContain('"code":true');
 await page.keyboard.press(process.platform==='darwin'?'Meta+e':'Control+e');
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+note.id)).blocks.find(b=>b.id==='newtext').content)).toContain('"code":true');
 // Create a real code block from an ordinary empty paragraph, through the UI.
 const command=page.locator('[data-id=command] .bn-inline-content');await command.click();await page.keyboard.type('/code');
 await page.keyboard.press('Enter');
 await expect(page.locator('[data-id=command] [data-content-type=codeBlock]')).toBeVisible();
 await page.keyboard.type('printf "Created from scratch"');
 await page.keyboard.press('Enter');await page.keyboard.press('Tab');await page.keyboard.type('echo "Second line"');
 await expect.poll(async()=>(await api('/notes/'+note.id)).blocks.find(b=>b.id==='command').type).toBe('codeBlock');
 await expect(page.locator('[data-id=command] pre code')).toContainText('  echo "Second line"');
 await page.locator('[data-id=command] pre code').evaluate(el=>{el.focus();const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);document.dispatchEvent(new Event('selectionchange'));});
 await page.keyboard.press(process.platform==='darwin'?'Meta+b':'Control+b');
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+note.id)).blocks.find(b=>b.id==='command').content)).toContain('"bold":true');
 await page.reload();await expect(page.locator('[data-id=newtext] code')).toHaveText('Новый код с нуля');
 await expect(page.locator('[data-id=command] pre code')).toContainText('Created from scratch');
 await expect(page.locator('[data-id=command] pre code strong').first()).toBeVisible();
 await expect.poll(()=>page.locator('[data-id=docker] code [style*="--shiki-light"]').count()).toBeGreaterThan(1);
 expect(await page.locator('[data-id=docker] pre code').evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
 await settle();await page.screenshot({path:'artifacts/drg-73/code-after-web.png'});
 await setScheme('dark');
 expect((await ink()).color).toBe('rgb(255, 123, 114)');
 await settle();await page.screenshot({path:'artifacts/drg-73/code-after-dark.png'});
 await page.setViewportSize({width:390,height:844});await settle();await page.screenshot({path:'artifacts/drg-73/code-after-webapp.png'});
 // A public read-only page has the same inline mark and cannot change styles.
 const publication=await api('/notes/'+note.id+'/shares','POST',{mode:'public'},csrf),guest=await browser.newContext({baseURL:origin,locale:'ru-RU'}),reader=await guest.newPage();
 await reader.goto('/share/'+publication.token);await expect(reader.locator('[data-id=newtext] code')).toHaveText('Новый код с нуля');
 expect(await reader.locator('[data-id=newtext] code').evaluate(el=>getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');await guest.close();
 // A long command wraps visually without inserting line breaks into its value.
 const longCommand='rsync --archive --verbose --progress /Volumes/Archive/VeryLongFolderNameWithoutSpaces/'.repeat(3)+' user@example.org:/srv/archive/';
 const wrapped=await api('/notes','POST',{title:'Длинные команды',blocks:[{id:'longcommand',type:'paragraph',content:''}]},csrf);
 await page.setViewportSize({width:1200,height:850});await page.goto('/page/'+wrapped.id);await expect(page.locator('[data-id=longcommand] .bn-inline-content')).toBeVisible();await setScheme('light');
 await page.locator('[data-id=longcommand] .bn-inline-content').click();await page.keyboard.type('/code');await page.keyboard.press('Enter');
 const longCode=page.locator('[data-id=longcommand] pre code');await expect(longCode).toBeVisible();await page.keyboard.insertText(longCommand);
 await page.locator('[data-id=longcommand] select').selectOption('bash');
 await expect.poll(async()=>{const value=(await api('/notes/'+wrapped.id)).blocks[0].content;return typeof value==='string'?value:value.map(n=>n.text||'').join('');}).toBe(longCommand);
 await page.reload();await expect(longCode).toHaveText(longCommand);await settle();
 await page.screenshot({path:'artifacts/drg-73/code-wrapping-web.png'});
 await page.setViewportSize({width:390,height:844});await settle();
 const size=await longCode.evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth,height:el.clientHeight,line:parseFloat(getComputedStyle(el).lineHeight)}));
 expect(size.scroll).toBeLessThanOrEqual(size.width+1);expect(size.height).toBeGreaterThan(size.line*3);
 await page.screenshot({path:'artifacts/drg-73/code-wrapping-webapp.png'});
 const sharedWrap=await api('/notes/'+wrapped.id+'/shares','POST',{mode:'public'},csrf),wrapGuest=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:390,height:844}}),wrapReader=await wrapGuest.newPage();
 await wrapReader.goto('/share/'+sharedWrap.token);const readonlyCode=wrapReader.locator('[data-id=longcommand] pre code');await expect(readonlyCode).toHaveText(longCommand);
 expect(await readonlyCode.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
 await wrapGuest.close();
 const layout=await api('/notes','POST',{title:'Блоки заметки',blocks:[
  {id:'quote',type:'quote',content:'Цитата с обычным текстом и ссылками'},
  {id:'calloutgray',type:'callout',props:{icon:'🧩',backgroundColor:'gray'},content:'Серый информационный блок',children:[{id:'inside',type:'paragraph',content:'Вложенный текст внутри того же блока'},{id:'insidecode',type:'codeBlock',props:{language:'bash'},content:'echo "Nested code"'}]},
  {id:'calloutdefault',type:'callout',content:'Новый блок по умолчанию'},
  {id:'calloutgreen',type:'callout',props:{icon:'✅',backgroundColor:'green'},content:'Зелёный блок'},
  {id:'heading',type:'heading',props:{level:2},content:'Заголовок и список'},
  {id:'bullet',type:'bulletListItem',content:'Пункт списка',children:[{id:'bulletchild',type:'bulletListItem',content:'Вложенный пункт',children:[{id:'grandchild',type:'bulletListItem',content:'Третий уровень'}]}]}
 ]},csrf);
 await page.setViewportSize({width:1200,height:950});await page.goto('/page/'+layout.id);await expect(page.locator('[data-id=calloutgray] .block-callout')).toBeVisible();await setScheme('light');
 if(!process.env.DRG_CAPTURE_BASELINE){
  const box=page.locator('[data-id=calloutgray]>.bn-block');
  expect(await box.evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgb(235, 236, 237)');
  expect(await page.locator('[data-id=calloutgreen]>.bn-block').evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgb(221, 237, 234)');
  expect(await box.locator('.block-callout').first().evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
  const bounds=await box.boundingBox(),child=await page.locator('.bn-block-outer[data-id=insidecode]').boundingBox();expect(child.y+child.height).toBeLessThan(bounds.y+bounds.height);
  const quote=await page.locator('[data-id=quote] blockquote').evaluate(el=>getComputedStyle(el).color);expect(quote).toBe(await page.locator('.bn-editor').evaluate(el=>getComputedStyle(el).color));
  // Reuse the page emoji picker, but update this block only.
  await box.getByRole('button',{name:'Значок блока',exact:true}).click();await expect(page.getByRole('heading',{name:'Значок блока',exact:true})).toBeVisible();
  await page.locator('[data-emoji-search]').fill('книг');const choice=page.locator('[data-emoji^="📚"]');await expect(choice).toBeVisible();const chosenIcon=await choice.getAttribute('data-emoji');await choice.click();
  await expect.poll(async()=>(await api('/notes/'+layout.id)).blocks.find(b=>b.id==='calloutgray').props.icon).toBe(chosenIcon);
  expect((await api('/notes/'+layout.id)).icon).toBe(layout.icon);
  await page.reload();await expect(box.locator('.callout-symbol')).toHaveText(chosenIcon);await settle();
 }
 const suffix=process.env.DRG_CAPTURE_BASELINE?'before':'after';
 await page.screenshot({path:'artifacts/drg-73/layout-'+suffix+'-web.png'});
 if(!process.env.DRG_CAPTURE_BASELINE){await setScheme('dark');await settle();
  const surface=id=>page.locator('[data-id='+id+']>.bn-block').evaluate(el=>getComputedStyle(el).backgroundColor);
  expect(await surface('calloutdefault')).toBe(await surface('calloutgray'));
  await page.screenshot({path:'artifacts/drg-73/layout-after-dark.png'});
 }
 expect(errors).toEqual([]);
}
const origin='http://127.0.0.1:8037',directory=await mkdtemp(tmpdir()+'/drg-73-inline-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8037','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  await mkdir('artifacts/drg-73',{recursive:true});await scenario(page);
  console.log('Notes formatting passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
