import {locale,tText,tHtml} from "../../../core/i18n.js";
import {csrfCookie} from "../../../core/auth.js";
import {esc,date} from "../../../core/ui.js";

const refreshInterval=600000;
const retryInterval=60000;
const widgetsBySession=new WeakMap();

function sessionWidgets(account) {
  if(!widgetsBySession.has(account))widgetsBySession.set(account,new Map());
  return widgetsBySession.get(account);
}

function widgetEntry(widgets,instance) {
  const {feeds=[],limit=10}=instance.config??{};
  const key=JSON.stringify([locale(),limit,feeds.map(feed=>[feed.url,feed.label||''])]);
  let entry=widgets.get(instance.id);
  if(entry?.key!==key){
    entry={key,data:null,error:'',pending:null,nextCheck:0};
  }
  widgets.delete(instance.id);
  widgets.set(instance.id,entry);
  // Keep recently mounted instances up to the dashboard's 30-widget limit.
  if(widgets.size>30)widgets.delete(widgets.keys().next().value);
  return entry;
}

async function fetchHeadlines(entry,ctx,refresh) {
  entry.nextCheck=Date.now()+refreshInterval;
  try{
    entry.data=await ctx.api('/widgets/'+ctx.instance.id+'/rss'+(refresh?'?refresh=true':''));
    entry.error='';
    entry.nextCheck=Date.now()+(entry.data.errors.length?retryInterval:refreshInterval);
  }catch(error){
    if([401,403,404].includes(error.status))entry.data=null;
    entry.error=error.message;
    const retryAfter=Number(error.retryAfter)*1000;
    entry.nextCheck=Date.now()+(retryAfter>0?retryAfter:retryInterval);
  }
}

export function mountRss(host,ctx) {
  const account=ctx.state.me,widgets=sessionWidgets(account),entry=widgetEntry(widgets,ctx.instance);
  let disposed=false,pending,timer;
  function active(){
    if(disposed || !host.isConnected || ctx.state.me!==account || widgets.get(ctx.instance.id)!==entry)return false;
    // Another tab can change accounts while this workspace still shows the previous one.
    if(account.csrf && csrfCookie()!==account.csrf){
      clearTimeout(timer);
      host.innerHTML=`<a href="/">${tHtml("app:signInAgain")}</a>`;
      return false;
    }
    return true;
  }

  function render(){
    if(!entry.data && !entry.error){
      host.innerHTML=`<p class="muted">${tHtml("dashboard:loadingHeadlines")}</p>`;
      return;
    }
    const data=entry.data;
    const items=data?`<div class="rss-items">${data.items.map(item=>`<a class="rss-item" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer"><strong>${esc(item.title)}</strong><span class="small muted">${esc(item.source)}${item.published?' · '+date(item.published):''}</span></a>`).join('') || `<p class="muted">${tHtml("dashboard:noHeadlinesYetAddSourcesInTheWidget")}</p>`}</div>${data.errors.map(error=>`<p class="hint rss-error">${esc(error.source)}: ${esc(error.message)}</p>`).join('')}`:'';
    host.innerHTML=items+`<button class="text-link" data-refresh-rss>${tHtml("dashboard:refreshNews")}</button><p class="hint" data-rss-status role="status"></p>`;
    host.querySelector('[data-rss-status]').textContent=entry.error || tText("dashboard:updateEvery10Minutes");
    host.querySelector('[data-refresh-rss]').onclick=()=>load(true);
  }

  function schedule(){
    clearTimeout(timer);
    timer=setTimeout(refreshStale,Math.max(0,entry.nextCheck-Date.now()));
  }

  function load(refresh){
    if(!active() || pending)return pending;
    if(!entry.pending)entry.pending=fetchHeadlines(entry,ctx,refresh).finally(()=>{entry.pending=null;});
    const button=host.querySelector('[data-refresh-rss]');
    if(button){button.disabled=true;button.textContent=tText("dashboard:refreshing");}
    schedule();
    pending=entry.pending.then(()=>{
      pending=null;
      if(active()){render();schedule();}
    });
    return pending;
  }

  // Partial failures expire at the server's shorter error TTL. A normal load
  // then retries failed sources while still reusing the healthy feeds.
  function refreshExpired(){return load(Boolean(entry.data && !entry.error && !entry.data.errors.length));}

  function refreshStale(){
    if(!document.hidden && active()){
      if(Date.now()>=entry.nextCheck)refreshExpired();
      else schedule();
    }
  }

  if(active()){
    render();
    if(entry.pending || Date.now()>=entry.nextCheck)refreshExpired();
    else schedule();
  }
  document.addEventListener('visibilitychange',refreshStale);
  return ()=>{disposed=true;clearTimeout(timer);document.removeEventListener('visibilitychange',refreshStale);};
}
