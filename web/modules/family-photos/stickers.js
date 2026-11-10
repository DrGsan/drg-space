import {tText,tHtml} from "../../core/i18n.js";
import {esc} from '../../core/ui.js';

export function stickerMarkup(message){
  const file=(message.media||[]).find(file=>file.kind==='sticker');
  const label=tText("family_photos:sticker", {value0:(message.sticker_emoji?' '+message.sticker_emoji:'')});
  if(!file||file.state!=='ready')return `<div class="fp-sticker-visual fp-sticker-wait" role="status">${esc(message.sticker_emoji||'')}<small>${tHtml("family_photos:stickerLoading")}</small></div>`;
  if(file.animation_url)return `<div class="fp-sticker-visual" data-fp-animation="${esc(file.id)}" data-animation-url="${esc(file.animation_url)}" role="img" aria-label="${esc(label)}"><span>${tHtml("family_photos:loadingSticker")}</span></div>`;
  const source=file.mime==='video/webm'?file.thumbnail_url:file.url;
  return source?`<img class="fp-sticker-visual" src="${esc(source)}" alt="${esc(label)}" loading="lazy">`:`<div class="fp-sticker-visual fp-sticker-wait" role="status"><small>${tHtml("family_photos:preparingSticker")}</small></div>`;
}

export function stickerPlayers(host,ctx,signal){
  const entries=new Map(),animations=new Map();let closed=false;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const update=()=>{for(const entry of entries.values()){if(entry.player){if(entry.visible&&!document.hidden&&!reduced.matches)entry.player.play();else entry.player.pause();}}};
  const load=async(node,entry)=>{
    if(entry.loading||entry.player||entry.failed)return;
    entry.loading=true;
    try{
      const url=new URL(node.dataset.animationUrl,location.origin);
      if(url.origin!==location.origin||!/^\/api\/family-photos\/groups\/[^/]+\/(?:media\/[a-f0-9]{32}|emoji\/\d{1,20})$/.test(url.pathname)||url.search!=='?animation=1')throw Error('Invalid sticker URL');
      if(!animations.has(url.href))animations.set(url.href,ctx.api(url.pathname.slice(4)+url.search,'GET',undefined,{signal}));
      const [data,module]=await Promise.all([animations.get(url.href),import('lottie-web/build/player/lottie_light_canvas.js')]);
      if(closed||!node.isConnected||entries.get(node)!==entry)return;
      node.replaceChildren();
      entry.player=module.default.loadAnimation({container:node,renderer:'canvas',loop:true,autoplay:false,animationData:structuredClone(data),rendererSettings:{clearCanvas:true,preserveAspectRatio:'xMidYMid meet'}});
      entry.player.addEventListener('data_failed',()=>{entry.failed=true;entry.player?.destroy();entry.player=null;node.textContent=node.dataset.emojiFallback||tText("family_photos:failedToDisplaySticker");});
      update();
    }catch(error){if(!closed&&node.isConnected&&!signal?.aborted){entry.failed=true;node.textContent=node.dataset.emojiFallback||tText("family_photos:failedToDisplaySticker");}}
    finally{entry.loading=false;}
  };
  const observer=new IntersectionObserver(changes=>{for(const change of changes){const entry=entries.get(change.target);if(!entry)continue;entry.visible=change.isIntersecting;if(entry.visible)void load(change.target,entry);}update();},{root:host,rootMargin:'80px'});
  document.addEventListener('visibilitychange',update);reduced.addEventListener('change',update);
  return {
    sync(){
      for(const [node,entry] of entries)if(!host.contains(node)){entry.player?.destroy();observer.unobserve(node);entries.delete(node);}
      host.querySelectorAll('[data-fp-animation]').forEach(node=>{if(!entries.has(node)){const entry={visible:false};entries.set(node,entry);observer.observe(node);}else{const entry=entries.get(node);if(entry.player&&!node.querySelector('canvas')){entry.player.destroy();entry.player=null;void load(node,entry);}}});
    },
    destroy(){closed=true;observer.disconnect();document.removeEventListener('visibilitychange',update);reduced.removeEventListener('change',update);for(const entry of entries.values())entry.player?.destroy();entries.clear();animations.clear();},
  };
}
