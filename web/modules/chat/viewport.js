// iOS can pan the visual viewport and scroll a focused field's ancestors while
// showing its keyboard. Size and position the entire chat, not only its shell.
export function bindChatViewport(shell){
  const root=document.documentElement,app=document.querySelector('#app');
  const viewport=window.visualViewport,listeners=[];
  let closed=false,frame=0;
  const update=()=>{
    if(closed)return;
    // Let the browser handle accessibility zoom without chasing its pan.
    if(viewport&&Math.abs(viewport.scale-1)>.01)return;
    const history=shell.querySelector('.chat-messages');
    const follow=history&&history.scrollHeight-history.scrollTop-history.clientHeight<100;
    const height=viewport?.height||window.innerHeight;
    const keyboard=Math.max(window.innerHeight,root.clientHeight)-height>100;
    // WebKit can retain offsetTop after dismissing the keyboard. Only apply
    // its pan while the visual viewport is actually shorter than the layout.
    const top=keyboard?Math.max(0,viewport?.offsetTop||0):0;
    root.style.setProperty('--chat-viewport-height',height+'px');
    root.style.setProperty('--chat-viewport-top',top+'px');
    root.style.setProperty('--chat-safe-bottom',keyboard?'0px':'var(--app-safe-bottom)');
    // An older layout may already have been scrolled by focus before a resize
    // notification. These ancestors have no scrollable content in chat mode.
    if(app){app.scrollTop=0;app.scrollLeft=0;}
    if(follow)history.scrollTop=history.scrollHeight;
  };
  const schedule=()=>{if(!closed&&!frame)frame=requestAnimationFrame(()=>{frame=0;update();});};
  const listen=(target,type)=>{target?.addEventListener(type,schedule);listeners.push(()=>target?.removeEventListener(type,schedule));};
  root.classList.add('chat-viewport');
  update();
  for(const type of ['resize','scroll','scrollend'])listen(viewport,type);
  for(const type of ['resize','pageshow','orientationchange'])listen(window,type);
  for(const type of ['focusin','focusout'])listen(document,type);
  // Focus can arrive before WebKit finishes panning its enclosing scroll view.
  listen(app,'scroll');
  return ()=>{
    closed=true;cancelAnimationFrame(frame);listeners.forEach(remove=>remove());
    root.classList.remove('chat-viewport');
    for(const name of ['--chat-viewport-height','--chat-viewport-top','--chat-safe-bottom'])root.style.removeProperty(name);
  };
}
