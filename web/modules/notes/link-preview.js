import {tText} from "../../core/i18n.js";
import {createExtension} from '@blocknote/core';
import {Plugin,PluginKey,NodeSelection,TextSelection} from 'prosemirror-state';
import {Decoration,DecorationSet} from 'prosemirror-view';
import {closeHistory} from 'prosemirror-history';
import {yUndoPluginKey} from 'y-prosemirror';
import {csrfCookie} from '../../core/auth.js';
import {abortable} from '../../core/request.js';
import './link-preview.css';

export function linkParagraphSpec(spec){
  return {...spec,config:{...spec.config,propSchema:{...spec.config.propSchema,linkDisplay:{default:'auto',values:['auto','url','bookmark','mention']},title:{default:''},description:{default:''},url:{default:''}}}};
}
function webURL(value){
  try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}
}
function paragraphLink(node){
  if(!node||node.type.name!=='paragraph'||node.attrs.linkDisplay==='url')return null;
  let hidden=false;node.forEach(child=>{if(child.marks.some(mark=>mark.type.name==='spoiler'))hidden=true;});if(hidden)return null;
  const text=node.textContent.trim();let href=null,onlyLink=true;
  node.forEach(child=>{
    if(child.type.name==='hardBreak')return;
    if(!child.isText){onlyLink=false;return;}
    const link=child.marks.find(mark=>mark.type.name==='link')?.attrs.href;
    if(!link||(href&&href!==link))onlyLink=false;
    if(link)href=link;
  });
  const value=onlyLink&&href?href:/^https?:\/\/\S+$/i.test(text)?text:null,url=webURL(value);
  if(!url)return null;
  const label=webURL(text)===url?'':text.split(value)[0].trim();
  return {url,label:node.attrs.title||(label&&label!==url?label.slice(0,200):''),storedTitle:node.attrs.title||'',description:node.attrs.description||'',image:node.attrs.url||'',mode:node.attrs.linkDisplay==='mention'?'mention':'bookmark'};
}
let closeMenu;
const displays=[['bookmark',tText("notes:preview"),tText("notes:cardWithTitleDescriptionAndImage")],['url',tText("notes:link"),tText("notes:regularTextLink")],['mention',tText("notes:mention"),tText("notes:compactLinkWithPageName")]];
function changeLink(editor,change){
  const view=editor.prosemirrorView;
  const boundary=()=>{yUndoPluginKey.getState(view.state)?.undoManager.stopCapturing();view.dispatch(closeHistory(view.state.tr));};
  boundary();change();boundary();
}
function chooseDisplay(editor,id,anchor,{pasted=false}={}){
  closeMenu?.();
  const block=editor.getBlock(id);if(!block||!editor.isEditable)return;
  const menu=document.createElement('div');menu.className='note-link-paste-menu';menu.setAttribute('role','menu');menu.setAttribute('aria-label',pasted?tText("notes:insertLinkAs"):tText("notes:displayLink"));
  const heading=document.createElement('p');heading.textContent=pasted?tText("notes:insertAs"):tText("notes:displayAs");menu.append(heading);
  function close(restore=false){menu.remove();document.removeEventListener('pointerdown',outside,true);document.removeEventListener('keydown',keys,true);window.removeEventListener('resize',close);window.removeEventListener('scroll',close,true);if(closeMenu===close)closeMenu=null;if(restore===true&&anchor?.isConnected)anchor.focus();}
  const outside=e=>{if(!menu.contains(e.target)&&e.target!==anchor)close();};
  function keys(e){
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);return;}
    if(!menu.contains(document.activeElement))return;
    if(['ArrowDown','ArrowUp','Home','End','Tab'].includes(e.key)){
      e.preventDefault();const items=[...menu.querySelectorAll('button')],index=items.indexOf(document.activeElement);
      items[e.key==='Home'?0:e.key==='End'?items.length-1:(index+(e.key==='ArrowUp'||(e.key==='Tab'&&e.shiftKey)?items.length-1:1))%items.length].focus();
    }
  }
  for(const [mode,label,description] of displays){
    const button=document.createElement('button');button.type='button';button.setAttribute('role','menuitemradio');button.setAttribute('aria-checked',String((block.props.linkDisplay==='auto'?'bookmark':block.props.linkDisplay)===mode));
    const title=document.createElement('strong');title.textContent=label;const detail=document.createElement('small');detail.textContent=description;button.append(title,detail);
    button.onclick=()=>{if(editor.isEditable&&editor.getBlock(id)){changeLink(editor,()=>editor.updateBlock(id,{props:{linkDisplay:mode}}));editor.focus();if(mode==='url')editor.setTextCursorPosition(id,'end');}close();};menu.append(button);
  }
  document.body.append(menu);closeMenu=close;
  const rect=anchor?.getBoundingClientRect()||editor.prosemirrorView?.coordsAtPos(editor.prosemirrorView.state.selection.from),width=Math.min(286,innerWidth-24);
  menu.style.width=width+'px';menu.style.left=Math.max(12,Math.min(rect?.left||12,innerWidth-width-12))+'px';menu.style.top=Math.max(12,Math.min((rect?.bottom||80)+6,innerHeight-menu.offsetHeight-12))+'px';
  document.addEventListener('pointerdown',outside,true);document.addEventListener('keydown',keys,true);window.addEventListener('resize',close);window.addEventListener('scroll',close,true);
  menu.querySelector('button')?.focus();
  return close;
}
export function linkPasteHandler(event,editor){
  if(!event.clipboardData||event.clipboardData.files.length||event.clipboardData.types.includes('blocknote/html'))return false;
  const text=event.clipboardData.getData('text/plain').trim();
  if(!/^https?:\/\/\S+$/i.test(text))return false;
  const url=webURL(text),current=editor.getTextCursorPosition().block;
  // Pasting into prose or a selection retains the ordinary inline link behavior.
  if(!url||current.type!=='paragraph'||current.content?.length||current.children.length||!editor.prosemirrorView.state.selection.empty)return false;
  event.preventDefault();
  changeLink(editor,()=>editor.updateBlock(current,{props:{linkDisplay:'bookmark',title:'',description:'',url:''},content:[{type:'link',href:url,content:url}]}));
  requestAnimationFrame(()=>{if(editor.isEditable&&editor.getBlock(current.id))chooseDisplay(editor,current.id,editor.prosemirrorView.dom.querySelector('.note-link-card-wrap[data-link-block="'+CSS.escape(current.id)+'"]'),{pasted:true});});
  return true;
}

// Preserve canonical paragraphs and URLs in exports and CRDT. The card replaces
// their visible text; explicit display choices are ordinary paragraph props.
export const linkPreviews=createExtension(({editor,options})=>{
  const key=new PluginKey('drg-link-previews'),cache=new Map(),queue=[];
  let running=0,disposed=false,observer;
  const controller=new AbortController();
  options.signal.addEventListener('abort',()=>{disposed=true;controller.abort();observer?.disconnect();closeMenu?.();queue.splice(0).forEach(item=>item.resolve(null));cache.clear();},{once:true});
  async function pump(){
    if(disposed||running>=3||!queue.length)return;
    const {url,resolve}=queue.shift();running++;
    try{
      const csrf=csrfCookie();if(!csrf){resolve(null);return;}
      resolve(await abortable(async signal=>{
        const response=await fetch('/api/note-link-preview',{method:'POST',credentials:'same-origin',signal,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({url})});
        return response.ok?await response.json():null;
      },{signal:controller.signal,timeoutMs:25000}));
    }catch{resolve(null);}finally{running--;pump();}
  }
  function metadata(url){if(!cache.has(url)){cache.set(url,new Promise(resolve=>queue.push({url,resolve})));pump();}return cache.get(url);}
  function card(info,id){
    const {url,label,mode}=info;
    const wrapper=document.createElement('div');wrapper.className='note-link-card-wrap'+(mode==='mention'?' note-link-mention':'');wrapper.contentEditable='false';wrapper.dataset.linkBlock=id;
    const link=document.createElement('a');link.className='note-link-card';link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';
    const copy=document.createElement('span');copy.className='note-link-card-copy';
    const title=document.createElement('strong');title.textContent=label||new URL(url).hostname;
    const description=document.createElement('span');description.className='note-link-description';description.textContent=info.description;description.hidden=!info.description;
    const address=document.createElement('small');address.textContent=url;
    copy.append(title,description,address);link.append(copy);wrapper.append(link);
    if(mode!=='mention'&&info.image){const image=document.createElement('img');image.src=info.image;image.alt='';image.loading='lazy';link.append(image);}
    if(!options.readOnly){
      const controls=document.createElement('div');controls.className='note-link-controls';
      const menu=document.createElement('button');menu.type='button';menu.className='note-link-options';menu.textContent='•••';menu.setAttribute('aria-label',tText("notes:displayLink"));menu.setAttribute('aria-haspopup','menu');menu.onclick=()=>chooseDisplay(editor,id,menu);controls.append(menu);
      const edit=document.createElement('button');edit.type='button';edit.className='note-link-options';edit.textContent='↗';edit.setAttribute('aria-label',tText("notes:changeLinkAddress"));
      edit.onclick=()=>{
        if(!editor.isEditable)return;closeMenu?.();
        const form=document.createElement('form');form.className='note-link-edit note-link-edit-popup';form.setAttribute('role','dialog');form.setAttribute('aria-label',tText("notes:changeLink"));
        const addressLabel=document.createElement('label');addressLabel.textContent=tText("notes:linkAddress");
        const input=document.createElement('input');input.type='url';input.value=url;input.required=true;input.setAttribute('aria-label',tText("notes:linkAddress"));addressLabel.append(input);
        const textLabel=document.createElement('label');textLabel.textContent=tText("notes:linkText");
        const caption=document.createElement('input');caption.type='text';caption.value=label;caption.maxLength=200;caption.placeholder=tText("notes:usePageName");caption.setAttribute('aria-label',tText("notes:linkText"));textLabel.append(caption);
        const actions=document.createElement('div');actions.className='note-link-edit-actions';
        const cancel=document.createElement('button');cancel.type='button';cancel.textContent=tText("notes:cancel");
        const save=document.createElement('button');save.type='submit';save.textContent=tText("notes:save");actions.append(cancel,save);form.append(addressLabel,textLabel,actions);document.body.append(form);
        const rect=edit.getBoundingClientRect(),width=Math.min(340,innerWidth-24);form.style.width=width+'px';form.style.left=Math.max(12,Math.min(rect.left,innerWidth-width-12))+'px';form.style.top=Math.max(12,Math.min(rect.bottom+7,innerHeight-form.offsetHeight-12))+'px';
        function close(){form.remove();document.removeEventListener('pointerdown',outside,true);document.removeEventListener('keydown',keys,true);window.removeEventListener('resize',close);window.removeEventListener('scroll',close,true);if(closeMenu===close)closeMenu=null;}
        const outside=event=>{if(!form.contains(event.target)&&event.target!==edit)close();};
        const keys=event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();edit.isConnected&&edit.focus();}};
        cancel.onclick=()=>{close();edit.isConnected&&edit.focus();};
        form.onsubmit=event=>{
          event.preventDefault();const next=webURL(input.value.trim());if(!next){input.setCustomValidity(tText("notes:enterTheFullHttpOrHttpsAddress"));input.reportValidity();return;}
          if(editor.isEditable&&editor.getBlock(id))changeLink(editor,()=>editor.updateBlock(id,{props:{title:'',description:'',url:''},content:[{type:'link',href:next,content:caption.value.trim()||next}]}));close();editor.focus();
        };
        input.oninput=()=>input.setCustomValidity('');document.addEventListener('pointerdown',outside,true);document.addEventListener('keydown',keys,true);window.addEventListener('resize',close);window.addEventListener('scroll',close,true);closeMenu=close;input.focus();input.select();
      };controls.append(edit);wrapper.append(controls);
    }
    let loaded=false;
    wrapper.loadPreview=()=>{if(loaded)return;loaded=true;metadata(url).then(data=>{
      if(disposed||!data)return;
      title.textContent=info.storedTitle||data.title||label||new URL(url).hostname;
      const detail=info.description||data.description||'';description.textContent=detail;description.hidden=!detail;
      if(mode!=='mention'&&!info.image&&data.image?.startsWith('data:image/jpeg;base64,')){
        const image=document.createElement('img');image.src=data.image;image.alt='';image.loading='lazy';link.append(image);
      }
    });};
    queueMicrotask(()=>{if(!disposed)observer?.observe(wrapper);});return wrapper;
  }
  function decorations(doc){
    const items=[];
    doc.descendants((node,pos,parent)=>{
      const info=paragraphLink(node);
      if(info){items.push(Decoration.node(pos,pos+node.nodeSize,{class:'note-link-source','aria-hidden':'true'}));items.push(Decoration.widget(pos+node.nodeSize,()=>card(info,parent.attrs.id),{key:'link:'+parent.attrs.id+':'+JSON.stringify(info),side:-1,stopEvent:()=>true,destroy:dom=>observer?.unobserve(dom)}));}
    });
    return DecorationSet.create(doc,items);
  }
  return {key:'drgLinkPreviews',prosemirrorPlugins:[new Plugin({
    key,
    state:{init:(_,state)=>decorations(state.doc),apply:(tr,current)=>tr.docChanged?decorations(tr.doc):current},
    appendTransaction:(_,old,state)=>{
      if(!state.selection.empty||state.selection instanceof NodeSelection)return null;
      const $from=state.selection.$from;
      for(let depth=$from.depth;depth>0;depth--){
        const node=$from.node(depth);if(paragraphLink(node))return state.tr.setSelection(NodeSelection.create(state.doc,$from.before(depth)-1)).setMeta('addToHistory',false);
      }
      return null;
    },
    props:{
      decorations:state=>key.getState(state),
      handleTextInput:(view,from,to,text)=>{
        const $from=view.state.doc.resolve(from),$to=view.state.doc.resolve(to),node=$from.parent;
        if(node.type.name!=='paragraph'||node.attrs.linkDisplay!=='auto'||!$from.sameParent($to))return false;
        const next=node.textContent.slice(0,$from.parentOffset)+text+node.textContent.slice($to.parentOffset);
        if(!/^https?:\/\/\S+$/i.test(next)||!webURL(next))return false;
        // A URL being typed must not turn into an atomic card mid-keystroke.
        view.dispatch(view.state.tr.setNodeMarkup($from.before(),undefined,{...node.attrs,linkDisplay:'url'}).insertText(text,from,to));return true;
      },
      handleKeyDown:(view,event)=>{
        const selection=view.state.selection;
        if(!(selection instanceof NodeSelection)||!paragraphLink(selection.node.firstChild))return false;
        if(event.key==='Enter'){
          event.preventDefault();const next=editor.insertBlocks([{type:'paragraph',content:''}],selection.node.attrs.id,'after')[0];editor.setTextCursorPosition(next,'start');return true;
        }
        if(['ArrowDown','ArrowRight','ArrowUp','ArrowLeft'].includes(event.key)){
          const forward=event.key==='ArrowDown'||event.key==='ArrowRight',pos=forward?selection.to:selection.from;
          const next=TextSelection.near(view.state.doc.resolve(pos),forward?1:-1);
          if(next.from===selection.from||next.from===selection.to||next instanceof NodeSelection)return false;
          view.dispatch(view.state.tr.setSelection(next));return true;
        }
        return false;
      },
    },
    view:view=>{
      observer=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){observer.unobserve(entry.target);entry.target.loadPreview();}}),{rootMargin:'200px'});
      view.dom.querySelectorAll('.note-link-card-wrap').forEach(node=>observer.observe(node));const currentObserver=observer;return {destroy(){currentObserver.disconnect();}};
    },
  })]};
});
