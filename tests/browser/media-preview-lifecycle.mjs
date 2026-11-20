// Real preview loader: bounded requests, empty catalogs and lifecycle races.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,
  format:'iife',globalName:'PreviewTest',write:false,loader:{'.css':'empty'}});
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
try{
 const page=await browser.newPage();
 await page.route('http://preview.test/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));
 await page.goto('http://preview.test/');await page.clock.install();
 await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{
  window.state={hidden:false,groups:Array.from({length:50},(_,i)=>({id:String(i),title:'Group '+i,latest_message_id:i<2?10:0,unread:i,can_send:true})),paths:[],active:0,peak:0,aborted:0,changes:[],hold:false,pending:[]};
  Object.defineProperty(document,'hidden',{configurable:true,get:()=>state.hidden});
  window.owner=new AbortController();
  window.ctx={moduleEnabled:()=>true,async api(path,_method,_body,_retry,_headers,{signal}={}){
   state.paths.push(path);state.active++;state.peak=Math.max(state.peak,state.active);
   try{
    if(state.hold)await new Promise((resolve,reject)=>{
     const end=()=>{signal?.removeEventListener('abort',cancel);resolve();};
     const cancel=()=>{state.aborted++;reject(new DOMException('Aborted','AbortError'));};
     signal?.addEventListener('abort',cancel,{once:true});state.pending.push(end);
     if(signal?.aborted)cancel();
    });
    if(path.split('?')[0]==='/family-photos/groups'){
     if(state.catalogError)throw Object.assign(new Error('Catalog request failed'),{status:state.catalogError});
     return {items:structuredClone(state.groups)};
    }
    const id=path.split('/')[3],group=state.groups.find(g=>g.id===id);
    if(group.denied)throw Object.assign(new Error('Denied'),{status:403});
    return {items:group.latest_message_id===0?[]:[{id:group.latest_message_id||10,created:1700000000,sender:'Synthetic',text:'Message',media:[]}]};
   }finally{state.active--;}
  }};
  window.start=async()=>{window.watcher=await PreviewTest.watchTelegramRooms(ctx,items=>{state.changes.push(structuredClone(items));},{signal:owner.signal});};
  window.visible=hidden=>{state.hidden=hidden;document.dispatchEvent(new Event('visibilitychange'));};
 });
 await page.evaluate(()=>start());
 assert.equal(await page.evaluate(()=>state.paths.length),3,'50 groups / 48 empty require catalog and two previews');
 assert.equal(await page.evaluate(()=>state.changes.at(-1).length),50,'empty rooms remain visible');
 await page.clock.runFor(15100);
 assert.equal(await page.evaluate(()=>state.paths.length),6,'each visible round skips all known empty histories');
 await page.evaluate(()=>{state.groups[2].latest_message_id=11;state.groups[0].latest_message_id=0;visible(true);});
 const before=await page.evaluate(()=>state.paths.length);await page.clock.runFor(45000);
 assert.equal(await page.evaluate(()=>state.paths.length),before,'hidden loader makes no requests');
 await page.evaluate(()=>{visible(false);visible(false);visible(false);});await page.clock.runFor(100);
 assert.equal(await page.evaluate(()=>state.paths.length),before+3,'burst of resume events starts exactly one round');
 assert.equal(await page.evaluate(()=>state.changes.at(-1).find(x=>x.group_id==='0').last_message),null,'empty catalog clears a stale preview');
 assert.equal(await page.evaluate(()=>state.changes.at(-1).find(x=>x.group_id==='2').last_message.id),11,'first message after empty appears');
 await page.evaluate(()=>{state.groups.forEach(g=>g.latest_message_id=10);state.hold=true;});
 await page.clock.runFor(15100); // held catalog
 await page.evaluate(()=>state.pending.shift()());await page.clock.runFor(100);
 assert.equal(await page.evaluate(()=>state.active),4,'only four history requests may be in flight');
 const held=await page.evaluate(()=>state.paths.length),changes=await page.evaluate(()=>state.changes.length);
 await page.evaluate(()=>visible(true));await page.clock.runFor(100);
 assert.equal(await page.evaluate(()=>state.active),0,'hiding aborts the active preview requests');
 assert.equal(await page.evaluate(()=>state.paths.length),held,'cancellation does not launch the rest of the queue');
 assert.equal(await page.evaluate(()=>state.changes.length),changes,'aborted round cannot publish partial rooms');
 await page.evaluate(()=>{state.hold=false;state.groups=state.groups.slice(0,6);delete state.groups[0].latest_message_id;state.groups[1].latest_message_id='0';state.groups[2].latest_message_id=false;state.groups[3].denied=true;visible(false);});
 await page.clock.runFor(100);
 assert.equal(await page.evaluate(()=>state.paths.length),held+7,'missing/malformed latest fields keep legacy history reads');
 assert.equal(await page.evaluate(()=>state.changes.at(-1).some(x=>x.group_id==='3')),false,'denied preview is not exposed');
 assert.equal(await page.evaluate(()=>state.changes.at(-1).length),5,'removed catalog groups are not retained');
 await page.evaluate(()=>{state.hold=true;visible(false);});await page.clock.runFor(100);
 const rapidChanges=await page.evaluate(()=>state.changes.length);
 await page.evaluate(()=>{visible(true);visible(false);visible(false);state.hold=false;state.pending.splice(0).forEach(done=>done());});
 await page.clock.runFor(100);
 assert.equal(await page.evaluate(()=>state.changes.length),rapidChanges+1,'rapid hide/resume publishes only one fresh round');
 await page.evaluate(()=>{state.catalogError=503;visible(false);});await page.clock.runFor(100);
 assert.equal(await page.evaluate(()=>state.changes.length),rapidChanges+1,'temporary failure keeps already visible family rooms');
 await page.evaluate(()=>{state.catalogError=403;visible(false);});await page.clock.runFor(100);
 assert.deepEqual(await page.evaluate(()=>state.changes.at(-1)),[],'revoked access drops previously visible rooms');
 await page.evaluate(()=>{state.catalogError=null;});
 await page.evaluate(()=>{state.hold=true;visible(false);});await page.clock.runFor(100);
 await page.evaluate(()=>{owner.abort();state.pending.splice(0).forEach(done=>done());});await page.clock.runFor(100);
 const ended=await page.evaluate(()=>state.paths.length);await page.evaluate(()=>visible(false));await page.clock.runFor(45000);
 assert.equal(await page.evaluate(()=>state.paths.length),ended,'destroyed owner removes timers and visibility listener');
 // Abort before the first catalog resolves, before the watcher is assigned.
 await page.evaluate(()=>{owner=new AbortController();state.hold=true;window.first=start();});await page.clock.runFor(100);
 await page.evaluate(()=>owner.abort());await page.clock.runFor(100);await page.evaluate(()=>first);
 assert.equal(await page.evaluate(()=>state.active),0,'parent teardown cancels the initial catalog too');
 const final=await page.evaluate(()=>state.paths.length);await page.evaluate(()=>visible(false));await page.clock.runFor(45000);
 assert.equal(await page.evaluate(()=>state.paths.length),final,'initial teardown cannot resurrect a watcher');
 console.log('Media preview lifecycle: 50/2/48 budget, hidden/resume, concurrency, cancellation, empty transitions, legacy and denial passed');
}finally{await browser.close();}
