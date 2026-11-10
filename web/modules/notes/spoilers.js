import {tText} from "../../core/i18n.js";
import {createStyleSpec,createExtension} from '@blocknote/core';
import './spoilers.css';

function spoilerDOM(){
 const dom=document.createElement('span'),contentDOM=document.createElement('span');
 dom.className='drg-spoiler';dom.contentEditable='false';dom.dataset.spoiler='';dom.tabIndex=0;dom.setAttribute('role','button');dom.setAttribute('aria-label',tText("notes:showHiddenText"));
 contentDOM.className='drg-spoiler-content';contentDOM.setAttribute('aria-hidden','true');dom.append(contentDOM);
 // Disclosure changes presentation attributes, never the ProseMirror mark.
 return {dom,contentDOM,ignoreMutation:mutation=>mutation.type==='attributes'&&(mutation.target===dom||mutation.target===contentDOM)};
}
export const spoilerStyle=createStyleSpec({type:'spoiler',propSchema:'boolean'},{render:spoilerDOM,parse:element=>element.hasAttribute('data-spoiler')?true:undefined});
export const spoilerShortcuts=createExtension(({editor})=>({key:'drg-spoiler-shortcuts',keyboardShortcuts:{'Mod-Shift-p':()=>{
 if(!editor.isEditable||editor.prosemirrorView.state.selection.empty)return false;
 editor.toggleStyles({spoiler:true});return true;
}}}));

// Dust is painted on the inline mark itself. Native scrolling and pinch zoom
// move it with the text, without a viewport canvas or JS geometry tracking.
export function bindSpoilers(host){
 let pointer;
 const down=event=>{pointer={x:event.clientX,y:event.clientY};};
 const reveal=event=>{
  const selector='.drg-spoiler:not(.is-revealed)',anchor=getSelection()?.anchorNode;
  const span=event.target.closest?.(selector)||(event.type==='keydown'?(document.activeElement?.closest?.(selector)||(anchor?.nodeType===Node.ELEMENT_NODE?anchor:anchor?.parentElement)?.closest?.(selector)):null);if(!span||!host.contains(span))return;
  if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;
  if(event.type==='click'&&pointer&&Math.hypot(event.clientX-pointer.x,event.clientY-pointer.y)>5)return;
  event.preventDefault();event.stopPropagation();
  const siblings=[...(span.closest('.bn-inline-content')||span.parentElement).querySelectorAll('.drg-spoiler')],index=siblings.indexOf(span),group=[span];
  const adjacent=(left,right)=>{const range=document.createRange();range.setStartAfter(left);range.setEndBefore(right);return range.toString()==='';};
  for(let i=index-1;i>=0&&adjacent(siblings[i],group[0]);i--)group.unshift(siblings[i]);
  for(let i=index+1;i<siblings.length&&adjacent(group.at(-1),siblings[i]);i++)group.push(siblings[i]);
  for(const item of group){item.classList.add('is-revealed');item.removeAttribute('contenteditable');item.removeAttribute('role');item.removeAttribute('tabindex');item.removeAttribute('aria-label');item.querySelector('.drg-spoiler-content')?.removeAttribute('aria-hidden');}
 };
 host.addEventListener('pointerdown',down,true);host.addEventListener('click',reveal,true);host.addEventListener('keydown',reveal,true);
 return()=>{host.removeEventListener('pointerdown',down,true);host.removeEventListener('click',reveal,true);host.removeEventListener('keydown',reveal,true);};
}
