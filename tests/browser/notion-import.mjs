import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtemp,rm,mkdir,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {build} from 'esbuild';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
const python=process.env.DRG_PYTHON||'.venv/bin/python';
const zip=files=>execFileSync(python,['-c',`import sys,json,io,zipfile
out=io.BytesIO()
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
 for name,value in json.load(sys.stdin).items():z.writestr(name,value)
sys.stdout.buffer.write(out.getvalue())`],{input:JSON.stringify(files)});
const html=(title,body)=>`<article class="page"><header><h1 class="page-title">${title}</h1></header><div class="page-body">${body}</div></article>`;
const root='Root '+'a'.repeat(32),child=root+'/Child '+'b'.repeat(32);
const fixture=zip({[root+'.html']:html('Root',`<figure class="link-to-page"><a href="${child}.html">Child</a></figure><figure class="image"><img src="https://notion-image.example/original.png"><figcaption>Remote original</figcaption></figure>`),
  [child+'.html']:html('Child','<ul class="toggle"><li><details><summary>Commands</summary><pre><code class="language-bash">echo hello</code></pre></details></li></ul><table><tr><th>Name</th><th>Value</th></tr><tr><td>one</td><td>two</td></tr></table><p><a href="manual.txt">Manual</a></p>'),[root+'/manual.txt']:'original file bytes'});
const extra=zip({['Extra '+'c'.repeat(32)+'.md']:'# Extra\n\nMarkdown content'});
const origin='http://127.0.0.1:8036',directory=await mkdtemp(tmpdir()+'/drg-notion-import-');
const env={...process.env,DRG_NOTION_IMAGE_FIXTURE:'1',DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-notion-import-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''};
const server=spawn(python,['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8036','tests.browser_app:app'],{env,stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));
try{
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok)break;}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true,...(process.env.DRG_BROWSER_CHANNEL?{channel:process.env.DRG_BROWSER_CHANNEL}:{})});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/widgets/**/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  let csrf='';
  async function api(path,method='GET',data){
    const response=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  const password='Import-browser-password-123';
  csrf=(await api('/setup','POST',{login:'importowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await api('/2fa/enroll','POST',{password});
  await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});
  csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
  await page.goto('/');
  await page.locator('[data-view=settings]').click();
  await page.locator('[data-preference-key=data] summary').click();
  await page.getByRole('button',{name:'Перенести из Notion',exact:true}).click();
  // Opening an archive picker can outlive the 15-minute access cookie. The
  // original ZIP selection must survive refresh, without a second user action.
  const access=(await context.cookies()).find(cookie=>cookie.name==='drg_session');
  await context.addCookies([{...access,value:'expired-access-cookie'}]);
  const previewStatuses=[];
  page.on('response',response=>{if(response.url().endsWith('/api/account/notion/preview'))previewStatuses.push(response.status());});
  await page.locator('[data-notion-form] input[type=file]').setInputFiles([{name:'html.zip',mimeType:'application/zip',buffer:fixture},{name:'md.zip',mimeType:'application/zip',buffer:extra}]);
  await expect(page.getByRole('heading',{name:'Архив проверен'})).toBeVisible();
  expect(previewStatuses).toEqual([401,200]);
  csrf=(await context.cookies()).find(cookie=>cookie.name==='drg_csrf').value;
  const selectedArchives=[{name:'html.zip',mimeType:'application/zip',buffer:fixture},{name:'md.zip',mimeType:'application/zip',buffer:extra}];
  // A lost upload must keep the selected files and allow one explicit retry.
  let interruptedUploads=0;
  const interrupt=async route=>{interruptedUploads++;await route.abort('failed');};
  await page.route('**/api/account/notion/preview',interrupt);
  await page.locator('[data-notion-form] input[type=file]').setInputFiles(selectedArchives);
  await expect(page.locator('[data-notion-status]')).toHaveAttribute('data-busy','false');
  await expect(page.locator('[data-notion-status]')).toContainText('Не удалось загрузить ZIP');
  expect(await page.locator('[data-notion-form] input').evaluate(input=>input.files.length)).toBe(2);
  await page.unroute('**/api/account/notion/preview',interrupt);
  await page.getByRole('button',{name:'Проверить архив',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Архив проверен'})).toBeVisible();
  expect(interruptedUploads).toBe(1);
  expect((await api('/notes')).length).toBe(0);
  await mkdir('artifacts',{recursive:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'artifacts/notion-import-preview-mobile.png',fullPage:true});
  await context.addCookies([{...(await context.cookies()).find(cookie=>cookie.name==='drg_session'),value:'expired-again-before-import'}]);
  await page.getByRole('button',{name:'Добавить в мои заметки',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Импорт завершён'})).toBeVisible();
  csrf=(await context.cookies()).find(cookie=>cookie.name==='drg_csrf').value;
  await page.getByRole('button',{name:'Открыть заметки',exact:true}).click();
  const pages=await api('/notes'),childPage=pages.find(p=>p.title==='Child');
  expect(pages.length).toBe(3);
  await page.setViewportSize({width:1280,height:900});
  const rootPage=pages.find(p=>p.title==='Root'),extraPage=pages.find(p=>p.title==='Extra');
  const icon=execFileSync(python,['-c',"import io,sys;from PIL import Image;s=io.BytesIO();Image.new('RGB',(24,24),'#d77744').save(s,format='PNG');sys.stdout.buffer.write(s.getvalue())"]);
  const uploaded=await context.request.post(origin+'/api/notes/'+childPage.id+'/assets/icon',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:icon});expect(uploaded.ok()).toBe(true);
  await page.goto('/page/'+rootPage.id);
  const importedImage=page.locator('#block-editor [data-content-type=image] img');
  await expect(importedImage).toBeVisible();await expect.poll(()=>importedImage.evaluate(image=>image.naturalWidth)).toBe(48);
  expect(await importedImage.getAttribute('src')).toContain('/api/notes/');
  const pageLink=page.locator('.inline-page-link'),linkTitle=pageLink.locator('.inline-page-link-title');
  await expect(pageLink).toHaveText('Child');await expect(pageLink.locator('img')).toBeVisible();
  await expect.poll(()=>pageLink.locator('img').evaluate(image=>image.naturalWidth)).toBeGreaterThan(0);
  for(const scheme of ['light','dark']){
    await page.evaluate(scheme=>{document.documentElement.dataset.scheme=scheme;window.dispatchEvent(new CustomEvent('drg-appearance'));},scheme);await page.mouse.move(0,0);
    await expect(pageLink).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
    await expect(pageLink).toHaveCSS('color',await page.evaluate(()=>getComputedStyle(document.body).color));
    await expect(linkTitle).toHaveCSS('text-decoration-line','underline');
    const box=await pageLink.boundingBox();expect(box.width).toBeLessThan(200);expect(box.height).toBeLessThan(36);
    await page.screenshot({path:'artifacts/notion-page-links-'+scheme+'.png',fullPage:true});
  }
  await page.evaluate(()=>{document.documentElement.dataset.scheme='light';window.dispatchEvent(new CustomEvent('drg-appearance'));});
  await pageLink.focus();await pageLink.press('Enter');await expect(page.locator('#page-title')).toHaveValue('Child');
  const toggle=()=>page.locator('[data-expand="'+rootPage.id+'"]'),childRow=()=>page.locator('[data-tree-row="'+childPage.id+'"]');
  await expect(toggle()).toHaveAttribute('aria-expanded','false');await expect(childRow()).toHaveCount(0);
  await toggle().click();await expect(childRow()).toBeVisible();
  await page.locator('[data-open-page="'+extraPage.id+'"]').click();await expect(childRow()).toBeVisible();
  await page.locator('#notes-main-menu').click();await page.locator('[data-view=settings]').click();await page.locator('[data-view=notes]').click();
  await expect(toggle()).toHaveAttribute('aria-expanded','true');await expect(childRow()).toBeVisible();
  await toggle().click();await expect(childRow()).toHaveCount(0);
  await toggle().click();await expect(childRow()).toBeVisible();
  await page.locator('[data-open-page="'+childPage.id+'"]').click();
  await page.reload();
  await expect(toggle()).toHaveAttribute('aria-expanded','false');await expect(childRow()).toHaveCount(0);
  await expect(page.locator('[data-content-type=toggleListItem]')).toHaveCount(1);
  await page.locator('.bn-toggle-button').first().click();
  await expect(page.locator('[data-content-type=codeBlock]')).toBeVisible();
  await expect(page.locator('[data-content-type=table]')).toBeVisible();
  const url=JSON.stringify(childPage.blocks).match(/http[^" ]+\/files\/[^" ]+/)[0];
  const attachment=await context.request.get(url);expect(await attachment.text()).toBe('original file bytes');
  await page.screenshot({path:'artifacts/notion-import-page.png',fullPage:true});
  // The page import button accepts both plain Markdown and a complete Notion ZIP.
  await page.locator('#page-actions').click();
  const mdChooser=page.waitForEvent('filechooser');
  await page.locator('[data-page-action="import"]').click();
  page.once('dialog',dialog=>dialog.accept());
  await (await mdChooser).setFiles({name:'test.md',mimeType:'text/markdown',buffer:Buffer.from('## Imported section\n\n[config.yaml]('+url+')\n\n```bash\necho hello\n```\n\n<details><summary>Details</summary><p>Hidden body</p></details>')});
  await expect(page.locator('#block-editor [data-content-type=file]')).toHaveCount(1);
  await expect(page.locator('#block-editor [data-content-type=toggleListItem]')).toHaveCount(1);
  await expect.poll(async()=>JSON.stringify((await api('/notes/'+childPage.id)).blocks)).toContain('Imported section');
  await page.reload();await expect(page.locator('#block-editor [data-content-type=file]')).toHaveCount(1);
  const mdArchive=process.env.DRG_NOTION_MD_EXPORT;
  const markdownFiles=mdArchive?JSON.parse(execFileSync(python,['-c',`import sys,json,zipfile,base64
with zipfile.ZipFile(sys.argv[1]) as z:print(json.dumps({n:base64.b64encode(z.read(n)).decode() for n in z.namelist() if not n.endswith('/')}))`,mdArchive],{encoding:'utf8'})):null;
  const zipBuffer=mdArchive?await readFile(mdArchive):zip({['Files '+'e'.repeat(32)+'.md']:'# Files\n\n## Section\n\n[config.yaml](config.yaml)',['config.yaml']:'config bytes'});
  const beforeImport=(await api('/notes')).map(p=>p.id);
  await page.locator('#page-actions').click();
  const zipChooser=page.waitForEvent('filechooser');await page.locator('[data-page-action="import"]').click();
  await (await zipChooser).setFiles({name:'notion.zip',mimeType:'application/zip',buffer:zipBuffer});
  const form=page.locator('[data-notion-form]');await expect(form).toBeVisible();
  expect(await form.locator('input[type=file]').evaluate(el=>el.files.length)).toBe(1);
  await form.getByRole('button',{name:'Проверить архив',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Архив проверен'})).toBeVisible();
  await expect(form).toContainText('HTML-экспорт');
  await form.getByRole('button',{name:'Добавить в мои заметки',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Импорт завершён'})).toBeVisible();
  const added=(await api('/notes')).filter(p=>!beforeImport.includes(p.id));expect(added).toHaveLength(1);
  await page.getByRole('button',{name:'Открыть заметки',exact:true}).click();
  await page.goto('/page/'+added[0].id);
  await expect(page.locator('#block-editor [data-content-type=file]')).toHaveCount(mdArchive?4:1);
  if(markdownFiles){
    const mdName=Object.keys(markdownFiles).find(n=>n.endsWith('.md')),source=Buffer.from(markdownFiles[mdName],'base64').toString('utf8');
    await expect(page.locator('#block-editor [data-content-type=image]')).toHaveCount(3);
    const headings=source.split('\n').filter(line=>/^#{2,6} /.test(line)).map(line=>line.replace(/^#+ /,'').replaceAll('**',''));
    expect(await page.locator('#block-editor [data-content-type=heading] .bn-inline-content').allTextContents()).toEqual(headings);
    const code=[...source.matchAll(/^```[^\n]*\n([\s\S]*?)^```/gm)].map(m=>m[1].replace(/\n$/,''));
    expect(JSON.stringify(await page.locator('#block-editor [data-content-type=codeBlock] .bn-inline-content').allTextContents())===JSON.stringify(code),'all original code blocks preserved').toBe(true);
    await expect.poll(async()=>Array.isArray((await api('/notes/'+added[0].id)).blocks)).toBe(true);
    const stored=(await api('/notes/'+added[0].id));
    const textValidation=await build({stdin:{contents:`import {BlockNoteEditor} from '@blocknote/core';import {schema} from './web/modules/notes/blocks.js';const inline=value=>typeof value==='string'?value:Array.isArray(value)?value.map(inline).join(''):value?.type==='link'?inline(value.content):value?.text||'';const text=blocks=>blocks.map(b=>(['image','file'].includes(b.type)?b.props.name:inline(b.content))+' '+text(b.children||[])).join(' ');window.originalMarkdownText=source=>{const editor=BlockNoteEditor.create({schema});const result=text(editor.tryParseMarkdownToBlocks(source));editor._tiptapEditor.destroy();return result.replace(/\\s+/g,' ').trim();};window.importedMarkdownText=blocks=>text(blocks).replace(/\\s+/g,' ').trim();`,resolveDir:process.cwd()},bundle:true,write:false,format:'iife',target:'es2022',define:{'process.env.NODE_ENV':'"production"'},plugins:[{name:'omit-styles',setup(build){build.onLoad({filter:/\.css$/},()=>({contents:'',loader:'js'}));}}]});
    await page.route('**/assets/markdown-validation.js',route=>route.fulfill({contentType:'application/javascript',body:textValidation.outputFiles[0].text}));
    await page.addScriptTag({url:origin+'/assets/markdown-validation.js'});
    const fullTextPreserved=await page.evaluate(({source,blocks})=>window.originalMarkdownText(source.replace(/^# [^\n]*\n+/,''))===window.importedMarkdownText(blocks),{source,blocks:stored.blocks});
    expect(fullTextPreserved,'all original text preserved').toBe(true);
    const originals=new Map(Object.entries(markdownFiles).filter(([n])=>!n.endsWith('.md')).map(([name,encoded])=>[name.split('/').at(-1),encoded]));
    const downloadedNames=[];
    for(const block of stored.blocks.filter(b=>['file','image'].includes(b.type))){
      const response=await context.request.get(block.props.url);expect(response.ok()).toBe(true);
      const filename=decodeURIComponent(response.headers()['content-disposition'].split("filename*=UTF-8''")[1]);
      expect(originals.has(filename),'original attachment filename retained').toBe(true);
      expect(Buffer.compare(await response.body(),Buffer.from(originals.get(filename),'base64'))===0,'attachment bytes unchanged').toBe(true);
      downloadedNames.push(filename);
    }
    expect(downloadedNames.sort()).toEqual([...originals.keys()].sort());
    await page.reload();await expect(page.locator('#block-editor [data-content-type=file]')).toHaveCount(4);
    console.log('Real Markdown export: 7 byte-identical attachments, 4 file blocks, 3 images, '+headings.length+' sections and '+code.length+' code blocks verified.');
  }
  if(process.env.DRG_NOTION_EXPORTS){
    const uid=(await api('/me')).id;
    const result=execFileSync(python,['-c',`import sys,json,io,tempfile
from pathlib import Path
from app.main import db,DATA,now,PUBLIC_URL,storage_gate
from app.notion_import import NotionArchive,restore
from app.file_store import file_store
files=[Path(p).read_bytes() for p in json.loads(sys.argv[2])]
with tempfile.TemporaryDirectory() as directory:
 a=NotionArchive(io.BytesIO(b''.join(files)),directory,list(map(len,files)))
 with storage_gate.exclusive_sync():r=restore(a,db,file_store(db,DATA),sys.argv[1],now,PUBLIC_URL)
 print(json.dumps(r));a.close()`,uid,process.env.DRG_NOTION_EXPORTS],{env,encoding:'utf8',maxBuffer:1024**2});
    const imported=JSON.parse(result);expect(imported.notes).toBeGreaterThan(300);
    const all=await api('/notes'),types=new Set(),selected=[];
    for(const note of all){
      if(note.kind==='database')continue;
      const found=new Set();const walk=blocks=>{for(const b of blocks||[]){found.add(b.type);walk(b.children);}};walk(note.blocks);
      if([...found].some(type=>!types.has(type))){selected.push(note);for(const type of found)types.add(type);}
    }
    for(const note of selected){
      await page.goto('/page/'+note.id);
      try{await expect(page.locator('#block-editor .bn-editor')).toBeVisible();}
      catch{throw Error('Real export rendering failed: '+note.title+'; errors: '+JSON.stringify(errors));}
      expect((await api('/notes/'+note.id)).blocks.length).toBe(note.blocks.length);
    }
    console.log('Real exports rendered',selected.length,'representative pages; block types:',[...types].join(', '));
    const attachedDatabase=all.find(note=>note.kind==='database'&&note.blocks?.some(block=>block.type==='file'));
    if(attachedDatabase){await page.goto('/page/'+attachedDatabase.id);await expect(page.locator('#block-editor [data-content-type=file]').first()).toBeVisible();}
    // Validate every exported document against the actual production editor schema.
    const validation=await build({stdin:{contents:`import {BlockNoteEditor} from '@blocknote/core';import {schema} from './web/modules/notes/blocks.js';window.validateNotionDocuments=pages=>{const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?['text','content','children','rows','cells'].filter(key=>key in value).map(key=>text(value[key])).join(''):typeof value==='string'?value:'';return pages.filter(p=>p.blocks?.length).map(p=>{try{const editor=BlockNoteEditor.create({schema,initialContent:p.blocks});const equal=text(editor.document)===text(p.blocks);editor._tiptapEditor.destroy();return {id:p.id,ok:equal};}catch{return {id:p.id,ok:false};}});};`,resolveDir:process.cwd()},bundle:true,write:false,format:'iife',target:'es2022',define:{'process.env.NODE_ENV':'"production"'},plugins:[{name:'omit-styles',setup(build){build.onLoad({filter:/\.css$/},()=>({contents:'',loader:'js'}));}}]});
    await page.route('**/assets/notion-validation.js',route=>route.fulfill({contentType:'application/javascript',body:validation.outputFiles[0].text}));
    await page.addScriptTag({url:origin+'/assets/notion-validation.js'});
    const validationResult=await page.evaluate(pages=>window.validateNotionDocuments(pages),all);
    expect(validationResult.filter(result=>!result.ok)).toEqual([]);
    console.log('All real rich documents passed editor schema and text preservation:',validationResult.length);
  }
  const longTitle='Длинное название страницы с инструкцией по настройке и проверке переноса заметок '.repeat(2).trim();
  const longPage=await api('/notes','POST',{title:longTitle,blocks:[{id:'title-body',type:'paragraph',content:'Body'}]});
  await page.setViewportSize({width:390,height:844});await page.goto('/page/'+longPage.id);
  const titleField=page.locator('#page-title');await expect(titleField).toHaveValue(longTitle);
  const geometry=()=>titleField.evaluate(input=>({height:input.clientHeight,scrollHeight:input.scrollHeight,width:input.clientWidth,scrollWidth:input.scrollWidth}));
  const mobileTitle=await geometry();expect(mobileTitle.height).toBeGreaterThan(80);expect(mobileTitle.scrollHeight).toBeLessThanOrEqual(mobileTitle.height+1);expect(mobileTitle.scrollWidth).toBeLessThanOrEqual(mobileTitle.width+1);
  await titleField.fill(longTitle+'!');await expect.poll(async()=>(await api('/notes/'+longPage.id)).title).toBe(longTitle+'!');
  await page.setViewportSize({width:1280,height:900});await page.reload();await expect(titleField).toHaveValue(longTitle+'!');expect((await geometry()).height).toBeLessThan(mobileTitle.height);
  expect(errors).toEqual([]);
  console.log('Notion import UI passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
