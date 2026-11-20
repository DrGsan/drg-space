// A single ordinary video preview; every visible GIF plays independently.
import {recordingPlayers} from './recording-playback.js';
export function inlineVideos(host){
 const recordings=recordingPlayers(host);
 const entries=new Set(),starting=new Set(),events=new AbortController(),signal=events.signal,reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const presented=new WeakSet(),frameRequests=new Map(),surfaces=new WeakMap();
 let desired=new Set(),suspended=false,recordingActive=false,capturing=false,closed=false;
 const animation=video=>video.hasAttribute('data-animation-preview');
 const clock=seconds=>{seconds=Math.max(0,Math.ceil(seconds||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');};
 function clearFrame(video){
   const request=frameRequests.get(video);frameRequests.delete(video);presented.delete(video);
   if(request?.kind==='video')video.cancelVideoFrameCallback(request.id);
   else if(request)cancelAnimationFrame(request.id);
   const surface=surfaces.get(video);
   if(surface){
     if(surface.request!==null)cancelAnimationFrame(surface.request);
     surface.request=null;surface.time=null;surface.lastPaint=-Infinity;
     if(surface.canvas.width)surface.canvas.width=0;
     if(surface.canvas.height)surface.canvas.height=0;
   }
 }
 function frameSurface(video,tile){
   const canvas=tile.querySelector('[data-video-frame]');
   if(!canvas)return null;
   let surface=surfaces.get(video);
   if(surface?.canvas!==canvas){
     if(surface)clearFrame(video);
     surface={canvas,context:undefined,request:null,time:null,lastPaint:-Infinity};
     surfaces.set(video,surface);
   }
   if(surface.context===undefined&&desired.has(video)&&!video.paused&&video.readyState>=2){
     surface.context=canvas.getContext('2d');
   }
   return surface.context===null?null:surface;
 }
 function paintFrames(video,surface){
   const active=()=>!closed&&desired.has(video)&&host.contains(video)&&
     !video.paused&&!video.seeking&&!video.error&&video.readyState>=2;
   if(!surface.context||surface.request!==null||!active())return;
   const paint=now=>{
     surface.request=null;
     if(!active())return;
     surface.request=requestAnimationFrame(paint);
     if(!video.videoWidth||!video.videoHeight||now-surface.lastPaint<1000/24||video.currentTime===surface.time)return;
     const scale=Math.min(1,480/Math.max(video.videoWidth,video.videoHeight));
     const width=Math.max(1,Math.round(video.videoWidth*scale));
     const height=Math.max(1,Math.round(video.videoHeight*scale));
     const {canvas,context}=surface;
     if(canvas.width!==width)canvas.width=width;
     if(canvas.height!==height)canvas.height=height;
     surface.lastPaint=now;
     try{
       context.drawImage(video,0,0,width,height);
       if(surface.time===null&&!context.getImageData(0,0,1,1).data[3])return;
     }
     catch{surface.context=null;clearFrame(video);update(video);return;}
     surface.time=video.currentTime;
     if(!presented.has(video)){presented.add(video);update(video);}
   };
   surface.request=requestAnimationFrame(paint);
 }
 function awaitFrame(video){
   if(closed||video.paused||video.seeking||video.error||presented.has(video)||frameRequests.has(video))return;
   const request={kind:typeof video.requestVideoFrameCallback==='function'?'video':'raf',id:0};
   const finish=()=>{
     if(frameRequests.get(video)!==request)return;
     frameRequests.delete(video);
     if(closed||!host.contains(video)||video.paused||video.seeking||video.error||video.readyState<2)return;
     presented.add(video);update(video);
   };
   if(request.kind==='video'){
     frameRequests.set(video,request);request.id=video.requestVideoFrameCallback(finish);
   }else if(video.readyState>=2){
     // Older engines: keep the image through the first rendering opportunity.
     frameRequests.set(video,request);request.id=requestAnimationFrame(()=>{
       if(frameRequests.get(video)===request)request.id=requestAnimationFrame(finish);
     });
   }
 }
 const pause=video=>{clearFrame(video);video.pause();video.closest('.chat-media-tile')?.classList.remove('is-preview-playing');};
 function update(video){
   const tile=video.closest('.chat-media-tile');if(!tile)return;
   const imagePoster=tile.querySelector('[data-video-poster]');
   const surface=frameSurface(video,tile);
   if(surface)paintFrames(video,surface);
   else if(imagePoster)awaitFrame(video);
   const playing=!video.paused&&(!imagePoster||presented.has(video));
   tile.classList.toggle('is-preview-playing',playing);
   tile.classList.toggle('has-painted-video-frame',playing&&!!surface);
   if(animation(video))return;
   const position=surface?(surface.time??0):video.currentTime;
   const timer=tile.querySelector('[data-video-remaining]');if(timer&&Number.isFinite(video.duration))timer.textContent=clock(video.duration-position);
   const progress=tile.querySelector('[data-video-progress]');if(progress&&video.duration)progress.style.width=100*position/video.duration+'%';
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
     for(const type of ['loadedmetadata','loadeddata','playing','timeupdate','play','seeked'])video.addEventListener(type,()=>update(video),{signal});
     for(const type of ['pause','ended','seeking','emptied','abort','error'])video.addEventListener(type,()=>{clearFrame(video);update(video);},{signal});
     video.addEventListener('waiting',()=>{if(video.readyState<2)clearFrame(video);update(video);},{signal});
     observer.observe(video);update(video);
   });select();
 },destroy(){closed=true;recordings.destroy();desired.clear();events.abort();observer.disconnect();for(const video of entries)pause(video);entries.clear();}};
}
