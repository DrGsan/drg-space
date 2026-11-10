import {tText} from "./i18n.js";
// The viewport observer also respects clipping by nested iOS scroll containers.
// Re-observe after a page arrives to fill short lists, one request at a time.
export function edgePagination(container,{root=null,canLoad,load,onError}){
  let target=null,busy=false,closed=false,failed=false;
  const paint=()=>{if(target){target.textContent=busy?tText("common:loading"):'';target.setAttribute('aria-busy',String(busy));}};
  const observe=()=>{if(target&&!closed){observer.unobserve(target);observer.observe(target);}};
  const observer=new IntersectionObserver(async entries=>{
    if(closed||busy||failed||!entries.some(e=>e.target===target&&e.isIntersecting)||!canLoad())return;
    busy=true;paint();
    try{await load();}
    catch(error){failed=true;if(!closed)onError(error);}
    finally{busy=false;paint();if(!failed)observe();}
  },{root,rootMargin:'120px 0px'});
  const retry=()=>{if(failed){failed=false;observe();}};
  // A failed page waits for another scroll/connection rather than a retry loop.
  const scrollTarget=root||document;
  scrollTarget.addEventListener('scroll',retry,true);window.addEventListener('online',retry);
  return {
    update(){if(closed)return;const next=container.querySelector('[data-page-edge]');if(target!==next){if(target)observer.unobserve(target);target=next;}paint();observe();},
    destroy(){closed=true;observer.disconnect();target=null;scrollTarget.removeEventListener('scroll',retry,true);window.removeEventListener('online',retry);}
  };
}
