// Refresh navigation independently of the document editor and its draft.
export function watchNoteCatalog(ctx,apply,{delay=5000}={}){
 let disposed=false,pending=null,timer,revision='';
 const schedule=()=>{clearTimeout(timer);if(!disposed)timer=setTimeout(()=>{refresh();},delay);};
 function refresh(){
  if(disposed||document.hidden){schedule();return Promise.resolve();}
  if(pending)return pending;
  const previous=ctx.state.notes;
  const signature=notes=>JSON.stringify(notes.map(n=>[n.id,n.title,n.version,n.space_id,n.parent_id,n.icon,n.cover,n.updated,n.favorite,n.isDraft]));
  const before=signature(previous);
  pending=Promise.all([ctx.api('/notes?catalog=true&catalog_revision='+encodeURIComponent(revision)),ctx.api('/social')]).then(([catalog,social])=>{
   if(disposed||ctx.state.notes!==previous||signature(previous)!==before)return;
   const legacy=Array.isArray(catalog);
   revision=legacy?'':catalog.revision;apply(legacy?catalog:catalog.unchanged?previous:catalog.notes,social.spaces);
  }).catch(()=>{/* Keep the last catalog through transient network failures. */}).finally(()=>{pending=null;schedule();});
  return pending;
 }
 const visible=()=>{if(!document.hidden)refresh();};
 window.addEventListener('focus',visible);window.addEventListener('online',visible);document.addEventListener('visibilitychange',visible);
 schedule();
 return ()=>{disposed=true;clearTimeout(timer);window.removeEventListener('focus',visible);window.removeEventListener('online',visible);document.removeEventListener('visibilitychange',visible);};
}
