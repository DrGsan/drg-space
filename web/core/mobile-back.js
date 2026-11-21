import {mobileApp} from './mobile.js';

const visible=element=>!!element&&!element.disabled&&element.getClientRects().length>0&&getComputedStyle(element).visibility!=='hidden';
const blocked='.modal-overlay,[aria-modal="true"],[role="dialog"]:not(.voice-call.is-minimized),[role="menu"],[data-chat-emoji-panel],[data-attach-menu]';
const controls='input,textarea,select,button,a,summary,video,audio,canvas,iframe,[contenteditable]:not([contenteditable="false"]),[role="slider"],[draggable="true"],[data-no-back-swipe]';
// Reuse the visible button's existing save/permissions/back behavior. A gesture
// never changes routes or editor state directly.
export function mobileBackButton(root){
  if([...document.querySelectorAll(blocked)].some(visible))return null;
  const selectors=[
    '.tasks-shell.has-properties [data-close-task-properties]',
    '.chat-shell.is-list-searching:not(.room-open) [data-close-room-search]',
    '.chat-shell.room-open [data-close-chat-search]',
    '.chat-shell.room-open [data-chat-back]',
    '.chat-shell.room-open [data-fp-back]',
    '.notes-shell:not(.mobile-notes-list) #notes-main-menu',
    '.workspace.files-open .drive-back',
    '#files-main-menu',
    '.mobile-secondary-back',
  ];
  return selectors.map(selector=>root.querySelector(selector)).find(visible)||null;
}
function scrollsHorizontally(target,root){
  for(let element=target;element&&element!==root;element=element.parentElement){
    if(element.scrollWidth>element.clientWidth+2&&/auto|scroll|overlay/.test(getComputedStyle(element).overflowX))return true;
  }
  return false;
}
export function bindMobileBack(root){
  let gesture=null,hint=null,suppressUntil=0,releasePoint=null;
  const reset=()=>{gesture=null;hint?.remove();hint=null;};
  const available=()=>mobileApp()&&(!window.visualViewport||Math.abs(window.visualViewport.scale-1)<.01);
  const start=event=>{
    reset();
    if(!available()||event.touches.length!==1)return;
    suppressUntil=0;releasePoint=null;
    const touch=event.touches[0],rect=root.getBoundingClientRect(),target=event.target;
    if(touch.clientX<rect.left||touch.clientX>rect.left+28||touch.clientY<rect.top||touch.clientY>rect.bottom)return;
    if(!(target instanceof Element)||target.closest(controls)||!window.getSelection()?.isCollapsed||scrollsHorizontally(target,root))return;
    const button=mobileBackButton(root);if(!button)return;
    gesture={id:touch.identifier,x:touch.clientX,y:touch.clientY,started:performance.now(),button,claimed:false,rect};
  };
  const move=event=>{
    if(!gesture)return;
    if(event.touches.length!==1||!available()||mobileBackButton(root)!==gesture.button){reset();return;}
    const touch=[...event.touches].find(t=>t.identifier===gesture.id);if(!touch){reset();return;}
    const dx=touch.clientX-gesture.x,dy=Math.abs(touch.clientY-gesture.y);
    // Decide once: a vertical start, reversal or excessive drift belongs to
    // scrolling. Only a clear rightward movement claims the touch sequence.
    if(dx< -8||dy>60||(!gesture.claimed&&dy>=12&&dy>=dx)){reset();return;}
    if(!gesture.claimed){if(dx<12||dx<dy*1.5)return;gesture.claimed=true;}
    if(!event.cancelable){reset();return;}
    event.preventDefault();
    if(!hint){hint=document.createElement('span');hint.className='mobile-back-hint';hint.setAttribute('aria-hidden','true');hint.textContent='‹';document.body.append(hint);}
    const progress=Math.min(1,Math.max(0,dx)/72);
    hint.style.left=(gesture.rect.left+8+progress*24)+'px';
    hint.style.top=(Math.max(gesture.rect.top+30,Math.min(gesture.y,gesture.rect.bottom-30))-22)+'px';
    hint.style.opacity=String(.4+.6*progress);hint.classList.toggle('is-ready',progress===1);
  };
  const end=event=>{
    if(!gesture)return;
    const touch=[...event.changedTouches].find(t=>t.identifier===gesture.id);
    const current=gesture,dx=touch?touch.clientX-current.x:0,dy=touch?Math.abs(touch.clientY-current.y):Infinity;
    const commit=current.claimed&&event.touches.length===0&&dx>=72&&dx>dy*1.5&&dy<=60&&performance.now()-current.started<=1200&&available()&&mobileBackButton(root)===current.button;
    if(current.claimed&&event.cancelable)event.preventDefault();reset();
    if(commit){current.button.focus({preventScroll:true});if(mobileBackButton(root)===current.button)current.button.click();}
    if(current.claimed){suppressUntil=performance.now()+400;releasePoint=touch?{x:touch.clientX,y:touch.clientY}:null;}
  };
  const click=event=>{if(performance.now()<suppressUntil&&releasePoint&&Math.hypot(event.clientX-releasePoint.x,event.clientY-releasePoint.y)<32){event.preventDefault();event.stopImmediatePropagation();}};
  root.addEventListener('touchstart',start,{passive:true});
  root.addEventListener('touchmove',move,{passive:false});
  root.addEventListener('touchend',end,{passive:false});
  root.addEventListener('touchcancel',reset,{passive:true});
  root.addEventListener('click',click,true);
  window.addEventListener('resize',reset);window.addEventListener('pagehide',reset);window.addEventListener('blur',reset);document.addEventListener('visibilitychange',reset);
  return ()=>{reset();root.removeEventListener('touchstart',start);root.removeEventListener('touchmove',move);root.removeEventListener('touchend',end);root.removeEventListener('touchcancel',reset);root.removeEventListener('click',click,true);window.removeEventListener('resize',reset);window.removeEventListener('pagehide',reset);window.removeEventListener('blur',reset);document.removeEventListener('visibilitychange',reset);};
}
