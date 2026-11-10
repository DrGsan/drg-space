// A single ordinary video preview; every visible GIF plays independently.
import {recordingPlayers} from './recording-playback.js';
export function inlineVideos(host){
 const recordings=recordingPlayers(host);
 const entries=new Set(),starting=new Set(),events=new AbortController(),signal=events.signal,reduced=matchMedia('(prefers-reduced-motion: reduce)');
 let desired=new Set(),suspended=false,recordingActive=false,capturing=false,closed=false;
 const animation=video=>video.hasAttribute('data-animation-preview');
 const clock=seconds=>{seconds=Math.max(0,Math.ceil(seconds||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');};
 const pause=video=>{video.pause();video.closest('.chat-media-tile')?.classList.remove('is-preview-playing');};
 function update(video){
   const tile=video.closest('.chat-media-tile');if(!tile)return;
   tile.classList.toggle('is-preview-playing',!video.paused||starting.has(video));
   if(animation(video))return;
   const timer=tile.querySelector('[data-video-remaining]');if(timer&&Number.isFinite(video.duration))timer.textContent=clock(video.duration-video.currentTime);
   const progress=tile.querySelector('[data-video-progress]');if(progress&&video.duration)progress.style.width=100*video.currentTime/video.duration+'%';
 }
 function select(){
   if(closed)return;
   const root=host.getBoundingClientRect(),top=Math.max(0,root.top),bottom=Math.min(innerHeight,root.bottom),center=(top+bottom)/2;
   const candidates=[...entries].filter(video=>{
     if(!video.isConnected||!animation(video)&&!video.hasAttribute('data-video-note')&&!video.closest('.chat-media-album.is-single'))return false;
     const box=video.getBoundingClientRect(),visible=Math.max(0,Math.min(box.bottom,bottom)-Math.max(box.top,top))*Math.max(0,Math.min(box.right,root.right,innerWidth)-Math.max(box.left,root.left,0));
     return box.width*box.height>0&&visible/(box.width*box.height)>(animation(video)?0:.55);
   });
   const ordinary=candidates.filter(video=>!animation(video)).sort((a,b)=>Math.abs(a.getBoundingClientRect().top+a.clientHeight/2-center)-Math.abs(b.getBoundingClientRect().top+b.clientHeight/2-center))[0];
   const allowed=!document.hidden&&!suspended&&!recordingActive&&!capturing&&!document.querySelector('.voice-call,[data-guest-active]')&&!reduced.matches&&!navigator.connection?.saveData;
   desired=new Set(allowed?candidates.filter(video=>animation(video)||video===ordinary):[]);
   for(const video of entries){
     if(video.dataset.recordingExplicit==='true')continue;
     if(!desired.has(video)){if(!video.paused||starting.has(video))pause(video);continue;}
     if(!video.paused||starting.has(video))continue;
     starting.add(video);if(!video.src&&video.dataset.recordingSource)video.src=video.dataset.recordingSource;video.muted=true;update(video);
     video.play().then(()=>{starting.delete(video);if(video.dataset.recordingExplicit!=='true'&&(closed||!desired.has(video)))pause(video);else update(video);}).catch(()=>{starting.delete(video);update(video);});
   }
 }
 const observer=new IntersectionObserver(select,{root:host,threshold:[0,.01,.55,.8,1]});
 host.addEventListener('scroll',select,{signal,passive:true});
 document.addEventListener('visibilitychange',select,{signal});reduced.addEventListener('change',select,{signal});
 document.addEventListener('drg-media-viewer',e=>{suspended=!!e.detail?.open;select();},{signal});
 document.addEventListener('drg-recording-capture',e=>{capturing=!!e.detail?.active;select();},{signal});
 for(const event of ['drg-call-change','drg-guest-call-change'])document.addEventListener(event,select,{signal});
 document.addEventListener('drg-recording-playback',e=>{recordingActive=!!e.detail?.active;select();},{signal});
 return {sync(){
   recordings.sync();
   for(const video of entries)if(!host.contains(video)){pause(video);observer.unobserve(video);entries.delete(video);}
   host.querySelectorAll('.chat-media-tile>video[data-video-preview],.chat-media-tile>video[data-animation-preview]').forEach(video=>{
     if(entries.has(video)){update(video);return;}
     entries.add(video);video.loop=true;video.muted=true;
     for(const type of ['loadedmetadata','timeupdate','play','pause','ended'])video.addEventListener(type,()=>update(video),{signal});
     observer.observe(video);update(video);
   });select();
 },destroy(){closed=true;recordings.destroy();desired.clear();events.abort();observer.disconnect();for(const video of entries)pause(video);entries.clear();}};
}
