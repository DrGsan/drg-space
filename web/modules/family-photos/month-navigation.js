// Read the real scroll surface: installed desktop apps scroll #app, ordinary
// browsers scroll the document. Never scroll the sidebar and its ancestors
// together with scrollIntoView(). A long archive must keep the index bounded.
export function familyMonthNavigation(host,{onMonth}={}){
  let rail=null,sections=[],buttons=[],frame=0,closed=false,current=null,activeButton=null;
  const scrollRoot=()=>{
    for(let el=host.parentElement;el&&el!==document.body;el=el.parentElement){
      if(/auto|scroll/.test(getComputedStyle(el).overflowY)&&el.scrollHeight>el.clientHeight+1)return el;
    }
    return document.scrollingElement;
  };
  const viewport=root=>root===document.scrollingElement?{top:0,height:window.innerHeight}:root.getBoundingClientRect();
  const mark=month=>{
    const changed=current!==month;current=month;
    const selected=buttons.find(b=>b.dataset.fpMonth===month);
    if(activeButton&&activeButton!==selected)activeButton.removeAttribute('aria-current');
    if(selected?.getAttribute('aria-current')!=='location')selected?.setAttribute('aria-current','location');
    activeButton=selected;
    // Only move the index when the viewed month changes. Scrolling the menu
    // manually, or polling unchanged photos, must not pull it back again.
    if(changed&&selected&&rail){
      const box=rail.getBoundingClientRect(),item=selected.getBoundingClientRect();
      if(item.top<box.top)rail.scrollTop-=box.top-item.top;
      else if(item.bottom>box.bottom)rail.scrollTop+=item.bottom-box.bottom;
    }
  };
  const paint=()=>{
    frame=0;
    if(closed||!rail?.isConnected||!rail.getClientRects().length||!sections.length||document.hidden)return;
    const root=scrollRoot(),box=viewport(root),height=Math.max(80,Math.floor(box.height-48));
    if(rail.style.getPropertyValue('--fp-rail-height')!==height+'px')rail.style.setProperty('--fp-rail-height',height+'px');
    const line=box.top+24;
    // Binary search avoids scanning thousands of dates on each scroll frame.
    let low=0,high=sections.length-1,at=0;
    while(low<=high){const mid=(low+high)>>1;if(sections[mid].getBoundingClientRect().top<=line+1){at=mid;low=mid+1;}else high=mid-1;}
    if(root.scrollHeight>root.clientHeight+1&&root.scrollTop+root.clientHeight>=root.scrollHeight-2)at=sections.length-1;
    mark(sections[at].dataset.fpDay.slice(0,7));
  };
  const schedule=()=>{if(!closed&&!frame)frame=requestAnimationFrame(paint);};
  const scroll=event=>{if(rail&&(event.target===rail||rail.contains(event.target)))return;schedule();};
  const jump=month=>{
    const target=sections.find(section=>section.dataset.fpDay.slice(0,7)===month);if(!target)return false;
    const root=scrollRoot(),box=viewport(root),top=root.scrollTop+target.getBoundingClientRect().top-box.top-24;
    // A month jump is direct; years of photos should not animate past the user.
    const surface=root===document.scrollingElement?window:root;
    surface.scrollTo({top:Math.max(0,top),behavior:'instant'});schedule();return true;
  };
  const click=event=>{
    const button=event.target.closest('[data-fp-jump]');if(!button||!rail?.contains(button))return;
    if(!jump(button.dataset.fpMonth))onMonth?.(button.dataset.fpMonth);
  };
  document.addEventListener('scroll',scroll,{capture:true,passive:true});
  window.addEventListener('resize',schedule,{passive:true});host.addEventListener('click',click);
  return {
    current:()=>current,
    jump,
    update(){rail=host.querySelector('.fp-date-rail');sections=[...host.querySelectorAll('[data-fp-day]')].filter(s=>s.dataset.fpDay!=='unknown');buttons=rail?[...rail.querySelectorAll('[data-fp-jump]')]:[];if(!rail)current=null;schedule();},
    destroy(){closed=true;cancelAnimationFrame(frame);document.removeEventListener('scroll',scroll,true);window.removeEventListener('resize',schedule);host.removeEventListener('click',click);}
  };
}
