import {tText} from '../../core/i18n.js';
import {esc} from '../../core/ui.js';
import {chatIcon} from './icons.js';
import './recordings.css';

let active=null,voiceRate=1;
const stamp=value=>{const s=Math.max(0,Math.floor(Number(value)||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');};
const announce=()=>document.dispatchEvent(new CustomEvent('drg-recording-playback',{detail:{active:!!active}}));
function deactivate({keepQueue=false,notify=true}={}){if(!active)return;const item=active;active=null;if(!keepQueue)item.cancelQueue();item.media.pause();if(item.media.tagName==='VIDEO'){delete item.media.dataset.recordingExplicit;item.media.muted=true;item.media.loop=true;}item.draw();if(notify)announce();}

const identity=file=>esc(JSON.stringify([file.kind,file.url,file.playback_url,file.thumbnail_url,file.state,file.duration,file.waveform]));
export function voiceMarkup(file){
 const samples=Array.isArray(file.waveform)?file.waveform.slice(0,64):[],ready=(!file.state||file.state==='ready')&&file.url;
 const path=samples.map((n,i)=>{const h=3+Math.max(0,Math.min(31,n))*23/31,x=2+i*196/Math.max(1,samples.length-1);return `M${x.toFixed(2)},${(16-h/2).toFixed(2)}v${h.toFixed(2)}`;}).join('');
 const wave=path?`<svg viewBox="0 0 200 32" preserveAspectRatio="none" aria-hidden="true"><path d="${path}"/><path class="chat-wave-played" d="${path}"/></svg>`:'<span class="chat-wave-line" aria-hidden="true"></span>';
 return `<div class="chat-voice-message chat-attachment" data-chat-attachment-id="${esc(file.id)}" data-recording="voice" data-recording-identity="${identity(file)}" data-recording-duration="${Number(file.duration)||0}"><audio preload="none" data-recording-player data-recording-source="${esc(file.playback_url||file.url)}"></audio><button type="button" class="chat-recording-play" data-recording-toggle aria-label="${esc(tText('chat:playRecording'))}" ${ready?'':'disabled'}>${chatIcon('recordingPlay')}</button><div class="chat-voice-track"><div class="chat-voice-wave" data-recording-seek role="slider" tabindex="0" aria-label="${esc(tText('chat:recordingPosition'))}" aria-valuemin="0" aria-valuemax="${Number(file.duration)||0}" aria-valuenow="0">${wave}</div><div class="chat-recording-meta"><span data-recording-time>${file.duration?stamp(file.duration):'—:—'}</span><span>${esc(tText(!ready?'chat:recordingUnavailable':file.kind==='voice'?'chat:voiceMessage':'chat:audioMessage'))}</span></div></div><button type="button" class="chat-recording-speed" data-recording-speed aria-label="${esc(tText('chat:recordingSpeed'))}" ${ready?'':'disabled'}>1×</button>${ready?`<a class="chat-recording-download" href="${esc(file.url)}${file.url.includes('?')?'&':'?'}download=1" download aria-label="${esc(tText('chat:downloadAudioMessage'))}">${chatIcon('download')}</a>`:''}</div>`;
}

export function videoNoteMarkup(file){
 const ready=(!file.state||file.state==='ready')&&file.url;
 return `<div class="chat-video-note" data-chat-attachment-id="${esc(file.id)}" data-recording="video_note" data-recording-identity="${identity(file)}" data-recording-duration="${Number(file.duration)||0}"><button type="button" class="chat-media-tile is-video-note" data-recording-toggle aria-label="${esc(tText('chat:playVideoNote'))}" ${ready?'':'disabled'}><video data-video-preview data-video-note data-recording-player muted loop playsinline preload="none" ${file.thumbnail_url?`poster="${esc(file.thumbnail_url)}"`:''} ${ready?`data-recording-source="${esc(file.url)}"`:''}></video><span class="chat-circle-ring" aria-hidden="true"></span><span class="chat-circle-sound" aria-hidden="true">${chatIcon('speaker')}</span><span class="chat-circle-time" data-recording-time>${file.duration?stamp(file.duration):'—:—'}</span></button></div>`;
}

export function recordingPlayers(host){
 const events=new AbortController(),signal=events.signal,wired=new WeakMap(),known=new Set();let closed=false,queueActive=false;
 function prepare(node){
  const media=node.querySelector('[data-recording-player]');if(!media)return null;
  let item=wired.get(media);if(item)return item;
  const mediaEvents=new AbortController(),mediaSignal=mediaEvents.signal;
  const duration=()=>Number.isFinite(media.duration)&&media.duration>0?media.duration:Number(node.dataset.recordingDuration)||0;
  function draw(){
   const total=duration(),time=Number.isFinite(media.currentTime)?media.currentTime:0,explicit=active?.media===media;
   node.style.setProperty('--recording-progress',100*(total?time/total:0)+'%');
   const label=node.querySelector('[data-recording-time]');if(label)label.textContent=total?stamp(explicit?Math.max(0,total-time):total):'—:—';
   const seeker=node.querySelector('[data-recording-seek]');if(seeker){seeker.setAttribute('aria-valuemax',String(total));seeker.setAttribute('aria-valuenow',String(time));}
   node.classList.toggle('is-recording-playing',explicit&&!media.paused);node.classList.toggle('is-recording-explicit',explicit);
   const button=node.querySelector('[data-recording-toggle]');button?.setAttribute('aria-label',tText(explicit&&!media.paused?'chat:pauseRecording':node.dataset.recording==='voice'?'chat:playRecording':'chat:playVideoNote'));
   if(node.dataset.recording==='voice'&&button)button.innerHTML=chatIcon(explicit&&!media.paused?'recordingPause':'recordingPlay');
   const speed=node.querySelector('[data-recording-speed]');if(speed)speed.textContent=voiceRate+'×';
  }
  item={media,node,draw,duration,events:mediaEvents,cancelQueue:()=>{queueActive=false;}};wired.set(media,item);known.add(item);
  for(const type of ['loadedmetadata','timeupdate','play','pause','ratechange'])media.addEventListener(type,draw,{signal:mediaSignal});
  media.addEventListener('ended',()=>{
   if(active!==item)return;
   if(queueActive&&media.tagName==='AUDIO'&&!closed&&!document.hidden){
    const nodes=[...host.querySelectorAll('[data-recording="voice"]')],index=nodes.indexOf(node),next=index>=0?nodes[index+1]:null;
    if(next&&!next.querySelector('[data-recording-toggle]')?.disabled){const successor=prepare(next);if(successor){play(successor,false,true);return;}}
   }
   deactivate();
  },{signal:mediaSignal});
  media.addEventListener('error',()=>{if(active===item)deactivate();node.classList.add('recording-failed');},{signal:mediaSignal});draw();return item;
 }
 async function play(item,toggle=true,continuation=false){
  const {media,node}=item;
  if(active===item&&!media.paused){if(toggle){media.pause();item.draw();}return;}
  if(active!==item){deactivate({keepQueue:continuation,notify:!continuation});active=item;if(media.tagName==='VIDEO'){media.dataset.recordingExplicit='true';media.pause();media.currentTime=0;media.loop=false;media.muted=false;}announce();}
  queueActive=media.tagName==='AUDIO';
  if(media.dataset.recordingSource&&!media.src)media.src=media.dataset.recordingSource;
  if(continuation||media.ended)media.currentTime=0;
  media.playbackRate=media.tagName==='AUDIO'?voiceRate:1;
  try{await media.play();if(closed||!media.isConnected||active!==item){media.pause();if(active===item)deactivate();return;}node.classList.remove('recording-failed');}catch{node.classList.add('recording-failed');if(active===item)deactivate();}item.draw();
 }
 const find=target=>{const node=target.closest('[data-recording]');return node&&host.contains(node)?prepare(node):null;};
 host.addEventListener('click',event=>{
  const item=find(event.target);if(!item)return;
  if(event.target.closest('[data-recording-toggle]')){event.preventDefault();event.stopPropagation();play(item);}
  if(event.target.closest('[data-recording-speed]')){voiceRate=voiceRate===1?1.5:voiceRate===1.5?2:1;if(active?.media.tagName==='AUDIO')active.media.playbackRate=voiceRate;item.draw();}
 },{signal});
 async function seek(item,seconds){if(!Number.isFinite(seconds)||item.node.querySelector('[data-recording-toggle]')?.disabled)return;await play(item,false);try{item.media.currentTime=Math.max(0,Math.min(item.duration(),seconds));}catch{}item.draw();}
 host.addEventListener('pointerdown',event=>{
  const slider=event.target.closest('[data-recording-seek]'),item=find(event.target);if(!slider||!item||event.button!==0)return;event.preventDefault();
  const at=e=>{const box=slider.getBoundingClientRect();return item.duration()*Math.max(0,Math.min(1,(e.clientX-box.left)/box.width));};
  slider.setPointerCapture(event.pointerId);const update=e=>{if(e.pointerId===event.pointerId){try{item.media.currentTime=at(e);}catch{}}};
  seek(item,at(event));slider.addEventListener('pointermove',update);slider.addEventListener('lostpointercapture',()=>slider.removeEventListener('pointermove',update),{once:true});
 },{signal});
 host.addEventListener('keydown',event=>{const item=find(event.target);if(!item||!event.target.matches('[data-recording-seek]')||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();seek(item,event.key==='Home'?0:event.key==='End'?item.duration():item.media.currentTime+(event.key==='ArrowLeft'?-5:5));},{signal});
 document.addEventListener('visibilitychange',()=>{if(document.hidden&&active&&host.contains(active.media))deactivate();},{signal});
 document.addEventListener('drg-recording-capture',event=>{if(event.detail?.active&&active&&host.contains(active.media))deactivate();},{signal});
 document.addEventListener('drg-media-viewer',event=>{if(event.detail?.open&&active&&host.contains(active.media))deactivate();},{signal});
 for(const event of ['drg-call-change','drg-guest-call-change'])document.addEventListener(event,()=>{if(active&&host.contains(active.media)&&document.querySelector('.voice-call,[data-guest-active]'))deactivate();},{signal});
 return {sync(){if(active&&!active.media.isConnected)deactivate();for(const item of known)if(!host.contains(item.media)){item.media.pause();item.media.removeAttribute('src');item.media.load();item.events.abort();known.delete(item);wired.delete(item.media);}host.querySelectorAll('[data-recording]').forEach(prepare);},destroy(){closed=true;if(active&&host.contains(active.media))deactivate();for(const item of known){item.media.pause();item.media.removeAttribute('src');item.media.load();item.events.abort();}known.clear();events.abort();}};
}
