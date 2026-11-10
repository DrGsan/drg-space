// One wall-time timer for every visible clock, including live previews.
// Hands interpolate in the compositor; minute-only clocks avoid second ticks.
const subscribers=new Map();
let timer;
function schedule(force=false){
  clearTimeout(timer);timer=undefined;
  if(document.hidden||!subscribers.size)return;
  const now=new Date();
  for(const [draw,state] of subscribers){const tick=Math.floor(now.getTime()/(state.seconds?1000:60000));if(force||state.tick!==tick){state.tick=tick;draw(now);}}
  const period=[...subscribers.values()].some(state=>state.seconds)?1000:60000;
  timer=setTimeout(()=>schedule(),period-Date.now()%period+10);
}
const resume=()=>schedule(true);
export function subscribeClock(draw,seconds){
  const first=!subscribers.size;subscribers.set(draw,{seconds,tick:null});
  if(first){document.addEventListener('visibilitychange',resume);window.addEventListener('focus',resume);}
  schedule(true);
  return ()=>{subscribers.delete(draw);if(!subscribers.size){clearTimeout(timer);timer=undefined;document.removeEventListener('visibilitychange',resume);window.removeEventListener('focus',resume);}else schedule();};
}
