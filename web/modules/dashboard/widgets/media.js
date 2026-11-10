import {tText,tHtml} from "../../../core/i18n.js";
import { esc } from "../../../core/ui.js";
export function mountMedia(host,ctx) {
  const config=ctx.instance.config,selected=config.selected || [];
  const items=selected.map(id=>(config.items || []).find(item=>item.id===id)).filter(Boolean);
  if(!items.length){host.innerHTML=`<p class="muted">${tHtml("dashboard:addImagesOrGifsAndMarkThemFor")}</p>`;return;}
  let index=0,timer,disposed=false;
  const draw=()=>{
    if(disposed || !host.isConnected)return;
    const item=items[index];
    host.innerHTML=`<figure class="media-frame"><img style="object-fit:${config.fit==='contain'?'contain':'cover'}" src="${esc(item.asset_id?'/api/media/'+item.asset_id:item.url)}" alt="${esc(item.title || tText("dashboard:widgetImage"))}"><figcaption>${esc(item.title)}${config.mode==='slideshow'&&items.length>1?` <span class="muted">${index+1}/${items.length}</span>`:''}</figcaption></figure><p class="hint" data-media-error hidden></p>`;
    const image=host.querySelector('img');let retried=false;
    image.onerror=async()=>{
      if(item.asset_id&&!retried){retried=true;try{await ctx.api('/me');if(!disposed&&image.isConnected)image.src='/api/media/'+item.asset_id+'?retry='+Date.now();return;}catch{}}
      if(!disposed&&host.isConnected){const message=host.querySelector('[data-media-error]');message.hidden=false;message.textContent=tText("dashboard:couldNotOpenImageCheckTheFileOr");}
    };
  };
  draw();
  if(config.mode==='slideshow'&&items.length>1)timer=setInterval(()=>{if(!document.hidden){index=(index+1)%items.length;draw();}},Math.max(2,config.interval || 10)*1000);
  return ()=>{disposed=true;clearInterval(timer);};
}
