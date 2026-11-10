import {test} from 'node:test';
import assert from 'node:assert/strict';
import {CallAudioOutput,phoneOutputRole} from '../web/modules/chat/audio-output.js';
function fixture(){const calls=[];const audio={sinkId:'',async setSinkId(id){calls.push(id);this.sinkId=id;}};let items=[{kind:'audiooutput',deviceId:'speaker',label:'iPhone Speaker'},{kind:'audiooutput',deviceId:'receiver',label:'iPhone Receiver'},{kind:'audioinput',deviceId:'mic',label:'Microphone'}];const devices={async enumerateDevices(){return items;},async selectAudioOutput(){calls.push('picker');return {deviceId:'headset',label:'AirPods'};}};return {calls,audio,devices,controller:new CallAudioOutput(audio,devices),setItems(value){items=value;}};}
test('an actual output switch uses the returned device ID without changing the microphone',async()=>{const f=fixture();await f.controller.refresh();assert.equal(f.controller.items.length,3);await f.controller.select('speaker');assert.equal(f.audio.sinkId,'speaker');await f.controller.select('receiver');assert.equal(f.audio.sinkId,'receiver');await assert.rejects(f.controller.select('invented-device'));assert.deepEqual(f.calls,['speaker','receiver']);});
test('native picker authorizes a new device and device removal restores the system route',async()=>{const f=fixture();await f.controller.refresh();await f.controller.choose();assert.equal(f.audio.sinkId,'headset');assert.equal(f.calls[0],'picker');f.setItems([]);await f.controller.refresh();assert.equal(f.audio.sinkId,'');});
test('unsupported APIs and rejected output permissions never report a selected speaker',async()=>{const c=new CallAudioOutput({},{});await c.refresh();assert.equal(c.supported,false);await assert.rejects(c.select('speaker'),/Браузер/);const f=fixture();await f.controller.refresh();f.audio.setSinkId=async()=>{throw new DOMException('permission','NotAllowedError');};await assert.rejects(f.controller.select('speaker'),{name:'NotAllowedError'});assert.equal(f.controller.current,'');});
test('an output selection completed after hangup is returned to the system route',async()=>{const f=fixture();await f.controller.refresh();let resolve;f.audio.setSinkId=id=>id==='speaker'?new Promise(r=>{resolve=()=>{f.audio.sinkId=id;r();};}):Promise.resolve().then(()=>{f.audio.sinkId=id;});const pending=f.controller.select('speaker');f.controller.reset();resolve();assert.equal(await pending,false);assert.equal(f.audio.sinkId,'');});

test('the browser-provided default speaker is kept as a selectable ID',async()=>{const f=fixture();f.setItems([{kind:'audiooutput',deviceId:'default',label:'iPhone Speaker'}]);await f.controller.refresh();assert.deepEqual(f.controller.items.map(d=>d.deviceId),['','default']);await f.controller.select('default');assert.equal(f.audio.sinkId,'default');});

test('rapid selections finish on the last requested output, with no overlapping native switches',async()=>{
 const f=fixture();await f.controller.refresh();const pending=new Map();
 f.audio.setSinkId=id=>new Promise(resolve=>pending.set(id,()=>{f.audio.sinkId=id;resolve();}));
 const first=f.controller.select('speaker'),last=f.controller.select('receiver');
 assert.equal(f.controller.busy,true);assert.equal(pending.has('receiver'),false);
 pending.get('speaker')();assert.equal(await first,false);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(pending.has('receiver'),true);
 pending.get('receiver')();assert.equal(await last,true);assert.equal(f.controller.current,'receiver');assert.equal(f.controller.busy,false);
});
test('every room player and a newly joined participant receive the chosen output',async()=>{
 const f=fixture(),player=()=>({sinkId:'',async setSinkId(id){this.sinkId=id;}}),second=player(),joined=player();
 f.controller.add(second);await f.controller.refresh();await f.controller.select('speaker');
 assert.equal(second.sinkId,'speaker');f.controller.add(joined);assert.equal(f.controller.current,null);
 await f.controller.prepare(joined);assert.equal(joined.sinkId,'speaker');assert.equal(f.controller.current,'speaker');
 f.controller.remove(joined);assert.equal(f.controller.diagnostics().players,2);
});
test('a failed room output is reported and successful players are rolled back',async()=>{
 const f=fixture();await f.controller.refresh();await f.controller.select('speaker');
 const second={sinkId:'speaker',async setSinkId(id){if(id==='receiver')throw new DOMException('denied','NotAllowedError');this.sinkId=id;}};
 f.controller.add(second);await assert.rejects(f.controller.select('receiver'),{name:'NotAllowedError'});
 assert.equal(f.audio.sinkId,'speaker');assert.equal(second.sinkId,'speaker');assert.equal(f.controller.current,'speaker');assert.ok(f.controller.error);
});
test('a browser resolving without changing sinkId never reports success',async()=>{
 const f=fixture();await f.controller.refresh();f.audio.setSinkId=async()=>{};
 await assert.rejects(f.controller.select('speaker'));assert.equal(f.controller.current,'');assert.ok(f.controller.error);
});
test('an older enumeration cannot overwrite a newer device list',async()=>{
 const f=fixture(),pending=[];f.devices.enumerateDevices=()=>new Promise(resolve=>pending.push(resolve));
 const first=f.controller.refresh(),last=f.controller.refresh();
 pending[1]([{kind:'audiooutput',deviceId:'receiver',label:'Latest'}]);await last;
 pending[0]([{kind:'audiooutput',deviceId:'speaker',label:'Old'}]);await first;
 assert.deepEqual(f.controller.items.map(x=>x.deviceId),['','receiver']);
});
test('a stale native chooser and cancelled chooser leave the current route intact',async()=>{
 const f=fixture();await f.controller.refresh();let choose;
 f.devices.selectAudioOutput=()=>new Promise(resolve=>{choose=resolve;});const pending=f.controller.choose();
 await f.controller.select('receiver');choose({deviceId:'headset',label:'AirPods'});
 assert.equal(await pending,false);assert.equal(f.controller.current,'receiver');
 f.devices.selectAudioOutput=async()=>{throw new DOMException('cancelled','AbortError');};assert.equal(await f.controller.choose(),false);assert.equal(f.controller.error,'');
});
test('teardown cannot clear an output selected by a subsequent call',async()=>{
 const f=fixture();await f.controller.refresh();let finish;
 f.audio.setSinkId=id=>id==='speaker'?new Promise(resolve=>{finish=()=>{f.audio.sinkId=id;resolve();};}):Promise.resolve().then(()=>{f.audio.sinkId=id;});
 const abandoned=f.controller.select('speaker');f.controller.reset();await f.controller.refresh();const next=f.controller.select('receiver');
 finish();assert.equal(await abandoned,false);assert.equal(await next,true);assert.equal(f.controller.current,'receiver');
});
test('audio route diagnostics never include device identifiers or labels',async()=>{
 const f=fixture();f.setItems([{kind:'audiooutput',deviceId:'opaque-private-speaker',label:'iPhone Speaker'}]);await f.controller.refresh();await f.controller.select('opaque-private-speaker');
 const report=f.controller.diagnostics({type:'auto',state:'active'}),text=JSON.stringify(report);
 assert.equal(report.selection,'device');assert.equal(report.sessionType,'auto');assert.equal(report.reportedOutputConsistent,true);
 for(const value of ['opaque-private-speaker','opaque-private-receiver','iPhone','deviceId','AirPods'])assert.equal(text.includes(value),false,value);
});

test('a room stores its preference without routing an unused probe element',async()=>{
 const f=fixture(),room=new CallAudioOutput(f.audio,f.devices,{controlOnly:true});await room.refresh();await room.select('speaker');
 assert.deepEqual(f.calls,[]);assert.equal(room.current,'speaker');assert.equal(room.diagnostics().players,0);
 const joined={sinkId:'',async setSinkId(id){this.sinkId=id;}};room.add(joined);await room.prepare(joined);
 assert.equal(joined.sinkId,'speaker');assert.equal(room.diagnostics().players,1);
});

test('cancelling a chooser does not forget an earlier successful in-flight route',async()=>{
 const f=fixture();await f.controller.refresh();let finish,choose;
 f.audio.setSinkId=id=>new Promise(resolve=>{finish=()=>{f.audio.sinkId=id;resolve();};});
 f.devices.selectAudioOutput=()=>new Promise((_,reject)=>{choose=()=>reject(new DOMException('cancelled','AbortError'));});
 const first=f.controller.select('speaker'),picker=f.controller.choose();finish();await first;choose();await picker;
 assert.equal(f.controller.current,'speaker');assert.equal(f.controller.selected,'speaker');
 const joined={sinkId:'',async setSinkId(id){this.sinkId=id;}};f.controller.add(joined);await f.controller.prepare(joined);assert.equal(joined.sinkId,'speaker');
});

test('a restored room preference is applied before a returning player starts',async()=>{
 const f=fixture(),room=new CallAudioOutput(f.audio,f.devices,{controlOnly:true,preferred:'speaker'});
 const player={sinkId:'',async setSinkId(id){this.sinkId=id;}};room.add(player);await room.prepare(player);
 assert.equal(room.current,'speaker');assert.equal(player.sinkId,'speaker');
});

test('a stale chooser rejection cannot replace a later successful selection',async()=>{
 const f=fixture();await f.controller.refresh();let reject;
 f.devices.selectAudioOutput=()=>new Promise((resolve,no)=>{reject=no;});
 const old=f.controller.choose();await f.controller.select('speaker');
 reject(new DOMException('permission','NotAllowedError'));await assert.rejects(old);
 assert.equal(f.controller.current,'speaker');assert.equal(f.controller.error,'');
});
test('a successful queued selection clears an earlier player preparation error',async()=>{
 const f=fixture();await f.controller.refresh();let reject;
 f.controller.selected='speaker';f.audio.setSinkId=id=>id==='speaker'?new Promise((resolve,no)=>{reject=no;}):Promise.resolve().then(()=>{f.audio.sinkId=id;});
 const preparing=f.controller.prepare(f.audio),selection=f.controller.select('receiver');
 reject(new DOMException('permission','NotAllowedError'));await assert.rejects(preparing);await selection;
 assert.equal(f.controller.current,'receiver');assert.equal(f.controller.error,'');
});

test('phone roles recognize explicit ru/en built-ins, never opaque IDs or default aliases',()=>{
 for(const label of ['Receiver','iPhone Receiver','Разговорный динамик','Телефонный динамик'])assert.equal(phoneOutputRole({deviceId:'opaque-receiver',label}),'receiver');
 for(const label of ['Speaker','iPhone Speaker','Динамик','Встроенный динамик'])assert.equal(phoneOutputRole({deviceId:'opaque-speaker',label}),'speaker');
 for(const label of ['JBL Speaker','AirPods','Bluetooth Receiver','Unknown speaker',''])assert.equal(phoneOutputRole({deviceId:'opaque-external',label}),'device');
 assert.equal(phoneOutputRole({deviceId:'default',label:'Speaker'}),'system');
});
test('a new phone call starts on the receiver even with headphones available',async()=>{
 const f=fixture();f.setItems([{kind:'audiooutput',deviceId:'speaker',label:'Speaker'},{kind:'audiooutput',deviceId:'receiver',label:'Receiver'},{kind:'audiooutput',deviceId:'headphones',label:'AirPods'}]);
 await f.controller.initializePhone();assert.equal(f.controller.current,'receiver');
 await f.controller.togglePhoneSpeaker();assert.equal(f.controller.current,'speaker');
 await f.controller.togglePhoneSpeaker();assert.equal(f.controller.current,'receiver');
 await f.controller.select('headphones');await f.controller.togglePhoneSpeaker();assert.equal(f.controller.current,'speaker');
 await f.controller.togglePhoneSpeaker();assert.equal(f.controller.current,'receiver');
});
test('phone switching never invents a speaker or reports failure as active',async()=>{
 const f=fixture();f.setItems([{kind:'audiooutput',deviceId:'default',label:'Speaker'}]);await f.controller.refresh();
 await assert.rejects(f.controller.togglePhoneSpeaker());assert.equal(f.calls.length,0);assert.equal(f.controller.phoneRoute,'system');assert.match(f.controller.error,/Не удалось/);
 const other=fixture();await other.controller.initializePhone();other.audio.setSinkId=async()=>{throw new DOMException('Denied','NotAllowedError');};
 await assert.rejects(other.controller.togglePhoneSpeaker());assert.equal(other.controller.phoneRoute,'receiver');
});
test('playback does not repeat a successful output switch outside the tap gesture',async()=>{
 const f=fixture();await f.controller.initializePhone();const count=f.calls.length;
 f.audio.setSinkId=async()=>{throw new DOMException('No gesture','NotAllowedError');};
 await f.controller.prepare(f.audio);assert.equal(f.calls.length,count);assert.equal(f.controller.current,'receiver');
});
test('late phone initialization cannot override a manual headphone selection',async()=>{
 const f=fixture();let resolve;f.devices.enumerateDevices=()=>new Promise(done=>{resolve=done;});
 const initializing=f.controller.initializePhone();
 f.controller.items=[{deviceId:'headphones',label:'AirPods'}];await f.controller.select('headphones');
 resolve([{kind:'audiooutput',deviceId:'headphones',label:'AirPods'},{kind:'audiooutput',deviceId:'receiver',label:'Receiver'}]);
 assert.equal(await initializing,false);assert.equal(f.controller.current,'headphones');
});
test('a room retry keeps an explicit headphone choice instead of applying the initial receiver',async()=>{
 const f=fixture();f.controller.selected='speaker';await f.controller.initializePhone();assert.equal(f.controller.selected,'speaker');assert.deepEqual(f.calls,[]);
});
