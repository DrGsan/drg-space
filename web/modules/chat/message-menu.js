import {tText,tHtml} from "../../core/i18n.js";
import {esc} from '../../core/ui.js';
import {chatIcon} from './icons.js';
import './message-menu.css';
import './menus.css';

// A scroll or a second finger cancels the hold; a completed hold consumes the
// following click so a media thumbnail doesn't open behind the action menu.
export function bindMessagePress(list,selector,open){
  let timer,point,suppressUntil=0;
  const cancel=()=>{clearTimeout(timer);point=null;};
  const down=e=>{
    cancel();
    if(e.pointerType!=='touch'||!e.isPrimary)return;
    const article=e.target.closest(selector);
    if(!article||e.target.closest('a,video[controls],audio,input,textarea'))return;
    point={x:e.clientX,y:e.clientY};
    timer=setTimeout(()=>{suppressUntil=performance.now()+900;open(article,e.clientX,e.clientY,true);},500);
  };
  const move=e=>{if(point&&Math.hypot(e.clientX-point.x,e.clientY-point.y)>10)cancel();};
  const context=e=>{
    const article=e.target.closest(selector);
    if(!article||e.target.closest('video[controls],audio'))return;
    e.preventDefault();cancel();
    if(performance.now()>=suppressUntil)open(article,e.clientX,e.clientY,e.pointerType==='touch');
  };
  const click=e=>{if(performance.now()<suppressUntil){e.preventDefault();e.stopPropagation();}};
  const keydown=e=>{
    if(e.key!=='ContextMenu'&&!(e.shiftKey&&e.key==='F10')&&!(e.key==='Enter'&&e.target.matches(selector)))return;
    const article=e.target.closest(selector);if(!article)return;
    e.preventDefault();e.stopPropagation();const rect=article.getBoundingClientRect();open(article,rect.right,rect.bottom,false);
  };
  // Scrolling caused by a late image/layout update is not a finger gesture.
  // Native touch scrolling sends pointercancel; movement and wheels cancel too.
  const events={pointerdown:down,pointermove:move,pointerup:cancel,pointercancel:cancel,pointerleave:cancel,contextmenu:context,wheel:cancel,keydown};
  for(const [type,fn] of Object.entries(events))list.addEventListener(type,fn);
  list.addEventListener('click',click,true);
  return()=>{cancel();for(const [type,fn] of Object.entries(events))list.removeEventListener(type,fn);list.removeEventListener('click',click,true);};
}

export function messageMenu({article,trigger,x,y,actions,reactions,footer='',touch=false,onClose=()=>{}}){
  const focused=touch||matchMedia('(max-width:760px), (pointer:coarse)').matches;
  const menu=document.createElement('div');menu.className='chat-popup chat-message-menu';
  menu.setAttribute('role','menu');menu.setAttribute('aria-label',tText("chat:messageActions"));
  menu.innerHTML=(reactions?`<div class="chat-quick-reactions" role="group" aria-label="${tHtml("chat:quickReactions")}">${reactions.emojis.map(emoji=>`<button type="button" data-quick-reaction="${emoji}" aria-label="${tHtml("chat:reactWith", {value0:(emoji)})}">${emoji}</button>`).join('')}<button type="button" data-more-reactions aria-label="${tHtml("chat:allReactions")}">${chatIcon('down')}</button></div>`:'')+actions.map(a=>`<button role="menuitem" type="button" data-message-action="${esc(a.id)}"${a.destructive?' class="is-destructive"':''}>${chatIcon(a.icon)}<span>${esc(a.label)}</span></button>`).join('')+(footer?`<div class="chat-message-menu-date">${chatIcon('check')}<time>${esc(footer)}</time></div>`:'');
  let layer,preview,closed=false,frame,removeTimer,app,wasInert,layout=()=>{},awaitingRelease=touch,consumeClickUntil=0;
  const viewport=window.visualViewport;
  const close=(restoreFocus=false,immediate=false)=>{
    if(closed)return;closed=true;cancelAnimationFrame(frame);
    document.removeEventListener('keydown',keys,true);document.removeEventListener('pointerup',released,true);document.removeEventListener('pointerdown',outside);window.removeEventListener('resize',resized);viewport?.removeEventListener('resize',resized);viewport?.removeEventListener('scroll',resized);
    trigger?.setAttribute('aria-expanded','false');
    if(app)app.inert=wasInert;
    if(layer){
      layer.classList.remove('is-open');layer.setAttribute('aria-hidden','true');layer.inert=true;
      if(immediate||matchMedia('(prefers-reduced-motion:reduce)').matches)layer.remove();
      else removeTimer=setTimeout(()=>layer.remove(),180);
    }else menu.remove();
    if(restoreFocus&&article.isConnected)(trigger?.offsetParent?trigger:article).focus({preventScroll:true});
    onClose();
  };
  const resized=()=>{if(!closed)layout();};
  const released=()=>{if(awaitingRelease){awaitingRelease=false;consumeClickUntil=performance.now()+200;}};
  const outside=e=>{if(!layer&&!menu.contains(e.target)&&!trigger?.contains(e.target))close();};
  const keys=e=>{
    const buttons=[...menu.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);}
    if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
      e.preventDefault();buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus({preventScroll:true});
    }
    if(e.key==='Tab'){e.preventDefault();if(focused)buttons[(index+(e.shiftKey?-1:1)+buttons.length)%buttons.length]?.focus();else close(true);}
  };
  const clamp=(n,min,max)=>Math.max(min,Math.min(n,max));
  if(focused){
    if(document.activeElement?.matches('input,textarea'))document.activeElement.blur();
    layer=document.createElement('div');layer.className='chat-context-layer';layer.setAttribute('role','dialog');layer.setAttribute('aria-modal','true');layer.setAttribute('aria-label',tText("chat:messageMenu"));
    preview=document.createElement('div');preview.className='chat-shell chat-context-preview';preview.setAttribute('aria-hidden','true');
    const clone=article.cloneNode(true);
    for(const el of [clone,...clone.querySelectorAll('*')]){
      el.removeAttribute('id');for(const attr of [...el.attributes])if(attr.name.startsWith('data-'))el.removeAttribute(attr.name);
      if(el.matches('button,a,[tabindex]'))el.setAttribute('tabindex','-1');
    }
    clone.querySelectorAll('.chat-message-actions,.chat-message-avatar').forEach(el=>el.remove());
    clone.querySelectorAll('video').forEach(video=>{const image=document.createElement('img');image.src=video.poster||'';image.alt='';image.style.cssText=video.style.cssText;video.replaceWith(image);});
    preview.append(clone);layer.append(preview,menu);document.body.append(layer);
    // Lifting the finger which opened the menu must not select an action or
    // dismiss its backdrop. A fresh tap is always deliberate.
    layer.addEventListener('pointerdown',()=>{awaitingRelease=false;consumeClickUntil=0;},true);
    layer.addEventListener('click',e=>{if(awaitingRelease||performance.now()<consumeClickUntil){e.preventDefault();e.stopImmediatePropagation();}},true);
    layer.addEventListener('click',e=>{if(!e.target.closest('.chat-message-menu'))close();});
    layer.addEventListener('touchmove',e=>{if(!e.target.closest('.chat-message-menu'))e.preventDefault();},{passive:false});
    layer.oncontextmenu=e=>e.preventDefault();
    app=document.querySelector('#app');if(app){wasInert=app.inert;app.inert=true;}
    frame=requestAnimationFrame(()=>layer.classList.add('is-open'));
  }else{
    document.body.append(menu);
  }
  layout=()=>{
    const left=viewport?.offsetLeft||0,top=viewport?.offsetTop||0,width=viewport?.width||innerWidth,height=viewport?.height||innerHeight;
    menu.style.maxHeight=Math.max(60,height-32)+'px';
    const box={width:menu.offsetWidth,height:menu.offsetHeight};
    if(preview){
      const source=article.getBoundingClientRect(),previewWidth=Math.min(source.width,width-24),previewHeight=Math.min(source.height,Math.max(0,height-box.height-40));
      const px=clamp(source.left,left+12,left+width-previewWidth-12),py=clamp(source.top,top+12,top+height-previewHeight-box.height-24);
      Object.assign(preview.style,{left:px+'px',top:py+'px',width:previewWidth+'px',height:previewHeight+'px'});
      menu.style.left=clamp(article.classList.contains('mine')?px+previewWidth-box.width:px,left+12,left+width-box.width-12)+'px';
      menu.style.top=(py+previewHeight+10)+'px';
    }else{
      menu.style.left=clamp(x-box.width,left+8,left+width-box.width-8)+'px';menu.style.top=clamp(y,top+8,top+height-box.height-8)+'px';
    }
  };layout();
  trigger?.setAttribute('aria-expanded','true');
  menu.querySelectorAll('button').forEach(button=>button.onclick=()=>{
    const action=actions.find(a=>a.id===button.dataset.messageAction);close();if(button.dataset.quickReaction)reactions.choose(button.dataset.quickReaction);else if(button.hasAttribute('data-more-reactions'))reactions.more();else action?.run();
  });
  document.addEventListener('keydown',keys,true);document.addEventListener('pointerup',released,true);document.addEventListener('pointerdown',outside);window.addEventListener('resize',resized);viewport?.addEventListener('resize',resized);viewport?.addEventListener('scroll',resized);
  menu.querySelector('button')?.focus({preventScroll:true});
  return {element:menu,close,destroy(){clearTimeout(removeTimer);if(closed)layer?.remove();else close(false,true);}};
}
