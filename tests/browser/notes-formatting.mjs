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
 async function api(path,method='GET',data,csrf=''){
  const response=await page.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(response.ok(),path+' '+await response.text()).toBe(true);return response.json();
 }
 let csrf=(await api('/setup','POST',{login:'formatowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password},csrf);await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
 const blocks=[{id:'heading_one',type:'heading',props:{level:1,isToggleable:true},content:'Пример заголовка',children:[{type:'paragraph',content:'Обычный текст под заголовком.'}]},{id:'format_text',type:'paragraph',content:[{type:'text',text:'Добрый день! ',styles:{bold:true,italic:true,underline:true}},{type:'text',text:'Выделите текст для форматирования.',styles:{}}]},{id:'equation_text',type:'paragraph',content:'E = mc^2'}];
 const note=await api('/notes','POST',{title:'Форматирование текста',blocks},csrf);await page.setViewportSize({width:1280,height:900});await page.goto('/page/'+note.id);
 await expect(page.locator('.bn-block-outer[data-id=heading_one]')).toBeVisible();
 for(const level of [1,2,3]){
  if(level!==1){await api('/notes/'+note.id,'PUT',{title:note.title,blocks:[{...blocks[0],props:{level,isToggleable:true}},...blocks.slice(1)],version:(await api('/notes/'+note.id)).version},csrf);await page.reload();}
  const title=page.locator('[data-id=heading_one]>.bn-block>.bn-block-content .bn-inline-content');await title.hover();await expect(page.locator('.bn-side-menu')).toBeVisible();
  const alignment=await title.evaluate(el=>{const box=el.getBoundingClientRect(),style=getComputedStyle(el),line=parseFloat(style.lineHeight);const buttons=[...document.querySelectorAll('.bn-side-menu .bn-button')];return {line:box.top+line/2,buttons:buttons.map(b=>{const r=b.getBoundingClientRect();return {center:r.top+r.height/2,width:r.width,height:r.height};})};});
  for(const button of alignment.buttons){expect(Math.abs(button.center-alignment.line)).toBeLessThan(2);expect(button.width).toBe(button.height);}
  if(level===1){const box=await title.boundingBox();await page.screenshot({path:'artifacts/notes-heading-handles.png',clip:{x:Math.max(0,box.x-70),y:box.y-8,width:Math.min(660,1280-box.x+70),height:110}});}
 }
 async function selectText(id){await page.locator('.bn-block-outer[data-id='+id+']>.bn-block>.bn-block-content .bn-inline-content').evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);el.focus();document.dispatchEvent(new Event('selectionchange'));});}
 await selectText('format_text');await expect(page.locator('.bn-formatting-toolbar')).toBeVisible();
 await expect(page.locator('[data-test=code]')).toBeVisible();await expect(page.locator('[data-format-equation]')).toBeVisible();await expect(page.locator('[data-format-clear]')).toBeVisible();
 await page.screenshot({path:'artifacts/notes-formatting-toolbar.png'});
 await page.locator('[data-format-clear]').click();
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+note.id)).blocks.find(b=>b.id==='format_text').content)).not.toContain('"bold":true');
 await selectText('equation_text');await page.locator('[data-format-equation]').click();
 await expect(page.getByRole('dialog',{name:'Формула',exact:true})).toBeVisible();await page.getByLabel('Запись LaTeX').fill('E=mc^2');await page.getByRole('button',{name:'Готово',exact:true}).click();
 await expect(page.locator('.drg-inline-equation .katex')).toBeVisible();
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+note.id)).blocks)).toContain('"type":"equation"');
 const fraction=String.raw`\frac{a}{b}`;
 await page.reload();await expect(page.locator('.drg-inline-equation .katex')).toBeVisible();await page.locator('.drg-inline-equation').click();await page.getByLabel('Запись LaTeX').fill(fraction);await page.getByRole('button',{name:'Готово',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Формула',exact:true})).toHaveCount(0);
 await expect.poll(async()=>(await api('/notes/'+note.id)).body).toContain(fraction).catch(async error=>{console.log(JSON.stringify({saved:await api('/notes/'+note.id),errors,status:await page.locator('#page-save-status').textContent(),rendered:await page.locator('.drg-inline-equation').allTextContents()}));throw error;});await page.screenshot({path:'artifacts/notes-equation.png'});
 // Spoilers retain styles, stay local when revealed, and round-trip Markdown.
 const secret=await api('/notes','POST',{title:'Скрытый текст',blocks:[{id:'secret',type:'paragraph',content:[{type:'text',text:'Обычный текст. ',styles:{}},{type:'text',text:'Секретное слово',styles:{bold:true,code:true}}]},{id:'wrapped',type:'paragraph',content:[{type:'text',text:'Длинный скрытый фрагмент '.repeat(18),styles:{spoiler:true}}]},{id:'mixed',type:'paragraph',content:[{type:'text',text:'Первая ',styles:{bold:true,spoiler:true}},{type:'text',text:'вторая',styles:{italic:true,spoiler:true}}]},{id:'hidden_link',type:'paragraph',content:[{type:'link',href:'https://example.com',content:[{type:'text',text:'Скрытая ссылка',styles:{spoiler:true}}]}]}]},csrf);
 await page.goto('/page/'+secret.id);await expect(page.locator('[data-id=secret] code')).toBeVisible();
 await page.locator('[data-id=secret] code').evaluate(el=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);document.dispatchEvent(new Event('selectionchange'));});
 await expect(page.locator('[data-format-spoiler]')).toBeVisible();await page.waitForTimeout(200);await page.screenshot({path:'artifacts/notes-spoiler-toolbar.png'});
 await page.locator('[data-format-spoiler]').click();
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+secret.id)).blocks.find(b=>b.id==='secret').content)).toContain('"spoiler":true');
 await page.locator('#page-title').click({position:{x:5,y:20}});await page.evaluate(()=>getSelection().removeAllRanges());
 const hidden=page.locator('.drg-spoiler').filter({hasText:'Секретное слово'});await expect(hidden).toHaveAttribute('role','button');
 await expect(hidden.locator('.drg-spoiler-content')).toHaveAttribute('aria-hidden','true');
 expect(await hidden.evaluate(el=>getComputedStyle(el.querySelector('span')).color)).toBe('rgba(0, 0, 0, 0)');
 await expect(page.locator('.drg-spoiler-dust')).toHaveCount(0);
 expect(await hidden.evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
 expect(await hidden.evaluate(el=>getComputedStyle(el).backgroundImage)).toContain('radial-gradient');
 const dust=await hidden.evaluate(el=>getComputedStyle(el).backgroundPosition);await page.waitForTimeout(100);
 expect(await hidden.evaluate(el=>getComputedStyle(el).backgroundPosition)).not.toBe(dust);
 await page.screenshot({path:'artifacts/notes-spoiler-hidden.png'});const version=(await api('/notes/'+secret.id)).version;
 await hidden.click();await expect(hidden).toHaveClass(/is-revealed/);await expect(hidden.locator('.drg-spoiler-content')).not.toHaveAttribute('aria-hidden','true');
 await page.waitForTimeout(700);expect((await api('/notes/'+secret.id)).version).toBe(version);
 await page.screenshot({path:'artifacts/notes-spoiler-revealed.png'});
 await page.reload();await expect(hidden).not.toHaveClass(/is-revealed/);
 await hidden.focus();await page.keyboard.press('Enter');await expect(hidden).toHaveClass(/is-revealed/);
 await page.locator('#page-actions').click();await page.locator('#mode-markdown').click();
 await expect(page.locator('#markdown-source')).toHaveValue(/\|\|.*Секретное слово.*\|\|/);
 await page.locator('#markdown-source').fill((await page.locator('#markdown-source').inputValue())+'\n');
 await page.locator('#page-actions').click();await page.locator('#mode-blocks').click();await expect(hidden).toHaveCount(1);
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+secret.id)).blocks)).toContain('\"spoiler\":true');
 await page.locator('#page-title').click({position:{x:5,y:20}});await page.evaluate(()=>getSelection().removeAllRanges());
 // A hidden link cannot turn into a bookmark or navigate on its first tap.
 const link=page.locator('.drg-spoiler').filter({hasText:'Скрытая ссылка'});await expect(link).toBeVisible();await link.click();await expect(link).toHaveClass(/is-revealed/);expect(page.url()).toContain('/page/'+secret.id);
 const mixed=page.locator('.bn-inline-content').filter({hasText:'Первая вторая'}).locator('.drg-spoiler');const fragments=await mixed.count();expect(fragments).toBeGreaterThanOrEqual(2);await mixed.first().click();for(let i=0;i<fragments;i++)await expect(mixed.nth(i)).toHaveClass(/is-revealed/);
 // Keyboard formatting toggles off again without dropping the other marks.
 await hidden.locator('.drg-spoiler-content').evaluate(el=>{el.closest('[contenteditable=true]').focus();const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);document.dispatchEvent(new Event('selectionchange'));});
 await page.keyboard.press('ControlOrMeta+Shift+p');await expect(hidden).toHaveCount(0);
 await page.keyboard.press('ControlOrMeta+z');await expect(hidden).toHaveCount(1);
 await page.setViewportSize({width:390,height:844});await expect(page.locator('.bn-formatting-toolbar')).toBeVisible();await page.waitForTimeout(200);const menuBox=await page.locator('.bn-formatting-toolbar').boundingBox();expect(menuBox.x).toBeGreaterThanOrEqual(0);expect(menuBox.x+menuBox.width).toBeLessThanOrEqual(390);await page.screenshot({path:'artifacts/notes-spoiler-mobile-toolbar.png'});await page.locator('#page-title').focus();await page.evaluate(()=>getSelection().removeAllRanges());await page.screenshot({path:'artifacts/notes-spoiler-mobile.png'});
 await page.emulateMedia({reducedMotion:'reduce'});expect(await hidden.evaluate(el=>getComputedStyle(el).animationName)).toBe('none');
 // Published read-only notes use the same concealment and disclosure.
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+secret.id)).blocks)).toContain('\"spoiler\":true');
 await page.evaluate(()=>{document.documentElement.dataset.scheme='dark';window.dispatchEvent(new Event('drg-appearance'));});await page.waitForTimeout(150);await page.screenshot({path:'artifacts/notes-spoiler-dark.png'});
 const publication=await api('/notes/'+secret.id+'/shares','POST',{mode:'public'},csrf);const guest=await browser.newContext({locale:'ru-RU',baseURL:origin}),reader=await guest.newPage();
 await reader.goto('/share/'+publication.token);const publicSecret=reader.locator('.drg-spoiler').filter({hasText:'Секретное слово'});await expect(publicSecret).toHaveAttribute('role','button');await publicSecret.focus();await reader.keyboard.press('Space');await expect(publicSecret).toHaveClass(/is-revealed/);await guest.close();
 expect(errors).toEqual([]);
}
const origin='http://127.0.0.1:8037',directory=await mkdtemp(tmpdir()+'/drg-notes-formatting-');
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
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Notes formatting passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
