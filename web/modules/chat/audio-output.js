import {tText} from "../../core/i18n.js";
const setSink=(audio,id)=>{try{return Promise.resolve(audio.setSinkId(id));}catch(error){return Promise.reject(error);}};
export const phoneCallAudioUI=()=>window.matchMedia('(max-width:760px) and (pointer:coarse), (max-height:500px) and (pointer:coarse)').matches;
// MediaDeviceInfo has no standard output-type field. Recognize only explicit
// built-in port names exposed by iOS (ru/en); never guess an opaque device ID
// or treat a Bluetooth speaker / the system-default alias as the loudspeaker.
export function phoneOutputRole(device){
  if(!device.deviceId||device.deviceId==='default')return 'system';
  const label=device.label.trim().toLowerCase().replace(/[()]/g,'').replace(/\s+/g,' ');
  if(/^(?:iphone[ -]+)?(?:receiver|earpiece|handset|разговорный динамик|телефонный динамик|при[её]мник)(?:[ -]+iphone)?$/u.test(label))return 'receiver';
  if(/^(?:iphone[ -]+)?(?:speaker|speakerphone|loudspeaker|built[ -]in speaker|динамик|встроенный динамик|громкая связь)(?:[ -]+iphone)?$/u.test(label))return 'speaker';
  return 'device';
}
// Device IDs stay in this browser; they are never part of call signaling.
export class CallAudioOutput {
  constructor(audio,devices,{changed=()=>{},controlOnly=false,preferred}={}){
    this.audio=audio;this.devices=devices;this.changed=changed;this.items=[];this.generation=0;
    this.controlOnly=controlOnly;this.targets=new Set(controlOnly?[]:[audio]);this.selected=this.supported&&typeof preferred==='string'?preferred:audio.sinkId||'';this.intent=0;this.enumeration=0;
    this.pending=0;this.choosing=0;this.queue=[];this.running=false;this.failure=null;
  }
  get supported(){return typeof this.audio.setSinkId==='function';}
  get current(){const ids=new Set([...this.targets].map(audio=>audio.sinkId||''));return !ids.size?this.selected:ids.size===1?[...ids][0]:null;}
  get busy(){return this.pending>0||this.choosing>0;}
  get phoneRoutes(){
    const only=role=>{const items=this.items.filter(device=>phoneOutputRole(device)===role);return items.length===1?items[0]:null;};
    return {receiver:only('receiver'),speaker:only('speaker')};
  }
  get phoneRoute(){return this.current===null?'mixed':phoneOutputRole(this.items.find(device=>device.deviceId===this.current)||{deviceId:this.current||'',label:''});}
  get error(){
    if(!this.failure)return '';
    if(this.failure.name==='PhoneRouteUnavailable')return tText('chat:phoneAudioRouteUnavailable');
    if(this.failure.operation==='refresh')return tText('chat:couldNotRetrieveSpeakerListPleaseRetry');
    if(this.failure.name==='NotSupportedError')return tText('chat:theBrowserDoesNotAllowYouToSelect');
    if(this.failure.name==='NotAllowedError')return tText('chat:speakerSelectionIsNotAllowedByTheBrowser');
    if(this.failure.name==='NotFoundError')return tText('chat:deviceDisconnectedPleaseSelectTheSpeakerAgain');
    return tText('chat:couldNotApplyAudioOutput');
  }
  // Start an idle operation synchronously: Safari's native chooser must retain
  // the click gesture. Only overlapping sink changes wait for earlier work.
  enqueue(work){
    return new Promise((resolve,reject)=>{this.queue.push({work,resolve,reject});this.drain();});
  }
  drain(){
    if(this.running||!this.queue.length)return;
    this.running=true;const {work,resolve,reject}=this.queue.shift();let result;
    try{result=work();}catch(error){result=Promise.reject(error);}
    Promise.resolve(result).then(resolve,reject).finally(()=>{this.running=false;this.drain();});
  }
  add(audio){this.targets.add(audio);}
  remove(audio){if(audio!==this.audio){this.targets.delete(audio);this.changed();}}
  async prepare(audio){
    if(!this.supported||!this.targets.has(audio))return false;
    const generation=this.generation;this.pending++;this.changed();
    try{return await this.enqueue(async()=>{
      if(generation!==this.generation||!this.targets.has(audio))return false;
      const id=this.selected;
      if((audio.sinkId||'')!==id)await setSink(audio,id);
      if(generation!==this.generation){await setSink(audio,'').catch(()=>{});return false;}
      if(!this.targets.has(audio))return false;
      if((audio.sinkId||'')!==id)throw new Error(tText('chat:audioOutputNotApplied'));
      return true;
    });}catch(error){if(generation===this.generation)this.failure={name:error.name};throw error;}
    finally{this.pending--;this.changed();}
  }
  async initializePhone(){
    const generation=this.generation,intent=this.intent;
    await this.refresh();
    if(generation!==this.generation||intent!==this.intent||this.selected)return false;
    const receiver=this.phoneRoutes.receiver;
    return receiver?this.select(receiver.deviceId):false;
  }
  togglePhoneSpeaker(){
    const {receiver,speaker}=this.phoneRoutes;
    if(!this.supported||!receiver||!speaker){this.failure={name:'PhoneRouteUnavailable'};this.changed();return Promise.reject(new Error(this.error));}
    // No enumerateDevices await here: setSinkId runs in the actual tap gesture.
    return this.select((this.phoneRoute==='speaker'?receiver:speaker).deviceId);
  }
  async refresh(){
    const generation=this.generation,enumeration=++this.enumeration;if(!this.supported||!this.devices?.enumerateDevices){this.items=[];this.changed();return;}
    let devices;
    try{devices=await this.devices.enumerateDevices();}catch(error){if(generation===this.generation&&enumeration===this.enumeration){this.failure={name:error.name,operation:'refresh'};this.changed();}throw error;}
    if(generation!==this.generation||enumeration!==this.enumeration)return;
    this.items=[{deviceId:'',label:tText("chat:asInTheSystem")},...devices.filter(d=>d.kind==='audiooutput'&&d.deviceId).map((d,i)=>({deviceId:d.deviceId,label:d.label||tText("chat:speaker2", {value0:(i+1)})}))];
    this.items=this.items.filter((d,i,items)=>items.findIndex(other=>other.deviceId===d.deviceId)===i);
    if(this.failure?.operation==='refresh')this.failure=null;
    if(this.selected&&!this.items.some(d=>d.deviceId===this.selected))await this.select('');this.changed();
  }
  async select(id){
    if(!this.supported){this.failure={name:'NotSupportedError'};this.changed();throw new Error(tText("chat:theBrowserDoesNotAllowYouToSelect"));}
    if(id&&!this.items.some(d=>d.deviceId===id)){this.failure={name:'NotFoundError'};this.changed();throw new DOMException(tText("chat:deviceDisconnectedPleaseSelectTheSpeakerAgain"),'NotFoundError');}
    const generation=this.generation,intent=++this.intent;this.pending++;this.failure=null;this.changed();
    try{return await this.enqueue(async()=>{
      if(generation!==this.generation||intent!==this.intent)return false;
      const targets=[...this.targets],previous=new Map(targets.map(audio=>[audio,audio.sinkId||'']));
      const results=await Promise.allSettled(targets.map(audio=>setSink(audio,id)));
      if(generation!==this.generation){await Promise.allSettled(targets.map(audio=>setSink(audio,'')));return false;}
      const failed=results.find((result,i)=>this.targets.has(targets[i])&&result.status==='rejected');
      const inconsistent=targets.some(audio=>this.targets.has(audio)&&(audio.sinkId||'')!==id);
      const disconnected=id&&!this.items.some(device=>device.deviceId===id);
      if(failed||inconsistent||disconnected){
        await Promise.allSettled(targets.filter(audio=>this.targets.has(audio)&&(audio.sinkId||'')!==previous.get(audio)).map(audio=>setSink(audio,previous.get(audio))));
        throw failed?.reason||(disconnected?new DOMException(tText('chat:deviceDisconnectedPleaseSelectTheSpeakerAgain'),'NotFoundError'):new Error(tText('chat:audioOutputNotApplied')));
      }
      this.selected=id;if(intent===this.intent)this.failure=null;return intent===this.intent;
    });}catch(error){if(generation===this.generation&&intent===this.intent)this.failure={name:error.name};throw error;}
    finally{this.pending--;this.changed();}
  }
  async choose(){
    if(!this.supported||!this.devices?.selectAudioOutput)return false;
    // Must be called directly from the button's click, before awaiting work.
    const generation=this.generation,intent=++this.intent;this.choosing++;this.failure=null;
    try{
      const choice=this.devices.selectAudioOutput();this.changed();const device=await choice;
      if(generation!==this.generation||intent!==this.intent)return false;
      if(!this.items.some(d=>d.deviceId===device.deviceId))this.items.push({deviceId:device.deviceId,label:device.label||tText("chat:speaker3")});
      return await this.select(device.deviceId);
    }catch(error){if(error.name==='AbortError')return false;if(generation===this.generation&&intent===this.intent)this.failure={name:error.name};throw error;}
    finally{this.choosing--;this.changed();}
  }
  diagnostics(session){
    const current=this.current;
    return {supported:this.supported,pending:this.busy,players:this.targets.size,reportedOutputConsistent:current!==null,selection:current===null?'mixed':current?'device':'system',phoneRoute:this.phoneRoute,phoneReceiverAvailable:!!this.phoneRoutes.receiver,phoneSpeakerAvailable:!!this.phoneRoutes.speaker,error:this.failure?.name||null,
      playback:[...this.targets].map(audio=>({hasStream:!!audio.srcObject,paused:!!audio.paused,muted:!!audio.muted,volume:typeof audio.volume==='number'?audio.volume:null})),
      sessionType:typeof session?.type==='string'?session.type:null,sessionState:typeof session?.state==='string'?session.state:null};
  }
  reset(){
    this.generation++;this.intent++;this.enumeration++;this.items=[];this.selected='';this.failure=null;
    const targets=[...this.targets];this.targets=new Set(this.controlOnly?[]:[this.audio]);
    if(this.supported)this.enqueue(()=>Promise.allSettled(targets.map(audio=>setSink(audio,'')))).catch(()=>{});
    this.changed();
  }
}
