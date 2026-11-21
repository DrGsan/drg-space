import {tText,tHtml,locale} from "../../core/i18n.js";
import {$,esc} from '../../core/ui.js';
import {renderMarkup} from '../../core/dom.js';
import {abortable} from '../../core/request.js';
import {callsController} from './calls.js';
import {CallAudioOutput,phoneCallAudioUI,phoneOutputRole} from './audio-output.js';
import {acquireCallAudioSession} from './audio-session.js';
import {chatIcon} from './icons.js';
import {guestRoomAvatar,mountGuestRoomEditor} from './guest-room-appearance.js';
import './guest-calls.css';

let current;
const identity=()=>({id:crypto.randomUUID().replaceAll('-',''),nonce:crypto.randomUUID().replaceAll('-','')+crypto.randomUUID().replaceAll('-','')});
const until=room=>new Date(room.expires*1000).toLocaleString(locale(),{day:'numeric',month:'long',hour:'2-digit',minute:'2-digit'});
const loadLiveKit=()=>import('./livekit-room.js').catch(()=>{throw new Error(tText('chat:callModuleLoadFailed'));});
async function publicApi(path,method='GET',data,credential,signal){
  return abortable(async bounded=>{
    const response=await fetch('/api/guest-calls/'+path,{method,credentials:'omit',cache:'no-store',signal:bounded,headers:{'Accept-Language':locale(),'Content-Type':'application/json',...(credential?{Authorization:'Bearer '+credential}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})})
      .catch(error=>{if(error.name==='TypeError')throw new Error(tText("chat:unableToConnectToTheServerPleaseCheck"));throw error;});
    let body;try{body=await response.json();}catch{throw Object.assign(new Error(tText("chat:theCallServiceIsTemporarilyUnavailablePleaseTry")),{status:response.status});}
    if(!response.ok)throw Object.assign(new Error(body.detail||tText("chat:failedToConnectToTheCall")),{status:response.status});return body;
  },{signal,timeoutMs:12000,message:tText("chat:serverDidNotRespondPleaseCheckYourConnection")});
}
async function copyLink(url,status){try{await navigator.clipboard.writeText(url);status.textContent=tText("chat:linkCopied");}catch{status.textContent=tText("chat:selectAndCopyTheLinkFromTheField");}}
export async function guestInvitation(ctx,room,{message='',createId=identity().id}={}){
  const overlay=ctx.modal(`<div data-guest-invitation><h2>${tHtml("chat:guestCall")}</h2><p role="status">${tHtml("chat:creatingInvitation")}</p></div>`),dialog=$('[data-guest-invitation]',overlay);
  const abort=new AbortController(),close=()=>overlay.querySelector('.modal-close')?.click();
  overlay.querySelector('.modal-close')?.addEventListener('click',()=>abort.abort(),{once:true});
  try{
    room??=await ctx.api('/calls/guest-rooms','POST',{id:createId,transport:'livekit'},true,{}, {signal:abort.signal,timeoutMs:12000});
    if(!dialog.isConnected)return;
    dialog.innerHTML=`<h2 data-guest-invite-title>${esc(room.title)}</h2><p class="hint">${tHtml("chat:sendTheLinkToParticipantsSimplyEnterYour")}</p><div data-guest-room-editor></div><div class="guest-invite"><label>${tHtml("chat:linkToTheCall")}<input data-guest-url readonly value="${esc(room.url)}" aria-label="${tHtml("chat:linkToTheCall")}"></label><button type="button" class="secondary" data-guest-copy>${chatIcon('copy')}<span>${tHtml("chat:copy")}</span></button></div><p class="hint">${tHtml("chat:upToParticipantsLinkIsValidUntil", {value0:(room.limit), value1:(esc(until(room)))})}</p><p class="hint guest-invite-status" data-guest-status role="status"></p><div class="guest-invite-actions"><button class="primary" data-guest-enter>${tHtml("chat:joinTheCall")}</button><button class="text-link" data-guest-end>${tHtml("chat:endInvitation")}</button></div>`;
    const editor=mountGuestRoomEditor($('[data-guest-room-editor]',dialog),ctx,room,{signal:abort.signal,saved:room=>{$('[data-guest-invite-title]',dialog).textContent=room.title;}});
    $('[data-guest-status]',dialog).textContent=room.join_disabled_reason||message;
    $('[data-guest-enter]',dialog).disabled=!!room.join_disabled_reason;
    $('[data-guest-url]',dialog).onclick=e=>e.target.select();
    $('[data-guest-copy]',dialog).onclick=()=>{const copy=()=>copyLink(room.url,$('[data-guest-status]',dialog));if(editor.dirty)editor.save().then(ok=>{if(ok&&dialog.isConnected)copy();});else copy();};
    $('[data-guest-enter]',dialog).onclick=async()=>{
      if(current||callsController.busy){ctx.toast(tText("chat:firstEndTheCurrentCall"));return;}
      if(!await editor.save()||!dialog.isConnected)return;
      close();startGuestCall({room,ctx,owner:true,
        join:(data,signal)=>ctx.api('/calls/guest-rooms/'+room.id+'/join','POST',data,true,{}, {signal,timeoutMs:12000}),
        failed:message=>{if(ctx.state.view==='chat')guestInvitation(ctx,room,{message});}});
    };
    $('[data-guest-end]',dialog).onclick=async e=>{e.currentTarget.disabled=true;try{await ctx.api('/calls/guest-rooms/'+room.id+'/end','POST');close();document.dispatchEvent(new Event('drg-guest-call-change'));}catch(error){ctx.toast(error.message);if(e.target.isConnected)e.target.disabled=false;}};
    document.dispatchEvent(new Event('drg-guest-call-change'));
  }catch(error){if(dialog.isConnected){
    dialog.innerHTML=`<h2>${tHtml("chat:failedToCreateInvitation")}</h2><p role="status">${esc(error.message)}</p><button type="button" class="primary" data-guest-create-retry>${tHtml("chat:retry")}</button><button type="button" class="secondary" data-guest-create-back>${tHtml("chat:toCalls")}</button>`;
    $('[data-guest-create-retry]',dialog).onclick=()=>{close();guestInvitation(ctx,room,{createId});};
    $('[data-guest-create-back]',dialog).onclick=close;
  }}
}

export async function mountGuestCall(host,token,{brand='',toast=()=>{}}={}){
  host.innerHTML=`<main class="guest-call-page"><a class="brand" href="/">${brand}</a><section class="auth-card"><h1>${tHtml("chat:guestCall")}</h1><p role="status">${tHtml("chat:verifyingInvitation")}</p></section></main>`;
  const card=$('.auth-card',host);
  try{
    const room=await publicApi(encodeURIComponent(token));
    card.innerHTML=`<div class="guest-lobby-icon" data-guest-lobby-avatar>${guestRoomAvatar(room)}</div><h1 data-guest-lobby-title>${esc(room.title)}</h1><p class="hint">${tHtml("chat:toParticipants", {value0:(room.count?(""+tText("chat:inTheRoom", {value0:(room.count)})+" "):''), value1:(room.limit)})}</p><form data-guest-name-form><label>${tHtml("chat:yourName")}<input name="name" maxlength="60" required autocomplete="given-name" placeholder="${tHtml("chat:howToAddressYou")}"></label><button class="primary" type="submit">${tHtml("chat:join")}</button></form><p class="hint">${tHtml("chat:linkIsValidUntil", {value0:(esc(until(room)))})}<br>${tHtml("chat:forTheCallMicrophoneAccessIsRequired")}</p><p data-guest-lobby-status role="status" class="hint"></p>`;
    const form=$('form',card);let expired=false;
    if(room.join_disabled_reason){form.hidden=true;$('[data-guest-lobby-status]',card).textContent=room.join_disabled_reason;}
    const expiry=setTimeout(()=>{expired=true;if(!current){form.hidden=true;$('[data-guest-lobby-status]',card).textContent=tText("chat:theInvitationSValidityPeriodHasExpired");}},Math.max(0,(room.expires-room.server_time)*1000));
    form.onsubmit=e=>{e.preventDefault();if(expired)return;if(current){current.panel?.focus();return;}
      const name=form.elements.name.value.trim();if(!name)return;form.elements.name.blur();
      startGuestCall({room:{...room,url:location.href},ctx:{toast},owner:false,
        roomChanged:updated=>{Object.assign(room,updated);if(card.isConnected){$('[data-guest-lobby-title]',card).textContent=room.title;renderMarkup($('[data-guest-lobby-avatar]',card),guestRoomAvatar(room));}},
        join:(data,signal)=>publicApi(encodeURIComponent(token)+'/join','POST',{...data,name},undefined,signal),
        finished:message=>{if(card.isConnected)$('[data-guest-lobby-status]',card).textContent=message||tText("chat:youHaveLeftTheCallYouCanReconnect");}});
    };
    window.addEventListener('pagehide',()=>clearTimeout(expiry),{once:true});
  }catch(error){card.innerHTML=`<h1>${tHtml("chat:invitationNotAvailable")}</h1><p class="muted">${esc(error.message)}</p><a href="/" class="secondary">${tHtml("chat:goToHome")}</a>`;}
}

function startGuestCall(options){if(current)return;current=new GuestCall(options);current.start();}
class GuestCall {
  constructor(options){
    Object.assign(this,options);callsController.setGuestBusy(true);this.peers=new Map();this.outgoing=new Map();this.identity=identity();this.abort=new AbortController();this.muted=false;this.closed=false;
    if(window.matchMedia('(max-width:760px), (max-height:500px) and (hover:none) and (pointer:coarse)').matches)document.activeElement?.blur?.();
    this.panel=document.createElement('section');this.panel.className='guest-call-panel';this.panel.dataset.guestActive='';this.panel.tabIndex=-1;this.panel.setAttribute('role','dialog');this.panel.setAttribute('aria-label',this.room.title);
    this.panel.innerHTML=`<header class="guest-call-top"><div class="guest-call-heading"><span data-guest-call-avatar>${guestRoomAvatar(this.room)}</span><div><strong data-guest-call-title>${esc(this.room.title)}</strong><small data-guest-expiry>${tHtml("chat:until", {value0:(esc(until(this.room)))})}</small></div></div><div class="guest-call-audio-actions"><button data-guest-devices aria-label="${tHtml('chat:headphonesAndDevices')}" aria-haspopup="menu" aria-expanded="false" hidden>${chatIcon('headphones')}</button><button class="chat-icon-button" aria-label="${tHtml("chat:endCall")}" data-guest-minimize>${chatIcon('minimize')}</button></div></header><div class="guest-call-people" role="list" aria-label="${tHtml("chat:callParticipants")}"></div><p class="guest-call-status" data-guest-call-status role="status">${tHtml("chat:connectingMicrophone")}</p><button class="text-link" data-guest-resume-audio hidden>${tHtml("chat:enableAudio")}</button><button class="text-link" data-guest-retry hidden>${tHtml("chat:reconnect")}</button><div class="guest-call-controls"><button data-guest-mute aria-label="${tHtml("chat:disableMicrophone")}" aria-pressed="false">${chatIcon('mic')}<span>${tHtml("chat:microphone")}</span></button><button data-guest-output aria-label="${tHtml("chat:audioOutput")}" aria-expanded="false">${chatIcon('speaker')}<span>${tHtml("chat:audio")}</span></button><button data-guest-share aria-label="${tHtml("chat:copyInvitation")}">${chatIcon('copy')}<span>${tHtml("chat:invite")}</span></button><button data-guest-leave class="guest-call-leave" aria-label="${tHtml("chat:endCall2")}">${chatIcon('hangup')}<span>${tHtml("chat:signOut")}</span></button></div><p class="guest-call-route-status" data-guest-route-status role="status" hidden></p><div data-guest-output-menu class="guest-output-menu" hidden></div>${this.owner?`<button class="text-link guest-end-all" data-guest-end-all>${tHtml("chat:endForAll")}</button>`:''}`;
    document.body.append(this.panel);
    this.sink=document.createElement('audio');this.outputs=new CallAudioOutput(this.sink,navigator.mediaDevices,{controlOnly:true,preferred:this.outputPreference,changed:()=>this.drawOutputs()});this.audioSession=navigator.audioSession;this.sessionChange=()=>this.audioSessionChanged();this.audioSession?.addEventListener?.('statechange',this.sessionChange);
    $('[data-guest-leave]',this.panel).onclick=()=>this.close();
    $('[data-guest-minimize]',this.panel).onclick=e=>{const small=this.panel.classList.toggle('is-minimized');if(!small&&window.matchMedia('(max-width:760px), (max-height:500px) and (hover:none) and (pointer:coarse)').matches)document.activeElement?.blur?.();e.currentTarget.setAttribute('aria-label',small?tText("chat:answerCall"):tText("chat:endCall"));e.currentTarget.innerHTML=chatIcon(small?'expand':'minimize');};
    $('[data-guest-share]',this.panel).onclick=()=>copyLink(this.room.url,$('[data-guest-call-status]',this.panel));
    $('[data-guest-mute]',this.panel).onclick=()=>{this.muted=!this.muted;this.stream?.getAudioTracks().forEach(t=>t.enabled=!this.muted);const button=$('[data-guest-mute]',this.panel);button.innerHTML=`${chatIcon(this.muted?'micOff':'mic')}<span>${tHtml("chat:microphone")}</span>`;button.setAttribute('aria-pressed',String(this.muted));button.setAttribute('aria-label',this.muted?tText("chat:enableMicrophone"):tText("chat:disableMicrophone"));this.sfu?.mute(this.muted).catch(()=>this.ctx.toast(tText('chat:livekitDisconnected')));this.drawPeople();this.schedule();};
    $('[data-guest-resume-audio]',this.panel).onclick=()=>this.resumeAudio();
    $('[data-guest-retry]',this.panel).onclick=async()=>{if(this.retrying)return;this.retrying=true;const options={room:this.room,ctx:this.ctx,owner:this.owner,join:this.join,finished:this.finished,failed:this.failed,roomChanged:this.roomChanged,outputPreference:this.outputs.selected};await this.close();startGuestCall(options);};
    $('[data-guest-output]',this.panel).onclick=()=>phoneCallAudioUI()?this.toggleSpeakerphone():this.openOutputs();
    $('[data-guest-devices]',this.panel).onclick=()=>this.openOutputs();
    this.outputLayoutChange=()=>this.drawOutputs();window.addEventListener('resize',this.outputLayoutChange);this.drawOutputs();
    $('[data-guest-end-all]',this.panel)?.addEventListener('click',async e=>{e.currentTarget.disabled=true;try{await this.ctx.api('/calls/guest-rooms/'+this.room.id+'/end','POST');this.close(tText("chat:callEnded2"));}catch(error){this.ctx.toast(error.message);if(e.target.isConnected)e.target.disabled=false;}});
    this.unload=()=>this.close();window.addEventListener('pagehide',this.unload);
    this.focus=()=>{this.sync();if(this.stream&&!document.hidden)this.refreshAudioOutput();};window.addEventListener('focus',this.focus);document.addEventListener('visibilitychange',this.focus);
    this.reset=()=>{if(this.owner)this.close();};document.addEventListener('drg-call-reset',this.reset);
    this.deviceChanged=()=>this.refreshAudioOutput();navigator.mediaDevices?.addEventListener('devicechange',this.deviceChanged);
  }
  async start(){
    try{
      if(!navigator.mediaDevices?.getUserMedia||!window.RTCPeerConnection)throw new Error(tText("chat:httpsAndABrowserWithMicrophoneAccessAre"));
      this.releaseAudioSession=acquireCallAudioSession(this.audioSession);
      const requested=navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      requested.then(stream=>{if(this.closed||this.abort.signal.aborted)stream.getTracks().forEach(t=>t.stop());}).catch(()=>{});
      this.stream=await abortable(()=>requested,{signal:this.abort.signal,timeoutMs:25000,message:tText("chat:microphoneIsNotRespondingCheckBrowserPermissions")});
      if(this.closed){this.stream.getTracks().forEach(t=>t.stop());return;}
      try{await abortable(()=>phoneCallAudioUI()?this.outputs.initializePhone():this.outputs.refresh(),{signal:this.abort.signal,timeoutMs:3000,message:tText('chat:couldNotRetrieveSpeakerListPleaseRetry')});}catch{}
      if(this.closed)return;
      this.stream.getAudioTracks().forEach(t=>{t.enabled=!this.muted;t.onended=()=>{if(!this.closed)this.close(tText("chat:microphoneIsDisabledReconnect"));};});
      let sfuModule;
      if(this.room.transport==='livekit'){
        this.status(tText('chat:connecting2'));
        sfuModule=await abortable(loadLiveKit,{signal:this.abort.signal,timeoutMs:30000,message:tText('chat:callModuleLoadFailed')});
        if(this.closed)return;
      }
      const joined=await this.join({...this.identity,...(this.room.transport==='livekit'?{transport:'livekit'}:{})},this.abort.signal);
      if(this.closed){publicApi('rooms/'+joined.room.id+'/leave','POST',{},joined.credential).catch(()=>{});return;}
      this.credential=joined.credential;this.self=joined.self;this.configuration=joined.configuration;
      if(joined.room.transport==='livekit'){
        const {LiveKitGuest}=sfuModule||await loadLiveKit();if(this.closed)return;
        this.sfu=new LiveKitGuest({outputs:this.outputs,changed:()=>{if(!this.closed){this.drawPeople();this.schedule();}},failed:message=>this.close(message),ended:()=>this.close(tText("chat:callEnded2")),playback:()=>{if(!this.closed)$('[data-guest-resume-audio]',this.panel).hidden=false;}});
        await abortable(()=>this.sfu.connect(joined.livekit,this.stream,this.muted),{signal:this.abort.signal,timeoutMs:30000,message:tText("chat:livekitDisconnected")});if(this.closed)return;
      }
      await this.apply(joined);await this.sync();if(this.closed)return;
      this.timer=setInterval(()=>this.sync(),this.sfu?15000:2000);this.clockTimer=setInterval(()=>this.tick(),1000);this.tick();
      document.dispatchEvent(new Event('drg-guest-call-change'));
    }catch(error){if(this.closed)return;const message=error.name==='NotAllowedError'?tText("chat:allowMicrophoneAccessInBrowserSettings"):error.name==='NotFoundError'?tText("chat:microphoneNotFound"):error.message;this.close(message);this.failed?.(message);if(!this.owner)this.ctx.toast(message);}
  }
  enqueue(id,description,candidate){
    if(this.closed)return;const pending=this.outgoing.get(id)||{peer_id:id,candidates:[]};if(description)pending.description=description;if(candidate&&pending.candidates.length<100)pending.candidates.push(candidate);this.outgoing.set(id,pending);this.schedule();
  }
  schedule(){if(!this.closed&&!this.signalTimer)this.signalTimer=setTimeout(()=>{this.signalTimer=0;this.sync();},150);}
  async sync(){
    if(this.closed||!this.credential)return;
    if(this.syncing){this.wanted=true;return;}this.syncing=true;
    const batch=[...this.outgoing.values()].map(s=>({...s,candidates:[...s.candidates]}));
    try{
      const result=await publicApi('rooms/'+this.room.id+'/sync','POST',{muted:this.muted,signals:batch},this.credential,this.abort.signal);
      if(this.closed)return;this.networkAt=null;
      for(const sent of batch){const pending=this.outgoing.get(sent.peer_id);if(!pending)continue;pending.candidates.splice(0,sent.candidates.length);if(pending.description===sent.description)delete pending.description;if(!pending.description&&!pending.candidates.length)this.outgoing.delete(sent.peer_id);}
      await this.apply(result);
    }catch(error){if(this.closed)return;if([401,403,404,410].includes(error.status)){this.close(error.message);return;}this.networkAt??=performance.now();if(performance.now()-this.networkAt>30000)this.close(tText("chat:connectionToServerLostReconnect"));else this.status(tText("chat:restoringConnection"));}
    finally{this.syncing=false;if(this.wanted&&!this.closed){this.wanted=false;this.schedule();}}
  }
  async apply(data){
    if(this.closed)return;this.room={...this.room,...data.room};this.clock={server:data.room.server_time,local:performance.now()};this.people=data.participants;
    const appearance=JSON.stringify([this.room.title,this.room.avatar_url]);
    if(appearance!==this.appearance){this.appearance=appearance;$('[data-guest-call-title]',this.panel).textContent=this.room.title;renderMarkup($('[data-guest-call-avatar]',this.panel),guestRoomAvatar(this.room));this.panel.setAttribute('aria-label',this.room.title);this.roomChanged?.(this.room);}
    if(this.room.transport==='livekit'){this.drawPeople();this.tick();return;}
    const ids=new Set(data.participants.filter(p=>p.id!==this.self).map(p=>p.id));
    for(const [id,peer] of this.peers)if(!ids.has(id)){this.drop(peer);this.peers.delete(id);this.outgoing.delete(id);}
    for(const id of ids){
      if(this.closed)return;
      try{
        let peer=this.peers.get(id);
        if(!peer){peer=this.createPeer(id);this.peers.set(id,peer);if(this.self<id){await peer.pc.setLocalDescription(await peer.pc.createOffer());if(this.closed)return;this.enqueue(id,peer.pc.localDescription.toJSON());}}
        const incoming=data.peers.find(p=>p.id===id);
        if(incoming?.description&&!peer.pc.remoteDescription){await peer.pc.setRemoteDescription(incoming.description);if(this.closed)return;if(incoming.description.type==='offer'){await peer.pc.setLocalDescription(await peer.pc.createAnswer());this.enqueue(id,peer.pc.localDescription.toJSON());}}
        if(peer.pc.remoteDescription)for(const candidate of incoming?.candidates||[]){const key=JSON.stringify(candidate);if(!peer.candidates.has(key)){await peer.pc.addIceCandidate(candidate);peer.candidates.add(key);}}
      }catch{const peer=this.peers.get(id);if(peer){peer.failed=true;this.drawPeople();$('[data-guest-retry]',this.panel).hidden=false;}}
    }
    this.drawPeople();this.tick();
  }
  createPeer(id){
    const pc=new RTCPeerConnection({iceServers:this.configuration.iceServers,bundlePolicy:'max-bundle'}),audio=document.createElement('audio');audio.autoplay=false;audio.setAttribute('playsinline','');audio.hidden=true;document.body.append(audio);
    this.outputs.add(audio);
    const peer={id,pc,audio,candidates:new Set()};
    for(const track of this.stream.getAudioTracks())pc.addTrack(track,this.stream);
    pc.onicecandidate=e=>{if(e.candidate&&!this.closed)this.enqueue(id,undefined,e.candidate.toJSON());};
    pc.ontrack=e=>{if(this.closed||e.track.kind!=='audio')return;audio.srcObject=new MediaStream([e.track]);this.playPeer(peer).then(ok=>{if(!this.closed&&!ok){$('[data-guest-resume-audio]',this.panel).hidden=false;if(this.outputs.error){this.outputOpen=!phoneCallAudioUI();this.drawOutputs();}}});};
    pc.onconnectionstatechange=()=>{if(this.closed)return;peer.failed=pc.connectionState==='failed';if(peer.failed)$('[data-guest-retry]',this.panel).hidden=false;this.drawPeople();};
    return peer;
  }
  drawPeople(){
    if(this.closed)return;
    const names=this.people||[];
    renderMarkup($('.guest-call-people',this.panel),names.map(person=>{const me=person.id===this.self,peer=this.peers.get(person.id),muted=me?this.muted:person.muted;
      return `<div class="guest-call-person ${muted?'is-muted':''}" data-guest-person="${esc(person.id)}" role="listitem"><span class="person-avatar">${esc([...person.name][0]?.toUpperCase()||'?')}</span><strong>${esc(person.name)}${me?(" "+tText("chat:you")+""):''}</strong><small>${person.host?(""+tText("chat:organizer")+" "):''}${muted?tText("chat:microphoneIsOff"):me?tText("chat:youAreInACall"):peer?.failed?tText("chat:noConnection"):(this.sfu?this.sfu.connected&&this.sfu.has(person.id):peer?.pc.connectionState==='connected')?tText("chat:inACall"):tText("chat:connecting2")}</small></div>`;
    }).join(''));
    if(this.sfu&&!this.sfu.connected){this.status(tText('chat:restoringConnection'));return;}
    if(!this.networkAt)this.status(names.length>1?`${names.length} ${names.length<5?tText("chat:participant"):tText("chat:participants")}`:tText("chat:waitingForParticipantsViaLink"));
  }
  tick(){
    if(this.closed||!this.clock)return;
    const remaining=this.room.expires-this.clock.server-(performance.now()-this.clock.local)/1000;
    if(remaining<=0){this.close(tText("chat:theInvitationSValidityPeriodHasExpired"));return;}
    $('[data-guest-expiry]',this.panel).textContent=tText("chat:until", {value0:(until(this.room))});
  }
  status(message){if(!this.closed)$('[data-guest-call-status]',this.panel).textContent=message;}
  async refreshAudioOutput(){
    try{await this.outputs.refresh();}catch{this.drawOutputs();}
    if(!this.closed)await this.resumeAudio();
  }
  audioSessionChanged(){
    if(this.closed||!this.stream)return;
    if(this.audioSession?.state==='interrupted'){
      $('[data-guest-resume-audio]',this.panel).hidden=false;this.status(tText('chat:audioSessionInterrupted'));
    }else if(this.audioSession?.state==='active')this.resumeAudio();
  }
  async playPeer(peer){
    if(this.closed||this.peers.get(peer.id)!==peer)return false;
    if(!peer.audio.srcObject)return true;
    try{await this.outputs.prepare(peer.audio);}catch{return false;}
    if(this.closed||this.peers.get(peer.id)!==peer)return false;
    try{await peer.audio.play();return true;}catch{return false;}
  }
  resumeAudio(){
    if(this.closed)return Promise.resolve(false);if(this.resumeTask)return this.resumeTask;
    const resume=async()=>{
      const results=this.sfu?[await this.sfu.resume()]:await Promise.all([...this.peers.values()].map(peer=>this.playPeer(peer)));
      const ok=results.every(Boolean);if(!this.closed){$('[data-guest-resume-audio]',this.panel).hidden=ok;if(!ok&&this.outputs.error){this.outputOpen=!phoneCallAudioUI();this.drawOutputs();}}return ok;
    };
    const pending=resume().finally(()=>{if(this.resumeTask===pending)this.resumeTask=null;});this.resumeTask=pending;return pending;
  }
  async toggleSpeakerphone(){
    this.outputOpen=false;this.drawOutputs();
    try{const ok=await this.outputs.togglePhoneSpeaker();if(ok&&!this.closed)await this.resumeAudio();}catch{}
    if(!this.closed)this.drawOutputs();
  }
  async openOutputs(){
    this.outputOpen=!this.outputOpen;this.drawOutputs();
    if(this.outputOpen)try{await this.outputs.refresh();}catch{}this.drawOutputs();
  }
  drawOutputs(){
    if(this.closed)return;const host=$('[data-guest-output-menu]',this.panel),button=$('[data-guest-output]',this.panel);
    const phone=phoneCallAudioUI(),speaker=this.outputs.phoneRoute==='speaker',headphones=$('[data-guest-devices]',this.panel);
    headphones.hidden=!phone;headphones.setAttribute('aria-expanded',String(!!this.outputOpen));
    button.disabled=phone&&this.outputs.busy;button.querySelector('span').textContent=tText(phone?'chat:speakerphone':'chat:audio');
    button.setAttribute('aria-label',tText(phone?(speaker?'chat:disableSpeakerphone':'chat:enableSpeakerphone'):'chat:audioOutput'));
    if(phone){button.setAttribute('aria-pressed',String(speaker));button.removeAttribute('aria-expanded');}
    else{button.removeAttribute('aria-pressed');button.setAttribute('aria-expanded',String(!!this.outputOpen));}
    const status=$('[data-guest-route-status]',this.panel);status.hidden=!phone||!this.outputs.error;status.textContent=this.outputs.error;
    host.hidden=!this.outputOpen;
    button.setAttribute('aria-description',this.outputs.error);if(!this.outputOpen)return;
    const busy=this.outputs.busy,current=this.outputs.current,items=this.outputs.items.filter(d=>!phone||phoneOutputRole(d)!=='speaker'&&(phoneOutputRole(d)!=='system'||!this.outputs.phoneRoutes.receiver));
    renderMarkup(host,`<strong>${tHtml("chat:audioOutput")}</strong>${phone&&this.outputs.supported?items.map(d=>`<button type="button" data-guest-output-id="${esc(d.deviceId)}" aria-pressed="${d.deviceId===current}" ${busy?'disabled':''}>${esc(phoneOutputRole(d)==='receiver'?tText('chat:phoneReceiver'):d.label)}</button>`).join(''):this.outputs.supported?`<select aria-label="${tHtml("chat:audioOutputDevice")}" ${busy?'disabled':''}>${current===null?`<option value="__mixed__" selected disabled>${tHtml('chat:audioOutputNeedsAttention')}</option>`:''}${this.outputs.items.map(d=>`<option value="${esc(d.deviceId)}" ${d.deviceId===current?'selected':''}>${esc(d.label)}</option>`).join('')}</select>`:`<p>${tHtml("chat:selectSpeakerOrHeadphonesInTheDeviceS")}</p>`}${navigator.mediaDevices?.selectAudioOutput&&this.outputs.supported?`<button class="text-link" data-guest-choose-output ${busy?'disabled':''}>${tHtml("chat:selectDevice")}</button>`:''}<p data-guest-output-status role="status">${esc(busy?tText('chat:applyingAudioOutput'):this.outputs.error)}</p><button class="text-link" data-guest-copy-output>${tHtml('chat:copyDiagnostics')}</button>`);
    const apply=async action=>{try{const ok=await action();if(ok&&!this.closed){this.outputOpen=false;this.drawOutputs();await this.resumeAudio();}}catch{if(!this.closed)this.drawOutputs();}};
    host.querySelectorAll('[data-guest-output-id]').forEach(button=>button.onclick=()=>apply(()=>this.outputs.select(button.dataset.guestOutputId)));
    const select=$('select',host);if(select){select.value=current===null?'__mixed__':current;select.onchange=e=>{const id=e.target.value;apply(()=>this.outputs.select(id));};}
    const choose=$('[data-guest-choose-output]',host);if(choose)choose.onclick=()=>apply(()=>this.outputs.choose());
    $('[data-guest-copy-output]',host).onclick=async()=>{
      try{await navigator.clipboard.writeText(JSON.stringify({audioOutput:this.outputs.diagnostics(this.audioSession),media:this.sfu?this.sfu.diagnostics():{transport:'webrtc',participants:this.peers.size},connections:[...this.peers.values()].map(peer=>({connection:peer.pc.connectionState,ice:peer.pc.iceConnectionState}))},null,2));this.ctx.toast(tText('chat:diagnosticsCopiedNoCallRecordingsArePresent'));}
      catch{this.ctx.toast(tText('chat:couldNotCopyDataIsAvailableAbove'));}
    };
  }
  drop(peer){this.outputs.remove(peer.audio);peer.pc.onicecandidate=peer.pc.ontrack=peer.pc.onconnectionstatechange=null;peer.pc.close();peer.audio.pause();peer.audio.srcObject=null;peer.audio.remove();}
  close(message){
    if(this.closed)return this.leaving;this.closed=true;this.resumeTask=null;this.abort.abort();clearInterval(this.timer);clearInterval(this.clockTimer);clearTimeout(this.signalTimer);
    this.sfu?.close();this.sfu=null;this.stream?.getTracks().forEach(t=>t.stop());for(const peer of this.peers.values())this.drop(peer);this.peers.clear();this.outputs.reset();this.releaseAudioSession?.();this.releaseAudioSession=null;this.panel.remove();
    if(this.credential)this.leaving=fetch('/api/guest-calls/rooms/'+this.room.id+'/leave',{method:'POST',credentials:'omit',keepalive:true,headers:{'Accept-Language':locale(),'Content-Type':'application/json',Authorization:'Bearer '+this.credential},body:'{}'}).catch(()=>{});
    window.removeEventListener('resize',this.outputLayoutChange);window.removeEventListener('pagehide',this.unload);window.removeEventListener('focus',this.focus);document.removeEventListener('visibilitychange',this.focus);document.removeEventListener('drg-call-reset',this.reset);navigator.mediaDevices?.removeEventListener('devicechange',this.deviceChanged);this.audioSession?.removeEventListener?.('statechange',this.sessionChange);
    this.leaving=Promise.resolve(this.leaving).finally(()=>{if(current===this){current=null;callsController.setGuestBusy(false);document.dispatchEvent(new Event('drg-guest-call-change'));}});this.finished?.(message);if(message&&this.owner)this.ctx.toast(message);document.dispatchEvent(new Event('drg-guest-call-change'));return this.leaving;
  }
}
