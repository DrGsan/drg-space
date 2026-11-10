import {tText} from "../../core/i18n.js";
// Only numerical transport counters and applied capture settings. Never retain
// audio, SDP, candidate addresses, device identifiers or TURN credentials.
const finite=value=>typeof value==='number'&&Number.isFinite(value)?value:null;
const delta=(before,after,key)=>finite(before?.[key])!==null&&finite(after?.[key])!==null?Math.max(0,after[key]-before[key]):null;
const round=value=>value===null?null:Math.round(value*100)/100;
export function audioCounters(before,after){
 const rows=[];
 for(const value of after.values()){
  if(value.type!=='inbound-rtp'||(value.kind||value.mediaType)!=='audio')continue;
  const previous=before.get(value.id);if(!previous||value.timestamp<=previous.timestamp)continue;
  const received=delta(previous,value,'packetsReceived'),lost=delta(previous,value,'packetsLost'),samples=delta(previous,value,'totalSamplesReceived'),concealed=delta(previous,value,'concealedSamples'),emitted=delta(previous,value,'jitterBufferEmittedCount'),delay=delta(previous,value,'jitterBufferDelay');
  if(received===0&&lost===0&&samples===0)continue;
  rows.push({intervalMs:round(value.timestamp-previous.timestamp),received,lost,lossPercent:received!==null&&lost!==null&&received+lost>0?round(100*lost/(received+lost)):null,jitterMs:finite(value.jitter)===null?null:round(value.jitter*1000),concealedPercent:samples>0&&concealed!==null?round(100*concealed/samples):null,concealmentEvents:delta(previous,value,'concealmentEvents'),bufferMs:emitted>0&&delay!==null?round(1000*delay/emitted):null});
 }
 const transport=[...after.values()].find(value=>value.type==='transport'&&value.selectedCandidatePairId),pair=transport&&after.get(transport.selectedCandidatePairId),local=pair&&after.get(pair.localCandidateId),remote=pair&&after.get(pair.remoteCandidateId);
 return {streams:rows,roundTripMs:finite(pair?.currentRoundTripTime)===null?null:round(pair.currentRoundTripTime*1000),route:local||remote?local?.candidateType==='relay'||remote?.candidateType==='relay'?'relay':'direct':null,protocol:['udp','tcp'].includes(local?.protocol)?local.protocol:null};
}
export function microphoneSettings(track){
 const settings=track?.getSettings?.()||{};
 return Object.fromEntries(['echoCancellation','noiseSuppression','autoGainControl','sampleRate','channelCount'].filter(key=>typeof settings[key]==='boolean'||typeof settings[key]==='number').map(key=>[key,settings[key]]));
}
export function microphoneGainAvailable(track){
 const capability=track?.getCapabilities?.().autoGainControl;
 return typeof track?.getSettings?.().autoGainControl==='boolean'&&typeof track.applyConstraints==='function'&&(!Array.isArray(capability)||capability.includes(true)&&capability.includes(false));
}
export async function setMicrophoneGain(track,enabled){
 if(!track||track.readyState!=='live'||typeof track.applyConstraints!=='function')throw new Error(tText("chat:microphoneIsUnavailable"));
 if(!microphoneGainAvailable(track))throw new Error(tText("chat:theBrowserDoesNotSupportAutomaticVolumeAdjustment"));
 try{await track.applyConstraints({...(track.getConstraints?.()||{}),autoGainControl:{exact:enabled}});}catch{throw new Error(tText("chat:theBrowserCannotChangeTheVolumeOfThis"));}
 if(track.getSettings().autoGainControl!==enabled)throw new Error(tText("chat:theBrowserHasNotAppliedTheVolumeSetting"));
}
export class CallAudioDiagnostics {
 constructor(pc,track,changed=()=>{}){this.pc=pc;this.track=track;this.changed=changed;this.previous=null;this.samples=[];this.stopped=false;this.timer=setInterval(()=>this.capture(),2000);this.capture();}
 async capture(){
  if(this.stopped||this.pending||this.pc.connectionState==='closed')return;this.pending=true;
  try{const stats=await this.pc.getStats();if(this.stopped)return;if(this.previous){const counters=audioCounters(this.previous,stats);if(counters.streams.length){this.samples.push(counters);this.samples=this.samples.slice(-30);}}this.previous=new Map([...stats].filter(([,value])=>value.type==='inbound-rtp'&&(value.kind||value.mediaType)==='audio').map(([id,value])=>[id,Object.fromEntries(['id','type','kind','mediaType','timestamp','packetsReceived','packetsLost','totalSamplesReceived','concealedSamples','concealmentEvents','jitterBufferEmittedCount','jitterBufferDelay'].filter(key=>key in value).map(key=>[key,value[key]]))]));this.changed();}catch{}finally{this.pending=false;}
 }
 report(){
  const values=this.samples.flatMap(sample=>sample.streams),maximum=key=>{const numbers=values.map(v=>v[key]).filter(v=>v!==null);return numbers.length?Math.max(...numbers):null;};
  return {windowSeconds:Math.round(this.samples.reduce((total,sample)=>total+Math.max(...sample.streams.map(stream=>stream.intervalMs)),0)/1000),latest:this.samples.at(-1)||null,peakLossPercent:maximum('lossPercent'),peakConcealedPercent:maximum('concealedPercent'),microphone:microphoneSettings(this.track)};
 }
 stop(){this.stopped=true;clearInterval(this.timer);this.previous=null;this.samples=[];}
}
export function audioQualityText(report){
 const value=(number,suffix)=>number===null||number===undefined?tText("chat:notReported"):number+suffix,latest=report.latest,stream=latest?.streams[0];
 const transport=stream?tText("chat:incomingAudioLastSecondsPacketLossAudioRecovery", {value0:(report.windowSeconds), value1:(value(stream.lossPercent,'%')), value2:(value(stream.concealedPercent,'%')), value3:(value(stream.jitterMs,(" "+tText("chat:speaker")+""))), value4:(value(stream.bufferMs,(" "+tText("chat:speaker")+""))), value5:(value(latest.roundTripMs,(" "+tText("chat:speaker")+""))), value6:(latest.route==='relay'?tText("chat:viaTurn"):latest.route==='direct'?tText("chat:direct"):tText("chat:notReported")), value7:(latest.protocol?' · '+latest.protocol.toUpperCase():''), value8:(value(report.peakLossPercent,'%')), value9:(value(report.peakConcealedPercent,'%'))}):tText("chat:measuringIncomingAudioQuality");
 const output=report.audioOutput;if(!output)return transport;
 const selection=output.selection==='mixed'?tText('chat:audioOutputNeedsAttention'):output.selection==='device'?tText('chat:audioOutputSelected'):tText('chat:asInTheSystem');
 return transport+'\n\n'+tText('chat:audioOutputDiagnostic',{value0:selection,value1:output.players,value2:output.sessionType||tText('chat:notReported'),value3:output.sessionState||tText('chat:notReported')});
}
