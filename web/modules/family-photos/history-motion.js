// Let wheel/touch/keyboard scrolling finish before changing history geometry.
// The idle timer follows momentum scroll events as well as the initial input.
export function historyMotion(history,onIdle,signal){
  const events=new AbortController(),pending=new Map(),waiters=new Set();let active=false,touching=false,timer=0,closed=false;
  function settle(){
    clearTimeout(timer);timer=0;if(touching)return;active=false;if(closed||document.hidden)return;
    const tasks=[...pending.values()];pending.clear();for(const task of tasks)task();
    for(const ready of waiters)ready(true);waiters.clear();onIdle?.();
  }
  function moving(){if(closed)return;active=true;clearTimeout(timer);timer=setTimeout(settle,160);}
  const options={signal:events.signal,passive:true};
  history.addEventListener('wheel',moving,options);
  history.addEventListener('touchstart',()=>{touching=true;moving();},options);
  history.addEventListener('touchmove',moving,options);
  for(const name of ['touchend','touchcancel'])history.addEventListener(name,event=>{touching=!!event.touches?.length;moving();},options);
  history.addEventListener('pointerdown',event=>{if(event.pointerType==='touch'||event.target===history)moving();},options);
  history.addEventListener('keydown',event=>{if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)&&!event.target.closest('input,textarea,[contenteditable="true"]'))moving();},{signal:events.signal});
  history.addEventListener('scroll',()=>{if(active)moving();},options);
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);timer=0;active=false;touching=false;}else if(!active)settle();},{signal:events.signal});
  function destroy(){if(closed)return;closed=true;clearTimeout(timer);events.abort();pending.clear();for(const ready of waiters)ready(false);waiters.clear();}
  signal?.addEventListener('abort',destroy,{once:true});
  return {
    get active(){return active;},
    defer(key,task){if(closed)return;if(!active&&!document.hidden)task();else pending.set(key,task);},
    whenIdle(){if(closed)return Promise.resolve(false);if(!active&&!document.hidden)return Promise.resolve(true);return new Promise(ready=>waiters.add(ready));},
    destroy,
  };
}
