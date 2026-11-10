import {tText} from "../../core/i18n.js";
import {chatIcon} from './icons.js';
import './menus.css';

export function attachmentMenu(trigger,onChoose){
  const menu=document.createElement('div');
  menu.className='chat-popup chat-attach-menu';menu.dataset.attachMenu='';
  menu.setAttribute('role','menu');menu.setAttribute('aria-label',tText("chat:attach"));
  menu.innerHTML=[['preview','image',tText("chat:photoOrVideo")],['file','file',tText("chat:file")],['camera','camera',tText("chat:camera")],['voice','mic',tText("chat:voiceMessage")],['video_note','video',tText("chat:videoNote")]].map(([mode,icon,label])=>`<button type="button" role="menuitem" data-upload-mode="${mode}">${chatIcon(icon)}<span>${label}</span></button>`).join('');
  document.body.append(menu);trigger.setAttribute('aria-expanded','true');
  const viewport=window.visualViewport;
  let closed=false;
  const close=(focus=false)=>{
    if(closed)return;closed=true;menu.remove();observer.disconnect();trigger.setAttribute('aria-expanded','false');
    document.removeEventListener('keydown',keys,true);document.removeEventListener('pointerdown',outside);document.removeEventListener('scroll',layout,true);window.removeEventListener('resize',layout);viewport?.removeEventListener('resize',layout);viewport?.removeEventListener('scroll',layout);
    if(focus&&trigger.isConnected)trigger.focus({preventScroll:true});
  };
  const outside=e=>{if(!menu.contains(e.target)&&!trigger.contains(e.target))close();};
  const keys=e=>{
    const buttons=[...menu.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);}
    if(e.key==='Tab')close();
    if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
      e.preventDefault();e.stopPropagation();buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus({preventScroll:true});
    }
  };
  const layout=()=>{
    if(!trigger.isConnected)return close();
    const left=viewport?.offsetLeft||0,top=viewport?.offsetTop||0,width=viewport?.width||innerWidth,height=viewport?.height||innerHeight,anchor=trigger.getBoundingClientRect();
    menu.style.maxHeight=Math.max(44,height-16)+'px';
    const box={width:menu.offsetWidth,height:menu.offsetHeight},clamp=(n,min,max)=>Math.max(min,Math.min(n,max));
    menu.style.left=clamp(anchor.left,left+8,left+width-box.width-8)+'px';
    menu.style.top=clamp(anchor.top-box.height-8,top+8,top+height-box.height-8)+'px';
  };
  const observer=new MutationObserver(()=>{if(!trigger.isConnected)close();});observer.observe(document.body,{childList:true,subtree:true});
  menu.querySelectorAll('button').forEach(button=>button.onclick=()=>{close();onChoose(button.dataset.uploadMode);});
  document.addEventListener('keydown',keys,true);document.addEventListener('pointerdown',outside);document.addEventListener('scroll',layout,true);window.addEventListener('resize',layout);viewport?.addEventListener('resize',layout);viewport?.addEventListener('scroll',layout);
  layout();menu.querySelector('button').focus({preventScroll:true});
  return {close,element:menu};
}
