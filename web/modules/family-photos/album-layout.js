// Visible placeholders retain their geometry when intrinsic dimensions arrive.
// Natural album proportions are applied offscreen, with one scroll restoration.
export function stableAlbumLayout(host,beforeLayout,signal){
  const entries=new Map(),bound=new WeakSet(),events=new AbortController();let frame=0,closed=false;
  const observer=new IntersectionObserver(changes=>{
    for(const change of changes){const entry=entries.get(change.target);if(entry){entry.visible=change.isIntersecting;if(!entry.visible&&entry.pending)schedule();}}
  },{root:host,rootMargin:'80px 0px'});
  function geometry(album,placeholder=false){
    const rows=[...album.querySelectorAll('.chat-media-row')],single=album.classList.contains('is-single');let complete=true,firstRatio=0;
    const layout=rows.map(row=>{
      const tiles=[...row.children];const ratios=tiles.map(tile=>{
        const m=tile.querySelector('img,video'),w=m?.naturalWidth||m?.videoWidth,h=m?.naturalHeight||m?.videoHeight;
        if(!w||!h)complete=false;const raw=w&&h?w/h:1;if(!firstRatio&&w&&h)firstRatio=raw;
        return placeholder?1:single?raw:Math.max(.45,Math.min(2,raw));
      });const total=ratios.reduce((a,b)=>a+b,0)||1;return {row,tiles,ratios,total};
    });
    const count=layout.reduce((n,row)=>n+row.tiles.length,0),stacked=!placeholder&&[3,4].includes(count)&&firstRatio>0&&firstRatio<.85;
    return {layout,single,complete:complete&&!placeholder,stacked,stackRatio:firstRatio*(count===4?4/3:3/2),width:Math.round(Math.min(360,(layout[0]?.total||1)*420))};
  }
  function apply(album,g){
    for(const {row,tiles,ratios,total} of g.layout){tiles.forEach((tile,i)=>tile.style.flex=String(ratios[i]/total*tiles.length));row.style.aspectRatio=String(total);}
    album.classList.toggle('is-provisional',!g.complete);album.classList.toggle('is-portrait-stack',g.stacked);
    if(g.stacked)album.style.aspectRatio=String(g.stackRatio);else album.style.removeProperty('aspect-ratio');
    const message=album.closest('.chat-message');
    if(g.single){album.style.width='100%';if(message)message.style.width=g.width+'px';}
    else{album.style.removeProperty('width');message?.style.removeProperty('width');}
  }
  function update(entry){
    const natural=geometry(entry.album);
    if(natural.complete&&!entry.visible){entry.selected=natural;entry.pending=false;return true;}
    if(natural.complete)entry.pending=true;
    return false;
  }
  function flush(){
    frame=0;if(closed||document.hidden)return;
    const changed=[];
    for(const entry of entries.values())if(entry.pending&&!entry.visible&&entry.album.isConnected&&update(entry))changed.push(entry);
    if(!changed.length)return;const restore=beforeLayout?.();for(const entry of changed)apply(entry.album,entry.selected);restore?.();
  }
  function schedule(){if(!frame&&!closed)frame=requestAnimationFrame(flush);}
  const visible=()=>{if(!document.hidden)schedule();};document.addEventListener('visibilitychange',visible,{signal:events.signal});
  function sync(){
    if(closed)return;
    for(const [album,entry] of entries)if(!host.contains(album)){observer.unobserve(album);entries.delete(album);}
    const albums=[...host.querySelectorAll('.chat-media-album')];
    for(const album of albums){
      const identity=[...album.querySelectorAll('.chat-media-tile')].map(tile=>{const m=tile.querySelector('img,video');return [tile.dataset.chatAttachmentId,m?.getAttribute('src')];});
      const key=JSON.stringify(identity);let entry=entries.get(album);
      if(!entry||entry.key!==key){
        const natural=geometry(album);entry={album,key,visible:entry?.visible??true,pending:false,selected:natural.complete?natural:geometry(album,true)};entries.set(album,entry);observer.observe(album);
      }else if(!entry.selected.complete)update(entry);
      // Reconciliation may have removed runtime-owned layout styles.
      // Refresh node references while retaining selected ratios for visible media.
      const rows=[...album.querySelectorAll('.chat-media-row')];
      entry.selected.layout=entry.selected.layout.map((g,i)=>({...g,row:rows[i],tiles:[...rows[i].children]}));
      album.querySelectorAll('img,video').forEach(m=>{
        if(bound.has(m))return;bound.add(m);
        m.addEventListener(m.tagName==='VIDEO'?'loadedmetadata':'load',()=>{
          const current=entries.get(album);if(!closed&&current&&album.isConnected){current.pending=true;schedule();}
        },{signal:events.signal});
      });
    }
    if(albums.length){const restore=beforeLayout?.();for(const album of albums)apply(album,entries.get(album).selected);restore?.();}
  }
  function destroy(){if(closed)return;closed=true;cancelAnimationFrame(frame);events.abort();observer.disconnect();entries.clear();}
  signal?.addEventListener('abort',destroy,{once:true});
  return {sync,destroy};
}
