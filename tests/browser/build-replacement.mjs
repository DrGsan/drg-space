import assert from 'node:assert/strict';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';

const origin='http://127.0.0.1:8049',results=[];
for(const [name,engine] of [['chromium',chromium],['webkit',webkit]]){
 const directory=await mkdtemp(tmpdir()+'/drg-public-builds-');
 const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','tests.browser_public_builds',directory],{stdio:['ignore','ignore','pipe']});let stderr='',browser;
 server.stderr.on('data',v=>stderr=(stderr+v).slice(-3000));
 try{
  let ready=false;for(let n=0;n<100;n++){try{if((await fetch(origin)).ok){ready=true;break}}catch{}if(server.exitCode!==null)throw Error(stderr);await new Promise(r=>setTimeout(r,100))}assert(ready,stderr);
  browser=await engine.launch({headless:true});const page=await browser.newPage(),lazy=[];
  page.on('response',r=>{if(/lazy-|shared-/.test(r.url()))lazy.push({path:new URL(r.url()).pathname,status:r.status()})});
  await page.goto(origin);await page.waitForFunction(()=>window.version==='A');assert.equal(lazy.length,0,'lazy graph must not be fetched before replacement');
  assert.equal((await page.request.post(origin+'/replace')).status(),204);
  const fresh=await browser.newPage();await fresh.goto(origin);await fresh.waitForFunction(()=>window.version==='B');
  await page.getByRole('button',{name:'Open lazy feature'}).click();await page.waitForFunction(()=>document.querySelector('output').textContent==='loaded-A');
  assert.deepEqual(lazy.map(x=>x.status),[200,200]);
  await fresh.getByRole('button',{name:'Open lazy feature'}).click();await fresh.waitForFunction(()=>document.querySelector('output').textContent==='loaded-B');
  const asset=await page.request.get(origin+'/assets/lazy-A.js');assert.equal(asset.status(),200);assert.match(asset.headers()['content-type'],/javascript/);assert.match(asset.headers()['cache-control'],/immutable/);
  assert.equal((await page.request.get(origin+'/assets/../index.json')).status(),404);
  results.push({browser:name,oldOpenPageLoadedOriginalGraph:true,newPageLoadedNewGraph:true,lazy});
 }finally{
  await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(directory,{recursive:true,force:true});
 }
}
await mkdir('artifacts/site-85/livekit',{recursive:true});await writeFile('artifacts/site-85/livekit/build-replacement.json',JSON.stringify(results,null,2)+'\n');console.log('Open-page deployment regression passed in Chromium and WebKit.');
