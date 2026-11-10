// Headless browsers have no physical speaker selector. Only the device API is
// substituted; capture, SDP, ICE, RTP and media playback remain native.
export function installCallOutputFixture(){
  const routes=new WeakMap(),prototype=HTMLMediaElement.prototype,native=prototype.setSinkId;
  window.__callOutputs=[{kind:'audiooutput',deviceId:'test-speaker',label:'Speaker'},{kind:'audiooutput',deviceId:'test-receiver',label:'Receiver'},{kind:'audiooutput',deviceId:'test-headphones',label:'Test headphones'}];
  window.__callOutputChanges=[];
  const devices=navigator.mediaDevices;Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:devices});
  Object.defineProperty(devices,'enumerateDevices',{configurable:true,value:async()=>window.__callOutputs});
  Object.defineProperty(prototype,'sinkId',{configurable:true,get(){return routes.get(this)||'';}});
  Object.defineProperty(prototype,'setSinkId',{configurable:true,value:async function(id){
    if(window.__callOutputReject===this){window.__callOutputReject=null;throw new DOMException('Fixture rejection','NotAllowedError');}
    if(window.__callOutputDelay){window.__callOutputDelay=false;await new Promise(resolve=>{window.__releaseCallOutput=resolve;});}
    if(!id&&native)await native.call(this,'');
    routes.set(this,id);window.__callOutputChanges.push({id,hasStream:!!this.srcObject});
  }});
  if(!navigator.audioSession){const session=new EventTarget();session.type='auto';session.state='active';Object.defineProperty(navigator,'audioSession',{configurable:true,value:session});}
}
