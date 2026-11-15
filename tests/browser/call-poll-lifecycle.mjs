// Exercise the actual controller with deterministic time and transport faults.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {readFile} from 'node:fs/promises';
import {chromium,webkit} from 'playwright';
const source=await readFile('web/modules/chat/calls.js','utf8');
const bundle=await build({stdin:{contents:source+'\nexport {VoiceCalls};\nexport {registerInboxConnection} from "../../core/inbox-connection.js";',resolveDir:process.cwd()+'/web/modules/chat',loader:'js'},bundle:true,format:'iife',globalName:'CallTest',write:false,loader:{'.css':'empty'}});
const browser=await(process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
try{
 const page=await browser.newPage();await page.route('http://127.0.0.1:8002/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8002/');await page.clock.install();await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{
  window.s={hidden:false,live:true,paths:[],call:null,held:false,pending:[],draws:0};Object.defineProperty(document,'hidden',{get:()=>s.hidden,configurable:true});
  CallTest.VoiceCalls.prototype.draw=function(){s.draws++;};CallTest.VoiceCalls.prototype.tick=()=>{};
  window.ctx={state:{me:{id:'synthetic-user',settings:{}}},moduleEnabled:()=>true,toast(){},async api(path){s.paths.push(path);if(s.held)await new Promise(resolve=>s.pending.push(resolve));return path==='/calls/config'?{available:true}:path.startsWith('/calls/current')?{call:structuredClone(s.call)}:{};}};
  window.unregister=CallTest.registerInboxConnection(ctx.state.me.id,()=>s.live);window.v=new CallTest.VoiceCalls(ctx);
  window.count=()=>s.paths.filter(p=>p.startsWith('/calls/current')).length;
  window.visible=hidden=>{s.hidden=hidden;document.dispatchEvent(new Event('visibilitychange'));};
 });
 await page.clock.runFor(45100);let n=await page.evaluate(()=>count());assert(n<=4&&n>=2,'proven live inbox bounds idle polling: '+n);
 assert.equal(await page.evaluate(()=>s.paths.filter(p=>p==='/calls/config').length),1,'availability refreshes are shared for one minute');
 // Incoming event bypasses the idle deadline, including a request in flight.
 await page.evaluate(()=>{s.call={id:'incoming-call-123456',status:'ringing',outgoing:false,owned:false};document.dispatchEvent(new Event('drg-incoming-call'));});await page.clock.runFor(100);
 assert.equal(await page.evaluate(()=>v.call?.id),'incoming-call-123456','incoming appears immediately without waiting for periodic fallback');
 const active=await page.evaluate(()=>count());await page.clock.runFor(6100);assert(await page.evaluate(()=>count())>=active+3,'live calls retain two-second signaling');
 await page.evaluate(()=>{s.call=null;v.close();s.live=false;});const fallback=await page.evaluate(()=>count());await page.clock.runFor(6100);assert(await page.evaluate(()=>count())>=fallback+3,'offline/old/stale inbox keeps fast fallback');
 await page.evaluate(()=>{s.live=true;visible(true);});const hidden=await page.evaluate(()=>count());await page.clock.runFor(30000);assert.equal(await page.evaluate(()=>count()),hidden,'hidden idle pages do not poll');
 await page.evaluate(()=>{visible(false);});await page.clock.runFor(100);assert.equal(await page.evaluate(()=>count()),hidden+1,'foreground refreshes immediately');
 await page.evaluate(()=>{s.held=true;v.poll(true);});await page.clock.runFor(100);const held=await page.evaluate(()=>count());
 await page.evaluate(()=>{s.call={id:'queued-incoming-call',status:'ringing',outgoing:false,owned:false};for(let i=0;i<12;i++)document.dispatchEvent(new Event('drg-incoming-call'));s.held=false;s.pending.splice(0).forEach(resolve=>resolve());});await page.clock.runFor(200);
 assert.equal(await page.evaluate(()=>v.call?.id),'queued-incoming-call');assert(await page.evaluate(()=>count())<=held+1,'event burst coalesces one follow-up');
 await page.evaluate(()=>{s.call=null;v.close();unregister();v.destroy();});const ended=await page.evaluate(()=>s.paths.length);await page.evaluate(()=>{visible(false);document.dispatchEvent(new Event('drg-incoming-call'));});await page.clock.runFor(45000);assert.equal(await page.evaluate(()=>s.paths.length),ended,'logout removes intervals/listeners and ignores late work');
 console.log('Call polling passed: idle <=4/45s, config1, immediate inbound, active/offline2s, hidden0, resume, coalescing and logout');
}finally{await browser.close();}
