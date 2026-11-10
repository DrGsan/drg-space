import {tText} from "../../core/i18n.js";
let runtime;
const snapshots=new WeakMap();
const load=()=>runtime??=import('./mermaid-runtime.js').catch(error=>{runtime=null;throw error;});
export function mermaidPreview(rendered,block,editor){
 if(block.props.language!=='mermaid')return rendered;
 const preview=document.createElement('div');preview.className='note-mermaid-preview';preview.contentEditable='false';preview.setAttribute('role','img');preview.setAttribute('aria-label',tText("notes:mermaidDiagram"));
 rendered.dom.append(preview);
 const cache=snapshots.get(editor)||new Map();snapshots.set(editor,cache);
 let timer,disposed=false,revision=0,lastSource;
 const source=()=>{const content=editor.getBlock(block.id)?.content;return typeof content==='string'?content:(content||[]).map(p=>p.text||'').join('');};
 preview.setAttribute('aria-busy','true');
 const remember=value=>{cache.delete(block.id);cache.set(block.id,value);if(cache.size>30)cache.delete(cache.keys().next().value);};
 const initial=cache.get(block.id);if(initial){preview.innerHTML=initial.svg;preview.classList.toggle('is-error',initial.error);}
 const draw=async()=>{
  const text=source(),key=text+'\0'+document.documentElement.dataset.scheme;if(key===lastSource)return;lastSource=key;
  const current=++revision;preview.setAttribute('aria-busy','true');
  try{const {diagram}=await load();if(disposed||revision!==current)return;const svg=await diagram(text,document.documentElement.dataset.scheme==='dark',()=>!disposed&&revision===current);if(disposed||revision!==current)return;preview.innerHTML=svg;preview.classList.remove('is-error');remember({svg,error:false});}
  catch(error){if(disposed||revision!==current)return;preview.replaceChildren();const message=document.createElement('span');message.textContent=/слишком|Настройки|Внешние/.test(error.message)?error.message:tText("notes:failedToCreateTheDiagramPleaseCheckThe");preview.append(message);preview.classList.add('is-error');remember({svg:preview.innerHTML,error:true});}
  finally{if(!disposed&&revision===current)preview.setAttribute('aria-busy','false');}
 };
 const schedule=()=>{clearTimeout(timer);timer=setTimeout(draw,250);};
 const stop=editor.onChange(schedule,true),destroy=rendered.destroy;
 window.addEventListener('drg-appearance',schedule);timer=setTimeout(draw,180);
 return{...rendered,ignoreMutation(mutation){if(mutation.type==='attributes'||preview===mutation.target||preview.contains(mutation.target))return true;return rendered.ignoreMutation?.(mutation)??false;},destroy(){disposed=true;revision++;clearTimeout(timer);stop?.();window.removeEventListener('drg-appearance',schedule);destroy?.();}};
}
