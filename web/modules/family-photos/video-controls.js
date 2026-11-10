import {tText,tHtml} from "../../core/i18n.js";
import {$} from '../../core/ui.js';
import {chatIcon} from '../chat/icons.js';
const clock=value=>{value=Math.max(0,Math.floor(value||0));return Math.floor(value/60)+':'+String(value%60).padStart(2,'0');};
export function videoControls(host,video){
 const controls=document.createElement('div');controls.className='fp-video-controls';
 controls.innerHTML=`<div class="fp-video-buttons"><button type="button" data-video-mute aria-label="${tHtml("family_photos:turnOffSound")}">${chatIcon('speaker')}</button><input type="range" data-video-volume aria-label="${tHtml("family_photos:volume")}" min="0" max="1" step="0.05" value="1"><button type="button" data-video-toggle aria-label="${tHtml("family_photos:play")}">${chatIcon('play')}</button><span></span><button type="button" data-video-pip aria-label="${tHtml("family_photos:pictureInPicture")}" ${document.pictureInPictureEnabled?'':'hidden'}>${chatIcon('restore')}</button><button type="button" data-video-fullscreen aria-label="${tHtml("family_photos:fullScreen")}">${chatIcon('expand')}</button><button type="button" data-video-speed aria-label="${tHtml("family_photos:playbackSpeed")}">1×</button></div><div class="fp-video-timeline"><time data-video-elapsed>0:00</time><input type="range" data-video-seek aria-label="${tHtml("family_photos:videoPosition")}" min="0" max="0" step="0.1" value="0"><time data-video-total>0:00</time></div>`;
 host.append(controls);const events=new AbortController(),signal=events.signal;
 const toggle=()=>{if(video.paused)video.play().catch(()=>{});else video.pause();};
 const update=()=>{
   const duration=Number.isFinite(video.duration)?video.duration:0,seek=$('[data-video-seek]',controls);seek.max=duration;seek.value=video.currentTime;
   $('[data-video-elapsed]',controls).textContent=clock(video.currentTime);$('[data-video-total]',controls).textContent=clock(duration);
   const button=$('[data-video-toggle]',controls);button.innerHTML=chatIcon(video.paused?'play':'pause');button.setAttribute('aria-label',video.paused?tText("family_photos:play"):tText("family_photos:pause"));
   $('[data-video-mute]',controls).setAttribute('aria-label',video.muted?tText("family_photos:enableAudio"):tText("family_photos:turnOffSound"));$('[data-video-volume]',controls).value=video.muted?0:video.volume;
 };
 $('[data-video-toggle]',controls).onclick=toggle;video.addEventListener('click',toggle,{signal});
 $('[data-video-seek]',controls).oninput=e=>{video.currentTime=Number(e.target.value);};
 $('[data-video-mute]',controls).onclick=()=>{video.muted=!video.muted;update();};
 $('[data-video-volume]',controls).oninput=e=>{video.volume=Number(e.target.value);video.muted=video.volume===0;};
 $('[data-video-speed]',controls).onclick=e=>{const values=[1,1.25,1.5,2,.75],index=values.indexOf(video.playbackRate);video.playbackRate=values[(index+1)%values.length];e.currentTarget.textContent=video.playbackRate+'×';};
 $('[data-video-pip]',controls).onclick=()=>{if(document.pictureInPictureElement)document.exitPictureInPicture?.().catch(()=>{});else video.requestPictureInPicture?.().catch(()=>{});};
 $('[data-video-fullscreen]',controls).onclick=()=>{if(document.fullscreenElement)document.exitFullscreen?.();else if(host.requestFullscreen)host.requestFullscreen().catch(()=>{});else video.webkitEnterFullscreen?.();};
 for(const event of ['loadedmetadata','timeupdate','play','pause','volumechange'])video.addEventListener(event,update,{signal});
 update();video.play().catch(()=>{});return()=>{events.abort();controls.remove();};
}
