import {test} from 'node:test';
import assert from 'node:assert/strict';
import {audioCounters,CallAudioDiagnostics,microphoneSettings,setMicrophoneGain} from '../web/modules/chat/audio-diagnostics.js';
const stats=(overrides={})=>new Map([['in',{id:'in',type:'inbound-rtp',kind:'audio',timestamp:1000,packetsReceived:100,packetsLost:2,totalSamplesReceived:48000,concealedSamples:480,concealmentEvents:1,jitterBufferEmittedCount:48000,jitterBufferDelay:2400,...overrides}],['transport',{type:'transport',selectedCandidatePairId:'pair'}],['pair',{localCandidateId:'local',remoteCandidateId:'remote',currentRoundTripTime:.08}],['local',{candidateType:'relay',protocol:'udp',address:'private-address',usernameFragment:'secret'}],['remote',{candidateType:'host',address:'private-address-2'}]]);
test('audio diagnostics use interval counters, include packet concealment and expose no addresses',()=>{
 const result=audioCounters(stats(),stats({timestamp:3000,packetsReceived:198,packetsLost:4,totalSamplesReceived:144000,concealedSamples:2400,concealmentEvents:3,jitter:.015,jitterBufferEmittedCount:144000,jitterBufferDelay:7200}));
 assert.deepEqual(result,{streams:[{intervalMs:2000,received:98,lost:2,lossPercent:2,jitterMs:15,concealedPercent:2,concealmentEvents:2,bufferMs:50}],roundTripMs:80,route:'relay',protocol:'udp'});
 assert.doesNotMatch(JSON.stringify(result),/private|secret/);
});
test('missing fields remain unknown and counter corrections cannot create negative loss',()=>{
 const minimal=new Map([['in',{id:'in',type:'inbound-rtp',mediaType:'audio',timestamp:1000}]]),later=new Map([['in',{...minimal.get('in'),timestamp:2000}]]);
 const report=audioCounters(minimal,later);assert.equal(report.streams[0].lossPercent,null);assert.equal(report.streams[0].concealedPercent,null);assert.equal(report.roundTripMs,null);
 assert.equal(audioCounters(stats(),stats({timestamp:2000,packetsReceived:200,packetsLost:1})).streams[0].lossPercent,0);
 assert.equal(audioCounters(new Map(),stats()).streams.length,0);
});
test('changing automatic gain preserves constraints and never replaces or stops the microphone',async()=>{
 let constraints={echoCancellation:true,noiseSuppression:true,deviceId:{exact:'private-device'},autoGainControl:true},settings={autoGainControl:true,deviceId:'private-device',sampleRate:48000};
 const track={readyState:'live',getConstraints:()=>constraints,getSettings:()=>settings,applyConstraints:async value=>{constraints=value;settings.autoGainControl=value.autoGainControl.exact;}};
 await setMicrophoneGain(track,false);assert.equal(settings.autoGainControl,false);assert.equal(constraints.echoCancellation,true);assert.equal(constraints.noiseSuppression,true);assert.deepEqual(constraints.deviceId,{exact:'private-device'});assert.deepEqual(microphoneSettings(track),{autoGainControl:false,sampleRate:48000});
 settings.autoGainControl=undefined;await assert.rejects(setMicrophoneGain(track,true),/не поддерживает/);
});
test('late stats are discarded on hangup and retained reports omit device metadata',async()=>{
 let resolve;const pc={connectionState:'connected',getStats:()=>new Promise(r=>resolve=r)},monitor=new CallAudioDiagnostics(pc,{getSettings:()=>({deviceId:'private-device',noiseSuppression:true})});
 monitor.stop();resolve(stats());await new Promise(r=>setImmediate(r));assert.equal(monitor.previous,null);assert.equal(monitor.report().latest,null);assert.doesNotMatch(JSON.stringify(monitor.report()),/private-device/);
});
test('the rolling report retains peaks while its baseline drops candidate addresses',async()=>{
 let current=stats();const monitor=new CallAudioDiagnostics({connectionState:'connected',getStats:async()=>current},{getSettings:()=>({autoGainControl:true,deviceId:'private-device'})});
 try{
  await new Promise(r=>setImmediate(r));assert.doesNotMatch(JSON.stringify([...monitor.previous.values()]),/private-address|secret/);
  current=stats({timestamp:3000,packetsReceived:198,packetsLost:4,totalSamplesReceived:144000,concealedSamples:2400});await monitor.capture();
  assert.equal(monitor.report().peakLossPercent,2);assert.equal(monitor.report().windowSeconds,2);
  current=stats({timestamp:5000,packetsReceived:298,packetsLost:4,totalSamplesReceived:240000,concealedSamples:2400});await monitor.capture();
  assert.equal(monitor.report().latest.streams[0].lossPercent,0);assert.equal(monitor.report().peakLossPercent,2);assert.equal(monitor.report().windowSeconds,4);
 }finally{monitor.stop();}
});
