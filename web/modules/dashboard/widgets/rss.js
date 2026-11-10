import {tText,tHtml} from "../../../core/i18n.js";
import { esc, date } from "../../../core/ui.js";

const refreshInterval=600000;

export function mountRss(host,ctx) {
  let disposed=false,pending,rendered=false,lastAttempt=0,timer;
  const load=(refresh=false)=>{
    if(disposed || !host.isConnected)return Promise.resolve();
    if(pending)return pending;
    lastAttempt=Date.now();
    clearTimeout(timer);timer=setTimeout(refreshStale,refreshInterval);
    if(!rendered)host.innerHTML=`<p class="muted">${tHtml("dashboard:loadingHeadlines")}</p>`;
    const button=host.querySelector('[data-refresh-rss]');
    if(button){button.disabled=true;button.textContent=tText("dashboard:refreshing");}
    pending=Promise.resolve().then(async()=>{
      try {
        const data=await ctx.api('/widgets/'+ctx.instance.id+'/rss'+(refresh?'?refresh=true':''));
        if(disposed || !host.isConnected)return;
        host.innerHTML=`<div class="rss-items">${data.items.map(item=>`<a class="rss-item" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer"><strong>${esc(item.title)}</strong><span class="small muted">${esc(item.source)}${item.published?' · '+date(item.published):''}</span></a>`).join('') || `<p class="muted">${tHtml("dashboard:noHeadlinesYetAddSourcesInTheWidget")}</p>`}</div>${data.errors.map(error=>`<p class="hint rss-error">${esc(error.source)}: ${esc(error.message)}</p>`).join('')}<button class="text-link" data-refresh-rss>${tHtml("dashboard:refreshNews")}</button><p class="hint" data-rss-status role="status">${tHtml("dashboard:updateEvery10Minutes")}</p>`;
        rendered=true;
        host.querySelector('[data-refresh-rss]').onclick=()=>load(true);
      }catch(error){
        if(disposed || !host.isConnected)return;
        if(rendered)host.querySelector('[data-rss-status]').textContent=error.message;
        else host.innerHTML='<p class="muted">'+esc(error.message)+'</p>';
      }finally{
        pending=null;
        const button=host.querySelector('[data-refresh-rss]');
        if(!disposed && button){button.disabled=false;button.textContent=tText("dashboard:refreshNews");}
      }
    });
    return pending;
  };
  const refreshStale=()=>{
    if(!document.hidden && Date.now()-lastAttempt>=refreshInterval)load(true);
  };
  document.addEventListener('visibilitychange',refreshStale);
  load();
  return ()=>{disposed=true;clearTimeout(timer);document.removeEventListener('visibilitychange',refreshStale);};
}
