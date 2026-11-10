// Headless browsers may still use the host's physical speakers. Silence only
// these disposable test contexts; keep capture, SDP, RTP and playback running.
export function installSilentAudio(){
 if(window.__drgSilentTestAudio)return;window.__drgSilentTestAudio=true;
 const nativePlay=HTMLMediaElement.prototype.play;
 HTMLMediaElement.prototype.play=function(...args){this.muted=true;return nativePlay.apply(this,args);};
 for(const element of document.querySelectorAll('audio,video'))element.muted=true;
 const nativeConnect=AudioNode.prototype.connect,sinks=new WeakMap();window.__drgSilentTestGains=[];
 AudioNode.prototype.connect=function(target,...args){
  if(target instanceof AudioDestinationNode){
   let sink=sinks.get(target);
   if(!sink){sink=target.context.createGain();sink.gain.value=0;nativeConnect.call(sink,target);sinks.set(target,sink);window.__drgSilentTestGains.push(sink);}
   return nativeConnect.call(this,sink,...args);
  }
  return nativeConnect.call(this,target,...args);
 };
}
export async function silenceTestAudio(context){await context.addInitScript(installSilentAudio);}
