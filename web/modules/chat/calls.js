import {tText,tHtml} from "../../core/i18n.js";
import {renderMarkup} from '../../core/dom.js';
import {CallAudioOutput,phoneCallAudioUI,phoneOutputRole} from './audio-output.js';
import {acquireCallAudioSession} from './audio-session.js';
import {CallAudioDiagnostics,audioQualityText,microphoneSettings,microphoneGainAvailable,setMicrophoneGain} from './audio-diagnostics.js';
import {$,esc} from '../../core/ui.js';
import {personName,personAvatar} from '../../core/people.js';
import {chatIcon} from './icons.js';
import {callLive,callLabel,callDuration} from './call-format.js';
import {csrfCookie} from '../../core/auth.js';
import {abortable} from '../../core/request.js';
import {ensureDevice,hasDevice,operate,operateLocal,verifyCallActions} from '../federation/client.js';
import './calls.css';

let active,ringAudio;
document.addEventListener('drg-people-names-change',()=>{if(active){active.drawKey=null;active.draw();}});
function unlockRing(){try{ringAudio??=new (window.AudioContext||window.webkitAudioContext)();ringAudio.resume().catch(()=>{});}catch{}}
document.addEventListener('pointerdown',unlockRing);document.addEventListener('keydown',unlockRing);
function ring(){if(ringAudio?.state!=='running')return;for(const delay of [0,.3]){const oscillator=ringAudio.createOscillator(),gain=ringAudio.createGain(),at=ringAudio.currentTime+delay;oscillator.frequency.value=660;gain.gain.setValueAtTime(.0001,at);gain.gain.exponentialRampToValueAtTime(.07,at+.03);gain.gain.exponentialRampToValueAtTime(.0001,at+.22);oscillator.connect(gain);gain.connect(ringAudio.destination);oscillator.start(at);oscillator.stop(at+.24);}}

export const callsController={
  attach(ctx){if(!ctx.moduleEnabled('chat')){this.destroy();return;}if(!active||active.uid!==ctx.state.me.id){this.destroy();active=new VoiceCalls(ctx);}active.ctx=ctx;},
  get busy(){return !!active?.call||!!active?.starting||!!active?.externalBusy;},
  setGuestBusy(value){if(active)active.externalBusy=value;},
  get checked(){return typeof active?.available==='boolean';},
  get available(){return active?.available===true;},
  get guestAvailable(){return active?.configuration?.guest_available??active?.available===true;},
  get guestTransports(){return active?.configuration?.guest_transports||[];},
  start(sid){return active?.start(sid);},
  startFederated(contact){return active?.startFederated(contact);},
  sync(){active?.poll();},
  destroy(){document.dispatchEvent(new Event('drg-call-reset'));active?.destroy();active=null;},
};
class VoiceCalls {
  constructor(ctx){this.ctx=ctx;this.uid=ctx.state.me.id;this.device=crypto.randomUUID();this.disposed=false;this.candidates=[];this.remoteCandidates=new Set();this.sequence=0;this.createAudioReceiver();this.outputLayoutChange=()=>this.drawOutputs();window.addEventListener('resize',this.outputLayoutChange);this.audioSession=navigator.audioSession;this.sessionChange=()=>this.audioSessionChanged();this.audioSession?.addEventListener?.('statechange',this.sessionChange);this.deviceChange=()=>{if(this.stream)this.refreshAudioOutput();};navigator.mediaDevices?.addEventListener?.('devicechange',this.deviceChange);this.focus=()=>{this.refreshAvailability(true);this.poll();if(this.stream&&!document.hidden)this.refreshAudioOutput();};this.inbox=()=>this.poll(true);window.addEventListener('focus',this.focus);document.addEventListener('visibilitychange',this.focus);document.addEventListener('drg-inbox-change',this.inbox);this.beforeUnload=()=>this.availabilityAbort?.abort();window.addEventListener('beforeunload',this.beforeUnload);this.pagehide=()=>{this.availabilityAbort?.abort();const call=this.call;if(callLive(call)&&call.owned){if(call.federation){this.api('/'+call.id+'/end','POST',{reason:'hangup'}).catch(()=>{});this.close();return;}this.close();fetch('/api/calls/'+call.id+'/end',{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRF-Token':csrfCookie()},body:JSON.stringify({reason:'hangup'})}).catch(()=>{});}};window.addEventListener('pagehide',this.pagehide);this.timer=setInterval(()=>this.poll(),2000);this.fullscreenChange=()=>this.draw();document.addEventListener('fullscreenchange',this.fullscreenChange);this.clock=setInterval(()=>this.tick(),1000);this.availabilityTimer=setInterval(()=>this.refreshAvailability(),15000);this.refreshAvailability();}
  async api(path,method='GET',data,signal){
    const id=/^\/([^/?]+)\//.exec(path)?.[1],route=(id&&this.federationRoutes?.get(id))||this.call?.federation;
    if(route){
      if(path==='/config')return operateLocal(this.ctx,'call_config',{});
      const operation=path.startsWith('/current?')?'call_poll':path===''?'call_invite':path.endsWith('/accept')?'call_accept':path.endsWith('/signal')?'call_signal':path.endsWith('/end')?'call_end':null;
      if(!operation)throw new Error(tText("chat:unsupportedInterServerCallAction"));
      const device=await ensureDevice(this.ctx),payload={...data,id:id||data?.id||this.call?.id,device:this.device};delete payload.space_id;
      const local=route.home_server_id===device.serverId;
      const call=local?await operateLocal(this.ctx,operation,payload):await operate(this.ctx,route.peer_id,route.actor_id,operation,payload);
      if(call?.federation){
        if(call.federation.home_server_id!==route.home_server_id||call.federation.peer_id!==route.peer_id||call.federation.actor_id!==route.actor_id)throw new Error(tText("chat:theServerHasChangedTheCallParticipants"));
        this.federationProofs??=new Map();let proofs=this.federationProofs.get(call.id);if(!proofs){proofs=new Set();this.federationProofs.set(call.id,proofs);}
        await verifyCallActions(this.ctx,call,proofs);
        if(!local)await operateLocal(this.ctx,'call_outgoing_status',{id:call.id,device:this.device,status:call.status,answered:call.answered||0,connected:call.connected||0,ended:call.ended||0});
      }
      return path.startsWith('/current?')?{call}:call;
    }
    const result=await this.ctx.api('/calls'+path,method,data,undefined,undefined,{timeoutMs:12000,signal});
    if(path.startsWith('/current?')&&!result.call&&await hasDevice(this.ctx)){
      try{
        const remote=await operateLocal(this.ctx,'call_current',{device:this.device});
        if(remote?.call){const call=remote.call;this.federationRoutes??=new Map();this.federationRoutes.set(call.id,call.federation);this.federationProofs??=new Map();while(this.federationRoutes.size>8){const oldest=this.federationRoutes.keys().next().value;this.federationRoutes.delete(oldest);this.federationProofs.delete(oldest);}const proofs=this.federationProofs.get(call.id)||new Set();await verifyCallActions(this.ctx,call,proofs);this.federationProofs.set(call.id,proofs);return remote;}
      }catch(error){if(![403,404].includes(error.status))throw error;}
    }
    return result;
  }
  async startFederated(contact){
    if(this.disposed||this.call||this.starting||this.externalBusy){this.ctx.toast(tText("chat:firstEndTheCurrentCall"));return;}
    try{await ensureDevice(this.ctx);const config=await operateLocal(this.ctx,'call_config',{});if(!config.available)throw new Error(tText("chat:interServerCallsAreCurrentlyUnavailablePleaseCheck"));this.configuration=config;this.setAvailable(true);this.nextFederation=contact;await this.start('federation:'+contact.peer_id+':'+contact.actor_id);}catch(error){this.nextFederation=null;this.ctx.toast(error.message);}
  }
  async refreshAvailability(force=false){
    if(this.disposed||this.checkingAvailability||document.hidden)return;this.checkingAvailability=true;const controller=new AbortController();this.availabilityAbort=controller;
    try{const config=await this.api('/config','GET',undefined,controller.signal);if(this.disposed)return;const guest=this.configuration?.guest_available;this.configuration=config;this.setAvailable(config.available===true);if(guest!==config.guest_available)document.dispatchEvent(new Event('drg-call-availability'));if(this.available)this.poll();}catch{if(!this.disposed&&!controller.signal.aborted){this.configuration=null;this.setAvailable(false);}}finally{this.checkingAvailability=false;}
  }
  setAvailable(value){if(this.available===value)return;this.available=value;document.dispatchEvent(new Event('drg-call-availability'));}
  async poll(force=false){
    if(!this.call&&document.querySelector('[data-guest-active]'))return;
    if(this.call&&!callLive(this.call)&&this.call.status!=='preparing')return;
    if((!this.available&&!this.call)||this.disposed||this.polling||this.starting||this.accepting||this.ending||document.hidden&&!this.call&&!force)return;
    if(!this.call&&Date.now()-(this.lastPoll||0)<1500)return;this.lastPoll=Date.now();
    this.polling=true;const seq=this.sequence;
    try{
      const {call}=await this.api('/current?device='+this.device+(this.call?'&call_id='+this.call.id:''));
      if(this.disposed||seq!==this.sequence)return;
      if(!call){if(this.call)this.close();return;}
      if(this.call?.id===call.id&&!callLive(call)){this.call=call;this.draw();this.release();this.scheduleClose();return;}
      // Calls claimed by a different tab/device never capture this microphone.
      if(!call.owned&&!(call.status==='ringing'&&!call.outgoing)){if(this.call?.id===call.id)this.close();return;}
      if(this.call&&this.call.id!==call.id)return;
      this.call=call;this.draw();
      if(!callLive(call)){this.release();this.scheduleClose();return;}
      if(call.owned&&this.pc){
        if(call.sdp&&!this.pc.remoteDescription){await this.pc.setRemoteDescription({type:call.outgoing?'answer':'offer',sdp:call.sdp});}
        if(this.pc.remoteDescription)for(const item of call.candidates||[]){const key=JSON.stringify(item);if(!this.remoteCandidates.has(key)){await this.pc.addIceCandidate(item);this.remoteCandidates.add(key);}}
        if(Date.now()-(this.lastSignal||0)>10000||this.candidates.length)await this.signal();
      }
    }catch(error){if(!this.disposed&&this.call){if([401,403,404,409].includes(error.status)){this.close();this.ctx.toast(error.message);}else{this.networkAt??=Date.now();if(Date.now()-this.networkAt>30000)this.end('failed');else this.status(tText("chat:checkingConnection"));}}}
    finally{this.polling=false;}
  }
  async media(){
    if(!navigator.mediaDevices?.getUserMedia||!window.RTCPeerConnection)throw new Error(tText("chat:aBrowserWithWebrtcSupportAndAccessTo"));
    const seq=this.sequence,controller=new AbortController();this.mediaAbort=controller;
    const releaseSession=acquireCallAudioSession(this.audioSession);this.releaseAudioSession=releaseSession;
    const request=navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    // A dismissed permission prompt cannot keep the call busy forever. A late
    // grant is stopped, including when the user already pressed hang up.
    request.then(stream=>{if(this.disposed||seq!==this.sequence)stream.getTracks().forEach(t=>t.stop());}).catch(()=>{});
    try{const stream=await abortable(()=>request,{signal:controller.signal,timeoutMs:25000,message:tText("chat:microphoneIsNotRespondingPleaseCheckYourBrowser")});if(this.disposed||seq!==this.sequence){stream.getTracks().forEach(t=>t.stop());throw new Error(tText("chat:callCancelled"));}this.stream=stream;
      const outputs=this.outputs;
      try{await abortable(()=>phoneCallAudioUI()?outputs.initializePhone():outputs.refresh(),{signal:controller.signal,timeoutMs:3000,message:tText('chat:couldNotRetrieveSpeakerListPleaseRetry')});}catch{if(controller.signal.aborted)throw new DOMException('Call cancelled','AbortError');}
      if(this.disposed||seq!==this.sequence)throw new DOMException('Call cancelled','AbortError');
      return stream;}
    catch(error){releaseSession();if(this.releaseAudioSession===releaseSession)this.releaseAudioSession=null;if(seq===this.sequence)this.sequence++;if(error.name==='NotAllowedError')throw new Error(tText("chat:allowMicrophoneAccessInYourBrowserSettingsTo"));if(error.name==='NotFoundError')throw new Error(tText("chat:microphoneNotFoundConnectItAndRetryThe"));throw error;}
  }
  async peer(stream){
    const seq=this.sequence;const config=await this.api('/config');if(this.disposed||!this.call||seq!==this.sequence)throw new Error(tText("chat:callCancelled"));if(!config.available)throw new Error(tText("chat:theCallServerIsUnavailablePleaseTryAgain"));
    this.pc=new RTCPeerConnection({iceServers:config.iceServers,bundlePolicy:'max-bundle'});this.relay=config.relay_configured;const pc=this.pc;
    this.audioDiagnostics=new CallAudioDiagnostics(pc,stream.getAudioTracks()[0],()=>{if(this.qualityOpen)this.drawOutputs();});
    for(const track of stream.getTracks()){pc.addTrack(track,stream);track.onended=()=>{if(this.pc===pc)this.end('failed');};}
    pc.onicecandidate=event=>{if(this.pc===pc&&event.candidate){this.candidates.push(event.candidate.toJSON());if(this.call?.owned)this.signal().catch(()=>{});}};
    pc.ontrack=event=>{if(this.pc!==pc)return;if(event.track.kind==='video'){this.remoteVideo??=new Map();this.remoteVideo.set(event.transceiver,new MediaStream([event.track]));this.draw();return;}this.audio.srcObject=new MediaStream([event.track]);this.resumeRemoteAudio(pc);};
    pc.onconnectionstatechange=()=>{
      if(this.pc!==pc)return;
      if(pc.connectionState==='connected'){clearTimeout(this.disconnectTimer);this.networkAt=null;this.connected=true;this.signal().catch(()=>{});this.draw();}
      if(pc.connectionState==='failed')this.end('failed');
      if(pc.connectionState==='disconnected'){this.status(tText("chat:restoringAudio"));clearTimeout(this.disconnectTimer);this.disconnectTimer=setTimeout(()=>{if(this.pc===pc&&pc.connectionState==='disconnected')this.end('failed');},20000);}
    };
    return pc;
  }
  async start(sid){
    if(this.disposed)return;
    if(!this.available){this.ctx.toast(tText("chat:theCallServerIsUnavailablePleaseTryAgain"));return;}
    if(this.call||this.starting||this.externalBusy||document.querySelector('[data-guest-active]')){this.ctx.toast(tText("chat:firstEndTheCurrentCall"));return;}
    this.starting=true;clearTimeout(this.closeTimer);const remote=this.nextFederation;this.nextFederation=null;this.call={id:crypto.randomUUID(),space_id:sid,outgoing:true,status:'preparing',person:remote?{id:remote.actor_id,login:remote.login||remote.actor_id,display_name:remote.display_name}:this.ctx.state.spaces.find(s=>s.id===sid)?.members.find(p=>p.id!==this.uid)||{login:tText("chat:caller")},...(remote?{federation:{home_server_id:remote.peer_id,home_actor_id:remote.actor_id,peer_id:remote.peer_id,actor_id:remote.actor_id}}:{})};if(remote){this.federationRoutes??=new Map();this.federationRoutes.set(this.call.id,this.call.federation);while(this.federationRoutes.size>8){const oldest=this.federationRoutes.keys().next().value;this.federationRoutes.delete(oldest);this.federationProofs?.delete(oldest);}}this.draw();const id=this.call.id,seq=this.sequence;
    try{
      const stream=await this.media();if(this.call?.id!==id||seq!==this.sequence)return;const pc=await this.peer(stream);this.visualSlots=['camera','screen'].map(()=>pc.addTransceiver('video',{direction:'sendrecv'}));await pc.setLocalDescription(await pc.createOffer());if(this.call?.id!==id||seq!==this.sequence)return;
      this.submitted=true;const saved=await this.api('','POST',{id,space_id:sid,device:this.device,sdp:pc.localDescription.sdp});
      if(this.disposed||this.call?.id!==id||seq!==this.sequence){this.api('/'+id+'/end','POST',{reason:'hangup'}).catch(()=>{});return;}
      this.call=saved;this.draw();await this.signal();
    }catch(error){if(error.name==='AbortError')return;if(this.call?.id===id){this.ctx.toast(error.message);await this.end('failed');}}
    finally{this.starting=false;if(!this.disposed)this.poll();}
  }
  async accept(){
    if(!this.call||this.accepting)return;this.accepting=true;const id=this.call.id,seq=this.sequence;this.status(tText("chat:connectingMicrophone"));
    try{
      // Called directly from the user's click to unlock Safari audio playback.
      this.audio.play().catch(()=>{});const stream=await this.media();if(this.call?.id!==id||seq!==this.sequence)return;
      const call=await this.api('/'+id+'/accept','POST',{device:this.device});if(this.call?.id!==id||seq!==this.sequence){this.api('/'+id+'/end','POST',{reason:'hangup'}).catch(()=>{});return;}
      this.call=call;const pc=await this.peer(stream);await pc.setRemoteDescription({type:'offer',sdp:call.sdp});this.visualSlots=pc.getTransceivers().filter(t=>t.receiver.track.kind==='video');this.visualSlots.forEach(t=>t.direction='sendrecv');await pc.setLocalDescription(await pc.createAnswer());if(this.call?.id!==id||seq!==this.sequence)return;
      await this.signal(pc.localDescription.sdp);this.draw();
    }catch(error){if(error.name==='AbortError')return;if(this.call?.id===id){this.ctx.toast(error.message);if(error.status===409)this.close();else await this.end('failed');}}
    finally{this.accepting=false;}
  }
  async signal(sdp){
    if(sdp)this.pendingSdp=sdp;
    if(this.signaling||!this.call?.owned||!this.pc||this.disposed)return;
    const id=this.call.id,seq=this.sequence,batch=this.candidates.splice(0),description=this.pendingSdp;this.mediaDirty=false;this.signaling=true;
    try{
      const saved=await this.api('/'+id+'/signal','POST',{device:this.device,candidates:batch,connected:!!this.connected,media:{muted:!!this.muted,camera:!!this.cameraStream,screen:!!this.screenStream},...(description?{sdp:description}:{})});
      if(this.call?.id===id&&seq===this.sequence&&!this.disposed){if(this.pendingSdp===description)this.pendingSdp=null;this.lastSignal=Date.now();this.networkAt=null;this.call=saved;this.draw();}
    }catch(error){if(this.call?.id===id&&seq===this.sequence)this.candidates.unshift(...batch);throw error;}
    finally{this.signaling=false;if((this.pendingSdp||this.mediaDirty)&&this.call?.id===id&&!this.disposed)setTimeout(()=>this.signal().catch(()=>{}),1000);}
  }
  async end(reason='hangup'){
    const call=this.call;if(!call||this.ending)return;this.ending=true;this.release();
    try{if(call.status!=='preparing'||this.submitted)await this.api('/'+call.id+'/end','POST',{reason});}
    catch(error){if(!this.disposed)this.ctx.toast(tText("chat:audioIsMutedTheServerWillAutomaticallyDisconnect"));}
    finally{this.ending=false;if(this.call?.id===call.id){this.call={...call,status:reason==='failed'?'failed':reason==='decline'?'declined':'ended',ended:Date.now()/1000};this.draw();this.scheduleClose();document.dispatchEvent(new Event('drg-call-change'));}}
  }
  status(text){const el=this.panel?.querySelector('[data-call-status]');if(el)el.textContent=text;}
  tick(){if(this.call?.status==='active'&&this.connected)this.status(callDuration(Date.now()/1000-this.call.connected));if(this.call?.status==='ringing'&&!this.call.outgoing&&!this.accepting&&this.ctx.state.me.settings?.notifications?.sound!==false&&Date.now()-(this.lastRing||0)>3000){this.lastRing=Date.now();ring();}}
  async toggleVisual(kind){
    const field=kind+'Stream',slot=this.visualSlots?.[kind==='camera'?0:1],pc=this.pc,id=this.call?.id,seq=this.sequence;
    if(this.visualBusy||!pc||!callLive(this.call))return;
    if(!slot){this.ctx.toast(tText("chat:forVideoAndScreenSharingStartANew"));return;}
    if(kind==='screen'&&!navigator.mediaDevices?.getDisplayMedia){this.ctx.toast(tText("chat:thisBrowserDoesNotSupportScreenSharingOpen"));return;}
    this.visualBusy=true;this.draw();
    try{
      if(this[field]){await slot.sender.replaceTrack(null);this[field].getTracks().forEach(t=>t.stop());this[field]=null;}
      else{
        // Native picker/camera prompt must begin inside this click, before any await.
        const request=kind==='screen'?navigator.mediaDevices.getDisplayMedia({video:{frameRate:{ideal:15,max:30}},audio:false}):navigator.mediaDevices.getUserMedia({audio:false,video:{width:{ideal:1280},height:{ideal:720},frameRate:{ideal:24,max:30},facingMode:'user'}});
        const controller=new AbortController();this.visualAbort=controller;
        request.then(stream=>{if(this.disposed||seq!==this.sequence||controller.signal.aborted)stream.getTracks().forEach(t=>t.stop());}).catch(()=>{});
        const stream=await abortable(()=>request,{signal:controller.signal,timeoutMs:60000,message:tText("chat:videoSourceSelectionWasNotCompletedPleaseRetry")});
        if(this.pc!==pc||this.call?.id!==id||seq!==this.sequence){stream.getTracks().forEach(t=>t.stop());return;}
        try{await slot.sender.replaceTrack(stream.getVideoTracks()[0]);}catch(error){stream.getTracks().forEach(t=>t.stop());throw error;}
        if(this.pc!==pc||seq!==this.sequence){stream.getTracks().forEach(t=>t.stop());return;}
        this[field]=stream;
        stream.getVideoTracks()[0].onended=()=>{if(this[field]===stream){this[field]=null;slot.sender.replaceTrack(null).catch(()=>{});stream.getTracks().forEach(t=>t.stop());this.changedMedia();}};
      }
      this.changedMedia();
    }catch(error){this.visualAbort?.abort();if(this.pc===pc&&kind==='camera'&&error.name==='NotAllowedError')this.ctx.toast(tText("chat:allowCameraAccessInYourBrowserSettings"));if(this.pc===pc&&error.name!=='AbortError'&&error.name!=='NotAllowedError')this.ctx.toast(error.name==='NotFoundError'?tText("chat:cameraNotFoundConnectItAndRetry"):error.name==='NotReadableError'?tText("chat:theCameraIsInUseByAnotherApplication"):error.message);}
    finally{if(seq===this.sequence){this.visualAbort=null;this.visualBusy=false;this.draw();}}
  }
  createAudioReceiver(){
    this.audio=document.createElement('audio');this.audio.autoplay=false;this.audio.setAttribute('playsinline','');this.audio.hidden=true;document.body.append(this.audio);
    const outputs=new CallAudioOutput(this.audio,navigator.mediaDevices,{changed:()=>{if(this.outputs===outputs)this.drawOutputs();}});this.outputs=outputs;
  }
  qualityReport(){return {...(this.audioDiagnostics?.report()||{}),audioOutput:this.outputs.diagnostics(this.audioSession)};}
  async refreshAudioOutput(){
    try{await this.outputs.refresh();}catch{this.audioToolStatus=tText('chat:couldNotRetrieveSpeakerListPleaseRetry');this.drawOutputs();}
    if(this.audio.srcObject&&(this.audio.paused||this.audioBlocked))await this.resumeRemoteAudio();
  }
  audioSessionChanged(){
    if(!this.stream||!this.pc)return;
    if(this.audioSession?.state==='interrupted'){
      this.audioBlocked=true;this.sessionInterrupted=true;this.audioToolStatus=tText('chat:audioSessionInterrupted');this.draw();
    }else if(this.audioSession?.state==='active'&&this.audioBlocked)this.resumeRemoteAudio();
  }
  resumeRemoteAudio(pc=this.pc,{prepare=true}={}){
    if(!pc||pc!==this.pc||!this.audio.srcObject)return Promise.resolve(false);
    if(this.resumeTask)return this.resumeTask;
    const seq=this.sequence;
    const play=async()=>{
      if(prepare){try{await this.outputs.prepare(this.audio);}catch{if(seq===this.sequence&&this.pc===pc){this.audioBlocked=true;this.outputOpen=!phoneCallAudioUI();this.draw();}return false;}}
      if(seq!==this.sequence||this.pc!==pc)return false;
      try{
        await this.audio.play();if(seq!==this.sequence||this.pc!==pc)return false;
        this.audioBlocked=false;if(this.sessionInterrupted){this.audioToolStatus='';this.sessionInterrupted=false;}this.draw();return true;
      }catch{if(seq===this.sequence&&this.pc===pc){this.audioBlocked=true;this.draw();}return false;}
    };
    const pending=play().finally(()=>{if(this.resumeTask===pending)this.resumeTask=null;});this.resumeTask=pending;return pending;
  }
  async toggleSpeakerphone(){
    const seq=this.sequence;this.outputOpen=false;this.drawOutputs();
    try{const ok=await this.outputs.togglePhoneSpeaker();if(ok&&seq===this.sequence&&this.audio.srcObject)await this.resumeRemoteAudio(this.pc,{prepare:false});}catch{}
    if(seq===this.sequence)this.drawOutputs();
  }
  async openOutputs(){
    if(!this.panel)return;this.outputOpen=!this.outputOpen;if(this.outputOpen)this.audioSettingsOpen=false;this.drawOutputs();if(!this.outputOpen)return;
    try{await this.outputs.refresh();}catch{this.ctx.toast(tText("chat:couldNotRetrieveSpeakerListPleaseRetry"));}
  }
  drawOutputs(){
    const menu=this.panel?.querySelector('[data-call-outputs]'),button=this.panel?.querySelector('[data-call-output]');if(!menu||!button)return;
    const phone=phoneCallAudioUI(),speaker=this.outputs.phoneRoute==='speaker',headphones=this.panel.querySelector('[data-call-devices]');
    headphones.hidden=!phone||button.hidden;headphones.setAttribute('aria-expanded',String(!!this.outputOpen));
    button.disabled=phone&&this.outputs.busy;button.querySelector('span').textContent=tText(phone?'chat:speakerphone':'chat:speaker3');
    button.setAttribute('aria-label',tText(phone?(speaker?'chat:disableSpeakerphone':'chat:enableSpeakerphone'):'chat:speakerAndSpeakerphone'));
    if(phone){button.setAttribute('aria-pressed',String(speaker));button.removeAttribute('aria-haspopup');button.removeAttribute('aria-expanded');}
    else{button.removeAttribute('aria-pressed');button.setAttribute('aria-haspopup','menu');button.setAttribute('aria-expanded',String(!!this.outputOpen));}
    const status=this.panel.querySelector('[data-call-route-status]');status.hidden=!phone||button.hidden||!this.outputs.error;status.textContent=this.outputs.error;
    menu.hidden=!this.outputOpen;if(!this.outputOpen)return;
    const supported=this.outputs.supported,busy=this.outputs.busy,items=this.outputs.items.filter(d=>!phone||phoneOutputRole(d)!=='speaker'&&(phoneOutputRole(d)!=='system'||!this.outputs.phoneRoutes.receiver)),tools=!phone||this.audioSettingsOpen,track=this.stream?.getAudioTracks()[0],gain=microphoneSettings(track).autoGainControl;
    renderMarkup(menu,`<strong>${tHtml(phone?"chat:audioDevices":"chat:audioOutput")}</strong>${supported?items.map(d=>`<button type="button" role="menuitemradio" aria-checked="${d.deviceId===this.outputs.current}" data-output-id="${esc(d.deviceId)}" ${busy?'disabled':''}>${d.deviceId===this.outputs.current?chatIcon('check'):''}${esc(phone&&phoneOutputRole(d)==='receiver'?tText('chat:phoneReceiver'):d.label)}</button>`).join(''):''}${supported&&navigator.mediaDevices?.selectAudioOutput?`<button type="button" data-choose-output ${busy?'disabled':''}>${tHtml("chat:chooseAnotherDevice")}</button>`:''}${!supported||this.outputs.items.length<=1?(supported?`<p>${tHtml("chat:onlyTheSystemAudioOutputIsAvailableCheck")}</p>`:`<p>${tHtml("chat:thisBrowserDoesNotSupportSelectingASpeaker")}</p>`):''}${phone?`<button type="button" data-audio-settings aria-expanded="${!!this.audioSettingsOpen}">${tHtml('chat:audioSettings')}</button>`:''}${tools?`<div class="voice-call-audio-tools"><label><input type="checkbox" data-call-auto-gain ${gain===true?'checked':''} ${!microphoneGainAvailable(track)||this.gainBusy?'disabled':''}> ${tHtml("chat:automaticMicrophoneVolume")}</label><small>${tHtml("chat:ifTheVolumeSuddenlyIncreasesTryMutingOn")}</small><button type="button" data-call-quality aria-expanded="${!!this.qualityOpen}">${tHtml("chat:callQuality")}</button>${this.qualityOpen?`<pre data-call-quality-report>${esc(audioQualityText(this.qualityReport()))}</pre><button type="button" data-call-copy-quality>${tHtml("chat:copyDiagnostics")}</button>`:''}</div>`:''}<p data-output-status role="status">${esc(busy?tText('chat:applyingAudioOutput'):this.outputs.error||this.audioToolStatus||'')}</p><button type="button" data-output-close>${tHtml("chat:close")}</button>`);
    const apply=async action=>{const seq=this.sequence;try{const ok=await action();if(ok&&seq===this.sequence&&this.panel){this.outputOpen=false;this.drawOutputs();if(this.audio.srcObject)await this.resumeRemoteAudio(this.pc,{prepare:false});}}catch{if(seq===this.sequence)this.drawOutputs();}};
    menu.querySelectorAll('[data-output-id]').forEach(el=>el.onclick=()=>apply(()=>this.outputs.select(el.dataset.outputId)));
    const settings=menu.querySelector('[data-audio-settings]');if(settings)settings.onclick=()=>{this.audioSettingsOpen=!this.audioSettingsOpen;this.drawOutputs();};
    const gainControl=menu.querySelector('[data-call-auto-gain]');if(gainControl)gainControl.checked=gain===true;
    if(gainControl)gainControl.onchange=async event=>{
      const enabled=event.target.checked,seq=this.sequence;this.gainBusy=true;this.audioToolStatus=tText("chat:applyingMicrophoneSettings");this.drawOutputs();
      try{await setMicrophoneGain(track,enabled);if(seq===this.sequence)this.audioToolStatus=tText("chat:automaticVolume", {value0:(enabled?tText("chat:enabled"):tText("chat:disabled"))});}catch(error){if(seq===this.sequence)this.audioToolStatus=error.message;}finally{if(seq===this.sequence){this.gainBusy=false;this.drawOutputs();}}
    };
    const quality=menu.querySelector('[data-call-quality]');if(quality)quality.onclick=()=>{this.qualityOpen=!this.qualityOpen;this.drawOutputs();};
    const copy=menu.querySelector('[data-call-copy-quality]');if(copy)copy.onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(this.qualityReport(),null,2));this.audioToolStatus=tText("chat:diagnosticsCopiedNoCallRecordingsArePresent");}catch{this.audioToolStatus=tText("chat:couldNotCopyDataIsAvailableAbove");}this.drawOutputs();};
    const choose=menu.querySelector('[data-choose-output]');if(choose)choose.onclick=()=>apply(()=>this.outputs.choose());menu.querySelector('[data-output-close]').onclick=()=>{this.outputOpen=false;this.drawOutputs();};
  }
  changedMedia(){this.mediaDirty=true;this.draw();this.signal().catch(()=>{});}
  draw(){
    if(this.disposed||!this.call)return;const call=this.call;
    if(!this.panel){
      if(window.matchMedia('(max-width:760px), (max-height:500px) and (hover:none) and (pointer:coarse)').matches)document.activeElement?.blur?.();
      this.panel=document.createElement('section');this.panel.className='voice-call';this.panel.setAttribute('role','dialog');this.panel.setAttribute('aria-label',tText("chat:call2"));
      this.panel.innerHTML=`<div class="voice-call-top"><span class="voice-call-kind">${tHtml("chat:privateCall")}</span><div><button data-call-devices aria-label="${tHtml('chat:headphonesAndDevices')}" aria-haspopup="menu" aria-expanded="false" hidden>${chatIcon('headphones')}</button><button data-call-expand aria-label="${tHtml("chat:fullScreen")}" title="${tHtml("chat:fullScreen")}">${chatIcon('expand')}</button><button data-call-minimize aria-label="${tHtml("chat:endCall")}" title="${tHtml("chat:endCall")}">${chatIcon('minimize')}</button></div></div><video data-call-remote autoplay playsinline muted hidden></video><div class="voice-call-identity">${personAvatar(call.person)}<h2>${esc(personName(call.person))}</h2><p data-call-status role="status"></p></div><video data-call-local autoplay playsinline muted hidden></video><div class="voice-call-bottom"><div class="voice-call-badges" aria-live="polite"><span data-peer-muted hidden>${tHtml("chat:speakerIsMuted", {value0:(chatIcon('micOff'))})}</span><span data-self-muted hidden>${tHtml("chat:youHaveMutedTheSpeaker", {value0:(chatIcon('micOff'))})}</span><span data-self-screen hidden>${tHtml("chat:youAreShowingYourScreen", {value0:(chatIcon('screen'))})}</span></div><div class="voice-call-controls"><button class="call-answer" data-call-answer aria-label="${tHtml("chat:accept")}">${chatIcon('phone')}<span>${tHtml("chat:accept")}</span></button><button data-call-camera aria-label="${tHtml("chat:video")}" aria-pressed="false">${chatIcon('video')}<span>${tHtml("chat:video")}</span></button><button data-call-screen aria-label="${tHtml("chat:screen")}" aria-pressed="false">${chatIcon('screen')}<span>${tHtml("chat:screen")}</span></button><button data-call-output aria-label="${tHtml("chat:speakerAndSpeakerphone")}" aria-haspopup="menu" aria-expanded="false">${chatIcon('speaker')}<span>${tHtml("chat:speaker3")}</span></button><button data-call-mute aria-pressed="false"></button><button data-call-audio aria-label="${tHtml("chat:enableAudio")}" hidden>${chatIcon('phone')}<span>${tHtml("chat:enableAudio")}</span></button><button class="call-end" data-call-end>${chatIcon('close')}<span>${tHtml("chat:endSession")}</span></button><button data-call-close aria-label="${tHtml("chat:close")}">${chatIcon('close')}<span>${tHtml("chat:close")}</span></button></div><p class="voice-call-route-status" data-call-route-status role="status" hidden></p><div class="voice-call-outputs" data-call-outputs role="menu" aria-label="${tHtml("chat:audioOutput")}" hidden></div><p class="voice-call-hint" data-call-hint hidden></p></div>`;
      document.body.append(this.panel);
      $('[data-call-answer]',this.panel).onclick=()=>this.accept();$('[data-call-end]',this.panel).onclick=()=>this.end(this.call?.status==='ringing'&&!this.call.outgoing?'decline':'hangup');$('[data-call-close]',this.panel).onclick=()=>this.close();
      $('[data-call-mute]',this.panel).onclick=()=>{this.muted=!this.muted;this.stream?.getAudioTracks().forEach(t=>t.enabled=!this.muted);this.changedMedia();};
      $('[data-call-output]',this.panel).onclick=()=>phoneCallAudioUI()?this.toggleSpeakerphone():this.openOutputs();
      $('[data-call-devices]',this.panel).onclick=()=>this.openOutputs();
      $('[data-call-camera]',this.panel).onclick=()=>this.toggleVisual('camera');$('[data-call-screen]',this.panel).onclick=()=>this.toggleVisual('screen');
      $('[data-call-audio]',this.panel).onclick=()=>this.resumeRemoteAudio().then(ok=>{if(!ok)this.ctx.toast(tText("chat:theBrowserHasNotYetAllowedAudioPlayback"));});
      $('[data-call-minimize]',this.panel).onclick=async()=>{if(document.fullscreenElement===this.panel){try{await document.exitFullscreen();}catch{this.ctx.toast(tText("chat:couldNotExitFullScreenMode"));return;}}this.minimized=!this.minimized;if(!this.minimized&&window.matchMedia('(max-width:760px), (max-height:500px) and (hover:none) and (pointer:coarse)').matches)document.activeElement?.blur?.();this.draw();};
      $('[data-call-expand]',this.panel).onclick=async()=>{if(document.fullscreenElement===this.panel){try{await document.exitFullscreen();}catch{this.ctx.toast(tText("chat:couldNotExitFullScreenMode"));}return;}this.minimized=false;this.draw();if(!this.panel.requestFullscreen){this.ctx.toast(tText("chat:fullScreenModeIsNotAvailableInThis"));return;}try{await this.panel.requestFullscreen();}catch{this.ctx.toast(tText("chat:fullScreenModeIsNotAvailableInThis"));}this.draw();};
    }
    const live=callLive(call)||call.status==='preparing',incoming=call.status==='ringing'&&!call.outgoing,controls=live&&!incoming&&call.status!=='preparing';
    this.panel.classList.toggle('is-incoming',incoming);this.panel.classList.toggle('is-active',call.status==='active');this.panel.classList.toggle('is-minimized',!!this.minimized);
    const phase=call.status==='preparing'?tText("chat:connectingMicrophone"):call.status==='ended'?tText("chat:callEnded"):callLabel(call);this.status(phase);
    for(const [name,show] of Object.entries({answer:incoming,camera:controls,screen:controls&&!!navigator.mediaDevices?.getDisplayMedia,output:controls,mute:controls,audio:controls&&this.audioBlocked,end:live,close:!live}))$('[data-call-'+name+']',this.panel).hidden=!show;
    const mute=$('[data-call-mute]',this.panel);mute.setAttribute('aria-label',this.muted?tText("chat:enableMicrophone"):tText("chat:disableMicrophone"));mute.setAttribute('aria-pressed',!!this.muted);mute.innerHTML=chatIcon(this.muted?'micOff':'mic')+`<span>${this.muted?tText("chat:enableMicrophone"):tText("chat:disableMicrophone")}</span>`;
    for(const kind of ['camera','screen']){const button=$('[data-call-'+kind+']',this.panel);button.setAttribute('aria-pressed',!!this[kind+'Stream']);button.disabled=!!this.visualBusy;}
    $('[data-call-answer]',this.panel).disabled=!!this.accepting;
    const end=$('[data-call-end]',this.panel);end.setAttribute('aria-label',incoming?tText("chat:decline"):tText("chat:endSession"));end.querySelector('span').textContent=end.getAttribute('aria-label');const minimize=$('[data-call-minimize]',this.panel),miniLabel=this.minimized?tText("chat:answerCall"):tText("chat:endCall");minimize.setAttribute('aria-label',miniLabel);minimize.title=miniLabel;minimize.innerHTML=chatIcon(this.minimized?'restore':'minimize');
    const expand=$('[data-call-expand]',this.panel),fullscreen=document.fullscreenElement===this.panel,expandLabel=fullscreen?tText("chat:exitFullScreen"):tText("chat:fullScreen");expand.setAttribute('aria-label',expandLabel);expand.title=expandLabel;expand.innerHTML=chatIcon(fullscreen?'contract':'expand');
    $('[data-self-muted]',this.panel).hidden=!controls||!this.muted;$('[data-peer-muted]',this.panel).hidden=!controls||!call.media?.muted;$('[data-self-screen]',this.panel).hidden=!this.screenStream;
    const videos=this.pc?.getTransceivers().filter(t=>t.receiver.track.kind==='video')||[],remoteKind=call.media?.screen?1:call.media?.camera?0:-1,remote=remoteKind>=0?this.remoteVideo?.get(videos[remoteKind]):null;
    const local=this.cameraStream||this.screenStream||null,remoteEl=$('[data-call-remote]',this.panel),localEl=$('[data-call-local]',this.panel);
    for(const [el,stream] of [[remoteEl,remote],[localEl,local]]){if(el.srcObject!==stream){el.srcObject=stream;if(stream)el.play().catch(()=>{});}el.hidden=!stream;}
    remoteEl.classList.toggle('is-screen',remoteKind===1);localEl.classList.toggle('is-screen',!this.cameraStream);this.panel.classList.toggle('has-video',!!remote);
    const hint=$('[data-call-hint]',this.panel);hint.hidden=call.status!=='failed';hint.textContent=tText("chat:checkYourNetworkAndMicrophoneCameraPermissions");this.drawOutputs();this.tick();
  }
  scheduleClose(){clearTimeout(this.closeTimer);this.closeTimer=setTimeout(()=>this.close(),6000);}
  release(){this.resumeTask=null;this.sessionInterrupted=false;this.audioBlocked=false;this.outputOpen=false;this.qualityOpen=false;this.gainBusy=false;this.audioToolStatus='';this.audioDiagnostics?.stop();this.audioDiagnostics=null;this.outputs.reset();this.sequence++;this.visualAbort?.abort();this.visualAbort=null;this.visualBusy=false;for(const field of ['cameraStream','screenStream']){this[field]?.getTracks().forEach(t=>t.stop());this[field]=null;}this.remoteVideo?.clear();this.visualSlots=null;this.mediaDirty=false;this.mediaAbort?.abort();this.mediaAbort=null;clearTimeout(this.disconnectTimer);const pc=this.pc;this.pc=null;pc?.close();this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;this.audio.srcObject=null;this.candidates=[];this.pendingSdp=null;this.remoteCandidates.clear();this.connected=false;this.muted=false;this.networkAt=null;this.lastRing=0;this.audio.pause();this.audio.remove();this.releaseAudioSession?.();this.releaseAudioSession=null;if(!this.disposed)this.createAudioReceiver();}
  close(){clearTimeout(this.closeTimer);this.release();this.call=null;this.minimized=false;this.panel?.remove();this.panel=null;this.drawKey=null;this.lastSignal=0;this.submitted=false;document.dispatchEvent(new Event('drg-call-change'));}
  destroy(){this.disposed=true;this.availabilityAbort?.abort();const call=this.call;if(callLive(call)&&call.owned)this.api('/'+call.id+'/end','POST',{reason:'hangup'}).catch(()=>{});this.close();clearInterval(this.timer);clearInterval(this.clock);clearInterval(this.availabilityTimer);document.removeEventListener('fullscreenchange',this.fullscreenChange);this.audio.remove();navigator.mediaDevices?.removeEventListener?.('devicechange',this.deviceChange);this.audioSession?.removeEventListener?.('statechange',this.sessionChange);window.removeEventListener('resize',this.outputLayoutChange);window.removeEventListener('focus',this.focus);window.removeEventListener('pagehide',this.pagehide);window.removeEventListener('beforeunload',this.beforeUnload);document.removeEventListener('visibilitychange',this.focus);document.removeEventListener('drg-inbox-change',this.inbox);}
}
