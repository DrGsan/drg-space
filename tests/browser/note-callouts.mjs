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
 const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.stack)});const password='Blocks-browser-password-123';
 async function api(path,method='GET',data,csrf=''){const r=await page.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();}
 let csrf=(await api('/setup','POST',{login:'blockowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password},csrf);await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
 const p=(id,content)=>({id,type:'paragraph',content});
 const note=await api('/notes','POST',{title:'Цветные блоки',favorite:true,blocks:[p('kept','Обычный текст'),p('warning_start',[{type:'text',text:':::warning\n',styles:{}},{type:'text',text:'WARNING: ',styles:{bold:true}},{type:'link',href:'https://example.com',content:[{type:'text',text:'Ссылка',styles:{}}]}]),p('warning_end',':::'),p('info_start',':::info\nПервый абзац'),p('info_body','Второй абзац'),p('info_end',':::'),p('tip_start',':::tip\nСовет'),p('tip_end',':::'),p('success_start',':::success\nГотово'),p('success_end',':::'),{id:'diagram',type:'codeBlock',props:{language:'mermaid'},content:'flowchart LR\n  A[Начало] --> B[Конец]'}]},csrf);
 const leaf=await api('/notes','POST',{title:'Листовая страница'},csrf),child=await api('/notes','POST',{title:'Вложенная страница',parent_id:note.id},csrf);
 page.on('console',m=>{if(m.type()==='error')console.log('CONSOLE',m.text())});page.on('response',r=>{if(r.status()>=400)console.log('RESPONSE',r.status(),r.url())});await page.setViewportSize({width:1280,height:960});await page.goto('/page/'+note.id);
 await expect(page.locator('#page-title')).toHaveValue(note.title);await page.locator('#favorite-page').click();await expect(page.locator('.favorite-page[data-open-page="'+note.id+'"]')).toBeVisible();await expect(page.locator('.block-callout')).toHaveCount(4);await expect(page.locator('.block-callout[data-callout-kind=warning] strong')).toHaveText('WARNING: ');
 await expect(page.locator('.block-callout[data-callout-kind=warning] a')).toHaveAttribute('href','https://example.com');
 await expect(page.locator('.note-mermaid-preview[aria-busy=false] svg')).toBeVisible();await expect(page.locator('.note-mermaid-preview')).toContainText('Начало');expect((await page.locator('.note-mermaid-preview').boundingBox()).height).toBeGreaterThan(70);const backgrounds=await page.locator('.block-callout').evaluateAll(items=>items.map(el=>getComputedStyle(el.closest('.bn-block')).backgroundColor));expect(new Set(backgrounds).size).toBe(4);
 await expect.poll(async()=>{const saved=await api('/notes/'+note.id);return saved.blocks.filter(b=>b.type==='callout').length;}).toBe(4);
 const saved=await api('/notes/'+note.id);expect(saved.body).toContain(':::warning');expect(saved.blocks.find(b=>b.id==='info_start').children[0].id).toBe('info_body');
 const positions=await page.locator('[data-tree-row="'+note.id+'"] .tree-page-icon,[data-tree-row="'+leaf.id+'"] .tree-page-icon,.favorite-page[data-open-page="'+note.id+'"] .tree-page-icon').evaluateAll(elements=>elements.map(el=>el.getBoundingClientRect().left));expect(positions.length).toBeGreaterThanOrEqual(3);expect(Math.max(...positions)-Math.min(...positions)).toBeLessThan(1);

 await page.screenshot({path:'artifacts/note-callouts-desktop.png',fullPage:true});await expect(page.locator('.note-mermaid-preview svg')).toBeVisible();
 const code=page.locator('[data-id=diagram] code');const fillCode=async text=>{await code.evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);el.focus();});await page.evaluate(text=>{const data=new DataTransfer();data.setData('text/plain',text);document.activeElement.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));},text);};await fillCode('sequenceDiagram\n  Alice->>Bob: Привет');await expect(page.locator('.note-mermaid-preview')).toContainText('Привет');
 await fillCode('flowchart LR\n  A-->');await expect(page.locator('.note-mermaid-preview')).toHaveClass(/is-error/);
 await fillCode('flowchart LR\n  A[Сохранено] --> B[Готово]');await expect(page.locator('.note-mermaid-preview svg')).toBeVisible();await expect(page.locator('.note-mermaid-preview')).toContainText('Сохранено');
 await expect.poll(async()=>{const n=await api('/notes/'+note.id);return JSON.stringify(n.blocks.find(b=>b.id==='diagram').content);}).toContain('Сохранено');
 await page.reload();await expect(page.locator('.block-callout')).toHaveCount(4);await expect(page.locator('.note-mermaid-preview')).toContainText('Сохранено');
 await page.evaluate(()=>{document.documentElement.dataset.scheme='dark';window.dispatchEvent(new Event('drg-appearance'));});await expect(page.locator('.note-mermaid-preview[aria-busy=false] svg')).toBeVisible();await page.screenshot({path:'artifacts/note-callouts-dark.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/note-callouts-mobile.png',fullPage:true});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 // A normal document must not fetch the diagram runtime at all.
 const fresh=await page.context().newPage(),requests=[];fresh.on('request',r=>{if(r.url().includes('/assets/mermaid'))requests.push(r.url());});await fresh.goto('/page/'+leaf.id);await expect(fresh.locator('#page-title')).toHaveValue(leaf.title);await fresh.waitForTimeout(350);expect(requests).toEqual([]);await fresh.close();
 // Existing Markdown exports and shared seeds get the same typed blocks.
 for(const shared of [false,true]){
  const space=shared?await api('/social/families','POST',{name:'Alerts family'},csrf):null;
  const imported=await api('/notes','POST',{title:'Markdown import '+shared,space_id:space?.id||null,body:':::tip\n**Совет:** текст\n:::\n\n```mermaid\nflowchart LR\n A-->B\n```'},csrf);
  await page.goto('/page/'+imported.id);await expect(page.locator('.block-callout[data-callout-kind=tip]')).toBeVisible();await expect(page.locator('.note-mermaid-preview svg')).toBeVisible();
  await expect.poll(async()=>{const n=await api('/notes/'+imported.id);return n.blocks?.[0]?.props?.variant;}).toBe('tip');
 }
 const oldSpace=await api('/social/families','POST',{name:'Legacy shared alerts'},csrf);
 const oldBlocks=[p('before','До исправления'),p('old_start',':::warning\nСтарый канонический блок'),p('old_end',':::'),p('after','После исправления')];
 let old=await api('/notes','POST',{title:'Existing canonical document',space_id:oldSpace.id,blocks:oldBlocks},csrf);
 const publication=await api('/notes/'+old.id+'/shares','POST',{mode:'public',permission:'edit'},csrf),grant=await api('/shared/'+publication.token);
 const changed=await page.request.put(origin+'/api/shared/'+publication.token,{headers:{Origin:origin,'X-CSRF-Token':csrf,'X-Share-Edit-Token':grant.edit_token},data:{title:old.title,body:'Legacy',blocks:oldBlocks,version:old.version}});expect(changed.ok(),await changed.text()).toBe(true);
 await page.goto('/page/'+old.id);await expect(page.locator('.block-callout[data-callout-kind=warning]')).toContainText('Старый канонический блок');
 await expect.poll(async()=>{const n=await api('/notes/'+old.id);return n.blocks[1].props.variant;}).toBe('warning');
 const repaired=await api('/notes/'+old.id);expect(repaired.blocks.map(b=>b.id)).toEqual(['before','old_start','after']);
 const readLink=await api('/notes/'+note.id+'/shares','POST',{mode:'public'},csrf),guest=await browser.newContext({locale:'ru-RU',baseURL:origin}),publicPage=await guest.newPage();await publicPage.goto('/share/'+readLink.token);await expect(publicPage.locator('.block-callout')).toHaveCount(4);await expect(publicPage.locator('.note-mermaid-preview svg')).toBeVisible();await guest.close();
 const menuNote=await api('/notes','POST',{title:'Меню блоков',body:''},csrf);await page.goto('/page/'+menuNote.id);
 const editable=page.locator('#block-editor .bn-editor');await editable.click();await page.keyboard.type('/Совет');await page.getByRole('option',{name:/Совет/}).click();await expect(page.locator('.block-callout[data-callout-kind=tip]')).toBeVisible();
 await page.keyboard.press('Enter');await page.keyboard.type('/Mermaid');await page.getByRole('option',{name:/Mermaid Diagram/}).click();await expect(page.locator('.note-mermaid-preview svg')).toBeVisible();
 // Invalid and hostile input cannot run scripts, add remote media or change config.
 const unsafe=await page.evaluate(async()=>{const {diagram}=await import('/assets/mermaid.js');const results=[];for(const code of ['%%{init: {"securityLevel":"loose"}}%%\nflowchart LR\nA-->B','flowchart LR\nA@{img:"https://example.invalid/leak.png"}']){try{await diagram(code);results.push('allowed');}catch{results.push('blocked');}}const svg=await diagram('flowchart LR\n A[Safe]-->B[Done]\n click A "javascript:alert(1)"');const container=document.createElement('div');container.innerHTML=svg;return{results,unsafe:container.querySelectorAll('script,a,image,foreignObject,[onclick],[href]').length};});
 expect(unsafe).toEqual({results:['blocked','blocked'],unsafe:0});
 expect(errors).toEqual([]);
}
const origin='http://127.0.0.1:8041',directory=await mkdtemp(tmpdir()+'/drg-note-callouts-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8041','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  console.log('Callouts and Mermaid passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
