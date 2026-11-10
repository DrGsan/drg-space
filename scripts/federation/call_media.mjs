// Two native browser RTC peers. Every control message passes through the
// external-device bridge and both independently deployed DrG servers.
import {chromium} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {existsSync} from 'node:fs';
import {randomUUID} from 'node:crypto';

function require(value,message){if(!value)throw new Error(message);}
const command=JSON.parse(process.env.DRG_CALL_MEDIA_BRIDGE||'null');
require(Array.isArray(command)&&command.length>0,'DRG_CALL_MEDIA_BRIDGE must be a command argument array.');
const bridge=spawn(command[0],command.slice(1),{env:process.env,stdio:['pipe','pipe','inherit']});
const pending=new Map();let serial=0;
createInterface({input:bridge.stdout}).on('line',line=>{
  let data;try{data=JSON.parse(line);}catch{for(const item of pending.values())item.reject(new Error('Invalid media bridge output.'));return;}
  const item=pending.get(data.id);if(!item)return;pending.delete(data.id);clearTimeout(item.timer);
  if(data.ok)item.resolve(data.result);else item.reject(new Error(data.error));
});
bridge.on('exit',code=>{for(const item of pending.values())item.reject(new Error('Media bridge stopped ('+code+').'));});
function rpc(method,payload={}){const id=++serial;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error('Media bridge timed out: '+method));},20000);pending.set(id,{resolve,reject,timer});bridge.stdin.write(JSON.stringify({id,method,payload})+'\n');});}
const sleep=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
const executable=process.env.DRG_BROWSER_EXECUTABLE||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser=await chromium.launch({headless:true,...(existsSync(executable)?{executablePath:executable}:{}),
  args:['--autoplay-policy=no-user-gesture-required','--disable-features=WebRtcHideLocalIpsWithMdns']});
const contexts=[await browser.newContext(),await browser.newContext()];
const pages=await Promise.all(contexts.map(context=>context.newPage()));
let started=false;
try{
  let config;
  for(let attempt=0;attempt<12;attempt++){
    config=await rpc('config');
    if(config.caller.available&&config.callee.available)break;
    await sleep(1500);
  }
  require(config.caller.available&&config.callee.available,'A real authenticated TURN allocation is required on both servers.');
  require(config.caller.iceServers?.some(server=>server.username&&server.credential),'Caller lacks own TURN credentials.');
  require(config.callee.iceServers?.some(server=>server.username&&server.credential),'Callee lacks own TURN credentials.');
  const relayPort=Number(process.env.DRG_CALL_MEDIA_TURN_PORT||0);
  const configurations=[config.caller,config.callee].map(config=>({
    iceServers:relayPort?config.iceServers.map(server=>({...server,urls:server.urls.filter(url=>url.startsWith('turn:')).map(()=>`turn:127.0.0.1:${relayPort}?transport=udp`)})).filter(server=>server.urls.length):[],
    ...(relayPort?{iceTransportPolicy:'relay'}:{}),bundlePolicy:'max-bundle'}));
  for(let index=0;index<pages.length;index++)await pages[index].evaluate(async config=>{
    const pc=new RTCPeerConnection(config),audio=new AudioContext(),oscillator=audio.createOscillator(),destination=audio.createMediaStreamDestination();
    oscillator.frequency.value=330;oscillator.connect(destination);oscillator.start();await audio.resume();
    const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;const brush=canvas.getContext('2d');let frame=0;
    const timer=setInterval(()=>{brush.fillStyle=frame++%2?'#406caf':'#72aa82';brush.fillRect(0,0,320,180);brush.fillStyle='white';brush.fillText('Federation RTP '+frame,10,30);},65);
    const visual=canvas.captureStream(15),stream=new MediaStream([...destination.stream.getTracks(),...visual.getTracks()]);
    for(const track of stream.getTracks())pc.addTrack(track,stream);
    const candidates=[],seen=new Set();pc.onicecandidate=event=>{if(event.candidate)candidates.push(event.candidate.toJSON());};
    pc.ontrack=event=>{const element=document.createElement(event.track.kind==='video'?'video':'audio');element.muted=true;element.autoplay=true;element.playsInline=true;element.srcObject=new MediaStream([event.track]);document.body.append(element);element.play().catch(()=>{});};
    window.rtc={pc,candidates,seen,stream,audio,oscillator,timer};
  },configurations[index]);
  const offer=await pages[0].evaluate(async()=>{const pc=window.rtc.pc;await pc.setLocalDescription(await pc.createOffer());return pc.localDescription.sdp;});
  const call=await rpc('invite',{id:randomUUID(),sdp:offer});started=true;
  require(call.status==='ringing'&&call.owned,'The caller did not receive its bound outgoing call.');
  const incoming=(await rpc('current')).call;
  require(incoming?.id===call.id&&!incoming.outgoing&&!incoming.owned&&!incoming.sdp,'The callee incoming summary is invalid.');
  const accepted=await rpc('accept');
  require(accepted.sdp===offer&&accepted.owned,'The verified offer was not delivered to the accepting device.');
  const answer=await pages[1].evaluate(async sdp=>{const pc=window.rtc.pc;await pc.setRemoteDescription({type:'offer',sdp});await pc.setLocalDescription(await pc.createAnswer());return pc.localDescription.sdp;},accepted.sdp);
  await rpc('callee_signal',{sdp:answer,media:{muted:false,camera:true,screen:false}});
  let installedAnswer=false,connected=false;
  for(let attempt=0;attempt<100;attempt++){
    const batches=await Promise.all(pages.map(page=>page.evaluate(()=>window.rtc.candidates.splice(0))));
    if(batches[0].length)await rpc('caller_signal',{candidates:batches[0],media:{muted:false,camera:true,screen:false}});
    if(batches[1].length)await rpc('callee_signal',{candidates:batches[1],media:{muted:false,camera:true,screen:false}});
    const caller=await rpc('caller_poll'),callee=await rpc('callee_poll');
    if(caller.sdp&&!installedAnswer){await pages[0].evaluate(sdp=>window.rtc.pc.setRemoteDescription({type:'answer',sdp}),caller.sdp);installedAnswer=true;}
    for(const [index,value]of [[0,caller],[1,callee]])await pages[index].evaluate(async candidates=>{const {pc,seen}=window.rtc;if(!pc.remoteDescription)return;for(const candidate of candidates){const key=JSON.stringify(candidate);if(!seen.has(key)){await pc.addIceCandidate(candidate);seen.add(key);}}},value.candidates||[]);
    const states=await Promise.all(pages.map(page=>page.evaluate(()=>window.rtc.pc.connectionState)));
    if(states.every(state=>state==='connected')){connected=true;break;}
    require(!states.includes('failed'),'Native WebRTC transport failed.');await sleep(120);
  }
  require(connected,'Native WebRTC peers did not connect through federated signaling.');
  await rpc('caller_signal',{connected:true});await rpc('callee_signal',{connected:true});
  const collect=page=>page.evaluate(async()=>{
    const stats=await window.rtc.pc.getStats();let audio=0,video=0,selected=[];
    for(const value of stats.values()){
      if(value.type==='inbound-rtp'&&value.kind==='audio')audio+=value.packetsReceived||0;
      if(value.type==='inbound-rtp'&&value.kind==='video')video+=value.framesDecoded||0;
      if(value.type==='transport'&&value.selectedCandidatePairId){const pair=stats.get(value.selectedCandidatePairId);selected=[stats.get(pair.localCandidateId)?.candidateType,stats.get(pair.remoteCandidateId)?.candidateType];}
    }
    return {audio,video,selected};
  });
  let metrics;
  for(let attempt=0;attempt<60;attempt++){metrics=await Promise.all(pages.map(collect));if(metrics.every(value=>value.audio>5&&value.video>2))break;await sleep(150);}
  require(metrics.every(value=>value.audio>5&&value.video>2),'Both directions must receive real audio RTP and decode real video frames.');
  if(relayPort)require(metrics.every(value=>value.selected[0]==='relay'&&value.selected[1]==='relay'),'Relay mode did not select authenticated TURN candidates on both peers.');
  const theft=await rpc('theft');require(Object.values(theft).every(value=>value===true),'Stolen server credentials bypassed live call authorization.');
  require((await rpc('tamper')).rejected,'Forged server-only SDP was not rejected independently by the device.');
  await rpc('caller_end',{reason:'hangup'});started=false;
  const terminal=await rpc('callee_poll');require(terminal.status==='ended'&&!terminal.sdp&&!terminal.candidates&&!terminal.peer_actions,'Ended call retained private signaling.');
  console.log('PASS: real audio RTP and decoded video frames in both directions through two device-authorized DrG servers'+(relayPort?' and authenticated TURN relay.':'.'));
  console.log('RTP metrics: '+JSON.stringify(metrics));
  console.log('PASS: independently verified peer SDP/ICE, rejection of server-key-only SDP substitution, hangup, and media cleanup.');
  console.log('PASS: live-call theft attacks rejected missing/wrong TLS, forged device proof, altered runtime handle, and replay; captured authorized poll stayed encrypted for the user device.');
}finally{
  if(started)await rpc('caller_end',{reason:'failed'}).catch(()=>{});
  for(const page of pages)await page.evaluate(()=>{const rtc=window.rtc;if(!rtc)return;rtc.pc.close();rtc.stream.getTracks().forEach(track=>track.stop());rtc.oscillator.stop();clearInterval(rtc.timer);rtc.audio.close();}).catch(()=>{});
  await browser.close();bridge.stdin.end();bridge.kill('SIGTERM');
}
