import {tText} from "./i18n.js";
// Installed phone UI is independent of the desktop workspace and its history.
export function mobileApp(){
  return typeof window!=='undefined'&&window.matchMedia('(max-width:760px)').matches&&
    (window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true);
}
export function mobileStart(enabled=()=>true){
  return ['chat','notes','tracker','tasks'].find(enabled)||'settings';
}
export const mobileTabs=[['chat','messages-square',tText("common:messages2")],['notes','notebook-text',tText("common:notes")],['tracker','kanban',tText("tracker:title")],['tasks','list-todo',tText("common:tasks")],['settings','settings',tText("common:settings")]];

// WebKit keeps the layout viewport tall when the keyboard covers its bottom.
// Keep the phone's shell inside the visual viewport; each module owns its scroll.
export function bindMobileViewport(){
  const root=document.documentElement,viewport=window.visualViewport,cleanups=[];
  let frame=0;
  const clear=()=>{root.classList.remove('mobile-keyboard');for(const name of ['--mobile-viewport-height','--mobile-viewport-top','--mobile-safe-bottom'])root.style.removeProperty(name);};
  const update=()=>{
    frame=0;
    if(!mobileApp()){clear();return;}
    if(viewport&&Math.abs(viewport.scale-1)>.01)return;
    const height=viewport?.height||window.innerHeight;
    const keyboard=Math.max(window.innerHeight,root.clientHeight)-height>100;
    root.classList.toggle('mobile-keyboard',keyboard);
    root.style.setProperty('--mobile-viewport-height',height+'px');
    root.style.setProperty('--mobile-viewport-top',(keyboard?Math.max(0,viewport?.offsetTop||0):0)+'px');
    root.style.setProperty('--mobile-safe-bottom',keyboard?'0px':'var(--app-safe-bottom)');
  };
  const schedule=()=>{if(!frame)frame=requestAnimationFrame(update);};
  const listen=(target,type)=>{target?.addEventListener(type,schedule);cleanups.push(()=>target?.removeEventListener(type,schedule));};
  for(const type of ['resize','scroll','scrollend'])listen(viewport,type);
  for(const type of ['resize','pageshow','orientationchange'])listen(window,type);
  update();
  return ()=>{cancelAnimationFrame(frame);cleanups.forEach(fn=>fn());clear();};
}
