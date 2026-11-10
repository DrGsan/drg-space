import {tText} from "./i18n.js";
// The navigation badge counts messages, independently of notification history.
let active;
export const chatUnreadController={
  attach(ctx){
    if(!ctx.moduleEnabled('chat')){this.destroy();return;}
    if(active?.uid!==ctx.state.me.id){this.destroy();active=createCounter(ctx);}
    active.attach(ctx);
  },
  destroy(){active?.destroy();active=null;},
};

function createCounter(initial){
  let ctx=initial,disposed=false,timer;
  const totals={site:0,family:0},running=new Set(),again=new Set();
  const uid=ctx.state.me.id,abort=new AbortController();
  function paint(){
    const total=totals.site+totals.family;
    for(const badge of document.querySelectorAll('[data-chat-unread]')){
      badge.hidden=total===0;
      const text=total>99?'99+':String(total);if(badge.textContent!==text)badge.textContent=text;
      const button=badge.closest('button'),description=tText("common:unreadMessages", {value0:(total)});
      button.setAttribute('aria-description',description);button.title=total?tText("common:messages", {value0:(description)}):tText("common:messages2");
    }
  }
  async function refreshSource(source){
    if(disposed||document.hidden)return;
    if(running.has(source)){again.add(source);return;}
    running.add(source);
    try{
      const data=await ctx.api(source==='site'?'/chat/unread':'/family-photos/unread','GET',undefined,true,{}, {signal:abort.signal});
      if(disposed)return;
      const unread=Number.isSafeInteger(data.unread)&&data.unread>=0?data.unread:(data.items||[]).reduce((sum,room)=>sum+(Number.isSafeInteger(room.unread)&&room.unread>0?room.unread:0),0);
      totals[source]=source==='family'&&!ctx.moduleEnabled('family_photos')?0:unread;
      paint();
    }catch(error){if(!disposed&&(source==='family'||[401,403].includes(error.status))){totals[source]=0;paint();}}
    finally{running.delete(source);if(again.delete(source)&&!disposed)schedule();}
  }
  function refresh(){
    refreshSource('site');
    if(ctx.moduleEnabled('family_photos'))refreshSource('family');
    else{totals.family=0;paint();}
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(refresh,180);}
  const events=[[document,'drg-inbox-change'],[document,'visibilitychange'],[window,'focus'],[window,'online']];
  for(const [target,type] of events)target.addEventListener(type,schedule);
  const poll=setInterval(refresh,20000);
  return {uid,attach(next){ctx=next;if(!ctx.moduleEnabled('family_photos'))totals.family=0;paint();schedule();},destroy(){disposed=true;abort.abort();clearTimeout(timer);clearInterval(poll);for(const [target,type] of events)target.removeEventListener(type,schedule);}};
}
