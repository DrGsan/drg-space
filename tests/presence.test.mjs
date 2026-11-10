import {test} from 'node:test';
import assert from 'node:assert/strict';
globalThis.document??={addEventListener(){}};
const {startPresence}=await import('../web/core/presence.js');
const {presenceText}=await import('../web/core/people.js');
function fixture(){let stamp=0,tick;const calls=[];const doc=new EventTarget();doc.hidden=false;doc.querySelectorAll=()=>[];const events=new EventTarget();const state={spaces:[{members:[{id:'friend',login:'Друг'}]}]};let rejectWrite=false,waitRead;const ctx={state,async api(path,method='GET'){calls.push([path,method]);if(method==='POST'){if(rejectWrite)throw new Error('429');return {};}if(waitRead)return waitRead;return {users:[{id:'friend',login:'Друг',online:true,last_seen:Math.floor(Date.now()/1000)}]};}};const controller=startPresence(()=>ctx,{doc,events,now:()=>stamp,every(fn){tick=fn;return 1;},cancel(){}});return {controller,doc,events,state,calls,tick:()=>tick(),advance(ms){stamp+=ms;},failWrite(){rejectWrite=true;},holdRead(promise){waitRead=promise;}};}
test('foreground polling refreshes contacts even when heartbeat writes fail and coalesces focus bursts',async()=>{const f=fixture();await f.controller.refresh();await new Promise(r=>setImmediate(r));f.calls.length=0;f.failWrite();f.advance(30000);await Promise.all([f.controller.refresh(),f.controller.refresh(),f.controller.refresh()]);assert.deepEqual(f.calls,[['/presence','POST'],['/presence','GET']]);assert.equal(f.state.spaces[0].members[0].online,true);f.controller.destroy();});
test('hidden pages do not keep users online and returning foreground refreshes immediately',async()=>{const f=fixture();await new Promise(r=>setImmediate(r));f.calls.length=0;f.doc.hidden=true;f.advance(60000);await f.tick();assert.equal(f.calls.length,0);f.doc.hidden=false;f.events.dispatchEvent(new Event('focus'));await new Promise(r=>setImmediate(r));assert.equal(f.calls[0][1],'POST');f.controller.destroy();});
test('a response after logout cannot change another account state',async()=>{const f=fixture();await new Promise(r=>setImmediate(r));let resolve;f.holdRead(new Promise(r=>resolve=r));const pending=f.controller.refresh();f.controller.destroy();f.state.spaces[0].members[0].online=false;resolve({users:[{id:'friend',online:true}]});await pending;assert.equal(f.state.spaces[0].members[0].online,false);});
test('online expires instead of remaining stuck in stale profile snapshots',()=>{assert.equal(presenceText({online:true,last_seen:1000},1050),'В сети');assert.notEqual(presenceText({online:true,last_seen:1000},1091),'В сети');});
test('admin labels show a precise local date, online status or explicitly missing activity',()=>{
  const stamp=new Date(2026,9,10,14,35).getTime()/1000;
  assert.match(presenceText({online:false,last_seen:stamp},stamp+10*86400,{precise:true}),/^Был в сети 10\.10\.2026,? 14:35$/);
  assert.equal(presenceText({online:true,last_seen:stamp},stamp+30,{precise:true}),'В сети');
  assert.equal(presenceText({},stamp,{precise:true}),'Нет данных об активности');
});
test('admin polls the full history endpoint and rejects responses after leaving that scope',async()=>{
  const f=fixture();await new Promise(r=>setImmediate(r));f.state.view='admin';f.state.me={role:'owner'};f.calls.length=0;
  await f.controller.refresh();assert.deepEqual(f.calls,[['/presence?scope=admin','GET']]);
  let resolve;f.holdRead(new Promise(r=>resolve=r));const pending=f.controller.refresh();f.state.view='home';
  f.state.spaces[0].members[0].online=false;resolve({users:[{id:'friend',online:true,last_seen:Math.floor(Date.now()/1000)}]});
  await pending;assert.equal(f.state.spaces[0].members[0].online,false);f.controller.destroy();
});

test('nickname repaint updates known avatars and preserves temporary or unavailable profile labels',async()=>{
  const {setPeopleNames,rememberPeople}=await import('../web/core/person-names.js');
  const f=fixture();await new Promise(resolve=>setImmediate(resolve));
  const make=(id,label)=>({dataset:{personAvatar:id},label,initial:{textContent:'?'},setAttribute(key,value){if(key==='aria-label')this.label=value;},querySelector(){return this.initial;}});
  const known=make('friend','Аватар Алёна'),temporary=make('','Аватар ?'),guest=make('unknown-profile','Аватар Гость');
  const query=f.doc.querySelectorAll.bind(f.doc);f.doc.querySelectorAll=selector=>selector==='[data-person-avatar]'?[known,temporary,guest]:query(selector);
  setPeopleNames('viewer',{friend:'Мама'});rememberPeople([{id:'friend',login:'alena',display_name:'Алёна'}]);
  f.controller.paint();
  assert.equal(known.label,'Аватар Мама');assert.equal(known.initial.textContent,'М');
  assert.equal(temporary.label,'Аватар ?');assert.equal(guest.label,'Аватар Гость');
  f.controller.destroy();setPeopleNames(null,{});
});
