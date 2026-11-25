import {readStorage,storageBusy} from '../../core/storage-read.js';

// Refresh navigation independently of the document editor and its draft.
export function watchNoteCatalog(ctx,apply,{delay=5000,onUnavailable}={}){
 let disposed=false,paused=false,pending=null,controller,timer,revision='';
 const schedule=()=>{clearTimeout(timer);if(!disposed&&!paused&&!document.hidden)timer=setTimeout(refresh,delay);};
 function refresh(){
  if(disposed||paused||document.hidden)return Promise.resolve();
  if(pending)return pending;
  clearTimeout(timer);
  controller=new AbortController();
  const read=path=>readStorage(ctx.api,path,controller.signal);
  const previous=ctx.state.notes;
  const signature=notes=>JSON.stringify(notes.map(n=>[n.id,n.title,n.version,n.space_id,n.parent_id,n.icon,n.cover,n.updated,n.favorite,n.isDraft]));
  const before=signature(previous);
  pending=Promise.all([read('/notes?catalog=true&catalog_revision='+encodeURIComponent(revision)),read('/social')]).then(([catalog,social])=>{
   if(!disposed)onUnavailable?.(null);
   if(disposed||ctx.state.notes!==previous||signature(previous)!==before)return;
   const legacy=Array.isArray(catalog);
   revision=legacy?'':catalog.revision;apply(legacy?catalog:catalog.unchanged?previous:catalog.notes,social.spaces);
  }).catch(error=>{
   controller.abort();
   if(disposed||!storageBusy(error))return;
   paused=true;
   onUnavailable?.(error,()=>{paused=false;return refresh();});
  }).finally(()=>{pending=null;schedule();});
  return pending;
 }
 const visible=()=>{if(!document.hidden)refresh();};
 window.addEventListener('focus',visible);window.addEventListener('online',visible);document.addEventListener('visibilitychange',visible);
 schedule();
 return ()=>{disposed=true;controller?.abort();clearTimeout(timer);window.removeEventListener('focus',visible);window.removeEventListener('online',visible);document.removeEventListener('visibilitychange',visible);};
}
