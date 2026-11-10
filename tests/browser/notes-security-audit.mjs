// Isolated adversarial rendering tests. No accounts, production requests,
// persistent profiles, screenshots or generated files are used.
import {build} from 'esbuild';
import {createServer} from 'node:http';
import {chromium,expect} from '@playwright/test';

const source="export {mountDatabase} from './web/modules/notes/database.js'; export {validateDatabasePayload} from './web/modules/federation/database-protocol.js'; export {mountBlockEditor,pageBlocks} from './web/modules/notes/blocks.js'; export {saveNoteRecovery,loadNoteRecovery} from './web/core/note-recovery.js'; export {md} from './web/core/ui.js';";
const bundle=await build({stdin:{contents:source,resolveDir:process.cwd()},bundle:true,write:false,format:'esm',external:['/assets/mermaid.js'],loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'}});
const policy="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; media-src 'self' https: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";
const requests=[];let parserAudit=false;
const server=createServer((request,response)=>{
  requests.push(request.url);response.setHeader('Content-Security-Policy',parserAudit?policy.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'"):policy);
  if(request.url==='/audit.js'){response.setHeader('Content-Type','text/javascript');response.end(bundle.outputFiles[0].text);}
  else if(request.url==='/'){response.setHeader('Content-Type','text/html');response.end('<!DOCTYPE html><div id="audit-host"></div>');}
  else{response.statusCode=404;response.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
try{
  browser=await chromium.launch({headless:true});const page=await browser.newPage({locale:'ru-RU'});
  await page.goto('http://127.0.0.1:'+server.address().port);
  const initialize=()=>page.evaluate(async()=>{window.audit=await import('/audit.js');window.auditExecuted=0;window.auditViolations=[];document.addEventListener('securitypolicyviolation',event=>window.auditViolations.push(event.violatedDirective));});await initialize();
  // Reproduce the original dangerous path and require defense at both layers:
  // reject a hostile peer's signed shape; escape it even in the native sink.
  await page.evaluate(()=>{
    const db={note_id:'D'.repeat(16),title:'Audit database',version:1,note_version:1,role:'editor',properties:[],views:[{id:'table',name:'Table',type:'table',lag_days:'0"><img src="/injected-pixel" data-audit-injected onerror="window.auditExecuted=1">'}],records:[]};
    let rejected=false;try{window.audit.validateDatabasePayload(db);}catch{rejected=true;}if(!rejected)throw Error('Hostile renderer data accepted.');
    window.auditHandle=window.audit.mountDatabase(document.querySelector('#audit-host'),{id:db.note_id,role:'editor'},{state:{notes:[],dirty:false},secret:true,api:async()=>structuredClone(db),toast:()=>{},modal:html=>{const dialog=document.createElement('div');dialog.dataset.auditDialog='';dialog.innerHTML=html;document.body.append(dialog);return dialog;}},async()=>{});
  });
  await page.locator('#db-view-config').click();await expect(page.locator('#db-lag-days')).toBeVisible();await expect(page.locator('[data-audit-injected]')).toHaveCount(0);
  await page.evaluate(()=>{window.auditHandle.destroy();document.querySelector('[data-audit-dialog]').remove();document.querySelector('#audit-host').replaceChildren();});
  console.log('PASS: hostile signed view fields rejected; native numeric attribute remains inert even without the remote validator.');

  // Deliberately permit inline scripts ONLY in this empty synthetic fixture so
  // CSP cannot hide a parser/rendering regression. No real accounts are used.
  parserAudit=true;await page.reload();await initialize();
  const attacks=[
    '<img src="/missing-image" onerror="window.auditExecuted=1">',
    '<svg onload="window.auditExecuted=1"><script>window.auditExecuted=1</script></svg>',
    '<iframe srcdoc="<script>parent.auditExecuted=1</script>"></iframe><object data="javascript:window.auditExecuted=1"></object>',
    '<a href="java&#10;script:window.auditExecuted=1">unsafe link</a>',
    '<a href="data:text/html,<script>opener.auditExecuted=1</script>">unsafe data</a>',
    '<details open><summary><img src="/missing-toggle" onerror="window.auditExecuted=1">Title</summary><script>window.auditExecuted=1</script>Body</details>',
    '<math><mtext><table><mglyph><style><!--</style><img src="/missing-mutation" onerror="window.auditExecuted=1">',
    '<span data-equation="\\href{javascript:window.auditExecuted=1}{X}">math</span>',
  ];
  for(const body of attacks){
    await page.evaluate(async body=>{const host=document.querySelector('#audit-host');window.auditHandle=window.audit.mountBlockEditor(host,{id:'P'.repeat(16),title:'Audit',blocks:null,body},{readOnly:true,secret:true});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));},body);
    await expect(page.locator('#audit-host script,#audit-host iframe,#audit-host object,#audit-host embed,#audit-host [onerror],#audit-host [onload]')).toHaveCount(0);
    expect(await page.locator('#audit-host a[href]').evaluateAll(links=>links.some(link=>/^(javascript|vbscript|data):/i.test(link.href)))).toBe(false);
    await page.evaluate(()=>{window.auditHandle.destroy();document.querySelector('#audit-host').replaceChildren();});
    await page.evaluate(body=>document.querySelector('#audit-host').innerHTML=window.audit.md(body),body);
    await expect(page.locator('#audit-host script,#audit-host iframe,#audit-host object,#audit-host embed,#audit-host [onerror],#audit-host [onload]')).toHaveCount(0);
    expect(await page.locator('#audit-host a[href]').evaluateAll(links=>links.some(link=>/^(javascript|vbscript|data):/i.test(link.href)))).toBe(false);
    await page.evaluate(()=>document.querySelector('#audit-host').replaceChildren());
  }
  await page.evaluate(async()=>{const hostile='<img src=/missing onerror="window.auditExecuted=1">';window.auditHandle=window.audit.mountBlockEditor(document.querySelector('#audit-host'),{id:'P'.repeat(16),title:'Audit',blocks:[{id:'text',type:'paragraph',content:[{type:'text',text:hostile,styles:{}},{type:'link',href:'javascript:window.auditExecuted=1',content:[{type:'text',text:'unsafe',styles:{}}]},{type:'equation',props:{latex:'\\href{javascript:window.auditExecuted=1}{X}'}}]},{id:'file',type:'file',props:{name:hostile,url:'https://example.invalid/file',caption:hostile}},{id:'callout',type:'callout',props:{icon:hostile},content:hostile}]},{readOnly:true,secret:true});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
  await expect(page.locator('#audit-host')).toContainText('<img');await expect(page.locator('#audit-host [onerror],#audit-host [onload],#audit-host script,#audit-host iframe')).toHaveCount(0);expect(await page.locator('#audit-host a[href]').evaluateAll(links=>links.some(link=>/^(javascript|vbscript|data):/i.test(link.href)))).toBe(false);
  expect(await page.evaluate(()=>window.auditExecuted)).toBe(0);expect(await page.evaluate(()=>window.auditViolations)).toEqual([]);
  await page.evaluate(()=>window.auditHandle.destroy());
  console.log('PASS: adversarial public Markdown/HTML, rich toggle import, link schemes, block text/file captions and untrusted LaTeX create no active content or attempted inline execution.');

  const storage=await page.evaluate(async()=>{
    const a='a'.repeat(32),b='b'.repeat(32),id='N'.repeat(16),ctx={state:{me:{id:a}}},other={state:{me:{id:b}}},snapshot={title:'Recovery',body:'PRIVATE_RECOVERY_AUDIT',blocks:[]};
    await window.audit.saveNoteRecovery(ctx,id,snapshot);const own=await window.audit.loadNoteRecovery(ctx,id),foreign=await window.audit.loadNoteRecovery(other,id);
    const db=await new Promise((resolve,reject)=>{const request=indexedDB.open('drg-note-recovery');request.onsuccess=()=>resolve(request.result);request.onerror=reject;});
    const read=store=>new Promise((resolve,reject)=>{const request=db.transaction(store).objectStore(store).getAll();request.onsuccess=()=>resolve(request.result);request.onerror=reject;});
    const rows=await read('drafts'),keys=await read('keys'),encrypted=rows.every(row=>row.body instanceof ArrayBuffer&&!new TextDecoder().decode(row.body).includes(snapshot.body)),nonextractable=keys.every(row=>row.key.extractable===false);
    const damaged={...rows[0],body:rows[0].body.slice(0)};new Uint8Array(damaged.body)[0]^=1;await new Promise((resolve,reject)=>{const tx=db.transaction('drafts','readwrite');tx.objectStore('drafts').put(damaged);tx.oncomplete=resolve;tx.onerror=reject;});let tamperRejected=false;try{await window.audit.loadNoteRecovery(ctx,id);}catch{tamperRejected=true;}
    await window.audit.saveNoteRecovery(ctx,id,snapshot);
    const original=crypto.subtle.decrypt.bind(crypto.subtle);let release,started;const gate=new Promise(resolve=>release=resolve),ready=new Promise(resolve=>started=resolve);crypto.subtle.decrypt=async(...args)=>{started();await gate;return original(...args);};
    const pending=window.audit.loadNoteRecovery(ctx,id);await ready;ctx.state.me.id=b;release();let accountSwitchRejected=false;try{await pending;}catch{accountSwitchRejected=true;}crypto.subtle.decrypt=original;
    return {ownMatches:own.body===snapshot.body,foreignIsNull:foreign===null,encrypted,nonextractable,tamperRejected,accountSwitchRejected};
  });
  expect(Object.values(storage).every(Boolean),JSON.stringify(storage)).toBe(true);
  expect(requests.includes('/injected-pixel')).toBe(false);
  console.log('PASS: recovery uses nonextractable keys and ciphertext, isolates account namespaces, rejects tampering and rejects an account switch during decryption.');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
