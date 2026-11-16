// Apply decoded preview proportions in one frame, preserving the reader's place.
// Video posters supply dimensions without fetching the original video.
export function stableAlbumLayout(host,beforeLayout,signal,motion){
  const entries=new Map(),bound=new WeakSet(),dirty=new Set(),events=new AbortController();let frame=0,closed=false;
  function geometry(album){
    const single=album.classList.contains('is-single');let firstRatio=0,first=true;
    const layout=[...album.querySelectorAll('.chat-media-row')].map(row=>{
      const tiles=[...row.children],ratios=tiles.map(tile=>{
        const image=tile.querySelector('img'),video=tile.querySelector('video'),w=image?.naturalWidth||video?.videoWidth,h=image?.naturalHeight||video?.videoHeight;
        const raw=w&&h?w/h:1;if(first){firstRatio=w&&h?raw:0;first=false;}
        return single?raw:Math.max(.45,Math.min(2,raw));
      });return {row,tiles,ratios,total:ratios.reduce((a,b)=>a+b,0)||1};
    });
    const count=layout.reduce((n,row)=>n+row.tiles.length,0),stacked=[3,4].includes(count)&&firstRatio>0&&firstRatio<.85;
    const stackRatio=firstRatio*(count===4?4/3:3/2),width=Math.round(Math.min(360,(layout[0]?.total||1)*420));
    return {layout,single,stacked,stackRatio,width,key:JSON.stringify([layout.map(g=>g.ratios),stacked,stackRatio,width])};
  }
  function apply(album,g){
    for(const {row,tiles,ratios,total} of g.layout){tiles.forEach((tile,i)=>tile.style.flex=String(ratios[i]/total*tiles.length));row.style.aspectRatio=String(total);}
    album.classList.toggle('is-portrait-stack',g.stacked);
    if(g.stacked)album.style.aspectRatio=String(g.stackRatio);else album.style.removeProperty('aspect-ratio');
    const message=album.closest('.chat-message');
    if(g.single){album.style.width='100%';if(message)message.style.width=g.width+'px';}
    else{album.style.removeProperty('width');message?.style.removeProperty('width');}
  }
  function flush(){
    frame=0;if(closed||document.hidden)return;
    if(motion?.active){motion.defer('media-layout',()=>schedule());return;}
    const changed=[];
    for(const entry of dirty){
      if(!host.contains(entry.album))continue;
      const next=geometry(entry.album);if(next.key!==entry.selected.key){entry.selected=next;changed.push(entry);}
    }
    dirty.clear();if(!changed.length)return;
    const restore=beforeLayout?.();for(const entry of changed)apply(entry.album,entry.selected);restore?.();
  }
  function schedule(entry){if(entry)dirty.add(entry);if(!frame&&!closed)frame=requestAnimationFrame(flush);}
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&dirty.size)schedule();},{signal:events.signal});
  function sync(){
    if(closed)return;
    for(const [album,entry] of entries)if(!host.contains(album)){dirty.delete(entry);entries.delete(album);}
    const albums=[...host.querySelectorAll('.chat-media-album')];
    for(const album of albums){
      let entry=entries.get(album);if(!entry){entry={album};entries.set(album,entry);}
      entry.selected=geometry(album);dirty.delete(entry);
      album.querySelectorAll('img,video').forEach(m=>{
        if(bound.has(m))return;bound.add(m);
        m.addEventListener(m.tagName==='VIDEO'?'loadedmetadata':'load',()=>{if(!closed&&host.contains(album))schedule(entries.get(album));},{signal:events.signal});
      });
    }
    // The reconciler can remove runtime layout styles even when media nodes stay.
    if(albums.length){const restore=beforeLayout?.();for(const album of albums)apply(album,entries.get(album).selected);restore?.();}
  }
  function destroy(){if(closed)return;closed=true;cancelAnimationFrame(frame);events.abort();entries.clear();dirty.clear();}
  signal?.addEventListener('abort',destroy,{once:true});
  return {sync,destroy};
}
