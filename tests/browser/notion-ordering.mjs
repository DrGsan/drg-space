import {chromium,webkit,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';

const probe=createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));
const cwd=process.env.DRG_ORDER_WORKDIR||process.cwd(),dir=await mkdtemp(tmpdir()+'/drg73-order-'),origin='http://127.0.0.1:'+port;
const output=fileURLToPath(new URL('../../artifacts/drg-73/',import.meta.url)),before=process.env.DRG_ORDER_BEFORE==='1';
const server=spawn('.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:'+port,'tests.notion_sync_browser_app:app'],{cwd,env:{...process.env,DRG_NOTION_BROWSER_TEST:'1',DATA_DIR:dir,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-order-test-'.repeat(5),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,FAMILY_PHOTO_URL:'',CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',v=>logs=(logs+v).slice(-4000));
try{
 for(let i=0;i<150;i++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
 browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
 const context=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:1180,height:850}});let csrf='';
 const api=async(path,data,method='POST')=>{const r=await context.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 const password='Isolated-order-review-123';csrf=(await api('/setup',{login:'orderowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll',{password});let bits='';for(const c of enrollment.secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
 const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(v=>parseInt(v,2)))).update(counter).digest(),offset=hash[19]&15;
 await api('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
 await api('/appearance',{theme:'ocean',style:'glass',mode:'light',wallpaper:'mountains',seasonal:'off'},'PUT');
 const first=await api('/notes',{title:'Первая обычная заметка',body:'Новые подключения появляются после существующих заметок.'}),second=await api('/notes',{title:'Вторая обычная заметка',body:'Локальный порядок сохраняется после синхронизации.'});
 await new Promise(r=>setTimeout(r,1100));
 const fixture=await api('/browser/notion-fixture',{}),preview=await api('/account/notion-sync/preview',{connection_id:'fixture-connection',source:fixture.source});
 const status=async()=>(await api('/account/notion-sync',undefined,'GET')).roots.find(r=>r.id===preview.id);
 await expect.poll(async()=>(await status()).status,{timeout:30000}).toBe('ready');
 const ready=await status();await api('/account/notion-sync/roots/'+ready.id+'/confirm',{preview_hash:ready.preview_hash});
 await expect.poll(async()=>(await status()).status,{timeout:30000}).toBe('active');const root=await status();
 const catalog=async()=>api('/notes?catalog=true',undefined,'GET');
 const rootIDs=async()=>(await catalog()).filter(n=>!n.parent_id&&!n.space_id).map(n=>n.id);
 if(!before)expect(await rootIDs()).toEqual([first.id,second.id,root.note_id]);
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const settle=()=>page.evaluate(async()=>{await document.fonts.ready;await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));});
 await page.goto('/page/'+first.id);await expect(page.locator('[data-tree-row="'+root.note_id+'"]')).toBeVisible();await settle();
 await mkdir(output,{recursive:true});await page.screenshot({path:output+'order-'+(before?'before':'after')+'-creation.png'});
 if(!before){
  // Drop in the middle of the preceding row: a sync must reorder, not nest.
  const source=page.locator('[data-tree-row="'+root.note_id+'"]'),target=page.locator('[data-tree-row="'+first.id+'"]');
  await source.dragTo(target,{targetPosition:{x:90,y:(await target.boundingBox()).height*.45}});
  await expect.poll(rootIDs).toEqual([root.note_id,first.id,second.id]);
  await api('/browser/notion-update',{});
  await expect.poll(async()=>(await api('/notes/'+root.note_id,undefined,'GET')).body,{timeout:30000}).toContain('Обновлённый текст');
  await page.reload();await expect(source).toBeVisible();await settle();expect(await rootIDs()).toEqual([root.note_id,first.id,second.id]);
  const saved=await api('/notes/'+root.note_id,undefined,'GET');expect(saved.parent_id).toBe(null);expect(saved.notion_sync.read_only).toBe(true);
  await page.screenshot({path:output+'order-after-drag.png'});
  await page.goto('/page/'+root.note_id);await page.locator('#page-actions').click();await expect(page.locator('[data-page-action=move-down]')).toBeVisible();await page.locator('[data-page-action=move-down]').click();
  await expect.poll(rootIDs).toEqual([first.id,root.note_id,second.id]);
 }
 expect(errors).toEqual([]);console.log('Notion order passed: '+(before?'baseline':browser.browserType().name()));
}finally{
 await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(dir,{recursive:true,force:true});
}
