import {tText,tHtml} from "../../core/i18n.js";
// Shared presentation for native attachments and Telegram albums. Source IDs stay intact.
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const visualMedia=file=>['photo','image','video','animation'].includes(file.kind);
export const animationVideo=file=>file.kind==='animation'&&!file.mime?.startsWith('image/');
// Posters stay with the media node, in memory only, and disappear with it.
const videoState=new WeakMap();
const videoSource=url=>url.includes('#')?url:url+'#t=0.1';
export function videoPoster(file){
  if(file.thumbnail_url)return file.thumbnail_url;
  return [...document.querySelectorAll('.chat-media-tile>video')].find(v=>[file.url,videoSource(file.url)].includes(v.getAttribute('src')))?.poster||'';
}
export function prepareVideos(host){
  host.querySelectorAll('video').forEach(video=>{
    const paint=poster=>{
      if(!poster)return;
      if(!video.poster)video.poster=poster;
      const tile=video.closest('.chat-media-tile');
      // A paused video may keep displaying its decoded frame after seeking,
      // instead of the new poster. Paint the captured frame on the tile itself
      // so the thumbnail doesn't depend on the inline video surface on iOS.
      if(tile){tile.style.backgroundImage=`url("${poster}")`;tile.classList.add('has-video-poster');}
    };
    const state=videoState.get(video);
    if(state){paint(state.poster);return;}
    const saved={poster:video.getAttribute('poster')||''};videoState.set(video,saved);
    paint(saved.poster);
    const capture=()=>{
      if(!video.isConnected||video.poster||video.readyState<2||!video.videoWidth)return;
      try{
        const canvas=document.createElement('canvas'),scale=Math.min(1,640/video.videoWidth,640/video.videoHeight);
        canvas.width=Math.max(1,Math.round(video.videoWidth*scale));canvas.height=Math.max(1,Math.round(video.videoHeight*scale));
        canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
        saved.poster=canvas.toDataURL('image/jpeg',.82);paint(saved.poster);
      }catch{/* Playback and downloading remain available if a frame cannot be captured. */}
    };
    const seek=()=>{if(!video.poster&&video.paused&&!video.closest('.is-preview-playing')&&video.currentTime===0&&Number.isFinite(video.duration)&&video.duration>0)video.currentTime=Math.min(.1,video.duration/2);};
    video.addEventListener('loadedmetadata',seek,{once:true});
    video.addEventListener('loadeddata',capture,{once:true});video.addEventListener('seeked',capture,{once:true});video.addEventListener('canplay',capture,{once:true});
    if(video.readyState>=1)seek();capture();
  });
}
export function albumRows(files){
  const layouts={1:[1],2:[2],3:[3],4:[2,2],5:[2,3],6:[3,3],7:[2,3,2],8:[2,3,3],9:[3,3,3],10:[3,3,2,2]};
  const result=[];
  for(let offset=0;offset<files.length;offset+=10){let start=offset;for(const count of layouts[Math.min(10,files.length-offset)]){result.push(files.slice(start,start+count));start+=count;}}
  return result;
}
export function albumMarkup(files,attribute){
  if(!files.length)return '';
  if(!['data-chat-media-id','data-fp-chat-file'].includes(attribute))throw Error('Invalid media attribute');
  return `<div class="chat-media-album ${files.length===1?'is-single':''}" role="group" aria-label="${files.length>1?tText("common:albumFiles", {count:(files.length)}):tText("chat:media")}">${albumRows(files).map((row,index)=>`<div class="chat-media-row" data-media-row="${index}">${row.map(file=>{
    const animation=file.kind==='animation',video=file.kind==='video'||animationVideo(file),ready=!file.state||file.state==='ready';
    const preview=file.thumbnail_url||(!video?file.url:null),label=animation?'GIF':video?tText("chat:video2"):tText("chat:photo2");
    const content=video?`<video ${animation?'data-animation-preview':'data-video-preview'} muted loop playsinline preload="${preview?'none':'metadata'}" ${preview?`poster="${esc(preview)}"`:''} src="${esc(animation?file.url:videoSource(file.url))}" aria-label="${animation?'GIF':tText("chat:videoPreview")}"></video>`:`<img src="${esc(animation?file.url:preview)}" alt="${animation?'GIF':tText("chat:photo")}" loading="lazy">`;
    return `<button type="button" class="chat-media-tile ${animation?'is-animation':''} ${file.state==='paused'?'is-unavailable':''}" data-chat-attachment-id="${esc(file.id)}" ${attribute}="${esc(file.id)}" aria-label="${tHtml("chat:open", {value0:(label)})}">${ready?content:`<span class="chat-media-wait">${file.state==='paused'?(animation?tText("chat:thisGifIsNotAvailableInTg"):video?tText("chat:thisVideoIsNotAvailableInTg"):tText("chat:thisPhotoIsNotAvailableInTg")):file.state==='failed'?tText("chat:waitingForReUpload"):tText("chat:uploading")}</span>`}${ready?(animation?'<span class="chat-video-timer chat-gif-mark">GIF</span>':video?'<span class="chat-video-timer" data-video-remaining></span><span class="chat-video-progress"><i data-video-progress></i></span><span class="chat-media-play" aria-hidden="true">▶</span>':''):''}</button>`;
  }).join('')}</div>`).join('')}</div>`;
}
export function telegramAlbums(messages){
  const result=[];
  for(const message of messages){
    const previous=(message.site_send_id?result.find(item=>item.site_send_id===message.site_send_id):null)||result.at(-1),media=message.media||[];
    const join=previous?.album_id&&previous.album_id===message.album_id&&media.length&&previous.media.length+media.length<=10&&
      (previous.sender_id||previous.sender)===(message.sender_id||message.sender)&&previous.group_id===message.group_id;
    if(join){
      const offset=(previous.text||'').length+(previous.text&&message.text?1:0);
      previous.media.push(...media);previous.sources.push(message);
      previous.custom_emojis.push(...(message.custom_emojis||[]).map(entry=>({...entry,offset:entry.offset+offset})));
      previous.reactions.push(...(message.reactions||[]));previous.site_reactions.push(...(message.site_reactions||[]));
      previous.text=[previous.text,message.text].filter(Boolean).join('\n');previous.edited=Math.max(previous.edited||0,message.edited||0);
    }
    else result.push({...message,media:[...media],sources:[message],custom_emojis:[...(message.custom_emojis||[])],reactions:[...(message.reactions||[])],site_reactions:[...(message.site_reactions||[])]});
  }
  return result;
}
const bound=new WeakSet();
export function fitAlbums(host,beforeLayout){
  const fit=row=>{
    if(!row?.isConnected)return;
    const restore=beforeLayout?.(),tiles=[...row.children],ratios=[];let total=0;
    for(const tile of tiles){const media=tile.querySelector('img,video');const width=media?.naturalWidth||media?.videoWidth,height=media?.naturalHeight||media?.videoHeight;
      const raw=width&&height?width/height:1,single=row.closest('.chat-media-album').classList.contains('is-single');
      const ratio=single?raw:Math.max(.45,Math.min(2,raw));ratios.push(ratio);total+=ratio;
    }
    // Flex factors whose sum is below one leave unused space in the row.
    tiles.forEach((tile,index)=>tile.style.flex=String(ratios[index]/total*tiles.length));
    row.style.aspectRatio=String(total);
    const album=row.closest('.chat-media-album');if(album.classList.contains('is-single')){album.style.width='100%';const message=album.closest('.chat-message');if(message)message.style.width=Math.round(Math.min(360,total*420))+'px';}
    const all=[...album.querySelectorAll('.chat-media-tile')],first=all[0]?.querySelector('img,video');
    const firstRatio=(first?.naturalWidth||first?.videoWidth)/(first?.naturalHeight||first?.videoHeight);
    const stacked=[3,4].includes(all.length)&&firstRatio>0&&firstRatio<.85;
    album.classList.toggle('is-portrait-stack',stacked);
    if(stacked)album.style.aspectRatio=String(firstRatio*(all.length===4?4/3:3/2));else album.style.removeProperty('aspect-ratio');
    restore?.();
  };
  host.querySelectorAll('.chat-media-row').forEach(row=>{fit(row);row.querySelectorAll('img,video').forEach(media=>{if(bound.has(media))return;bound.add(media);media.addEventListener(media.tagName==='VIDEO'?'loadedmetadata':'load',()=>fit(media.closest('.chat-media-row')));});});
  prepareVideos(host);
}

export function mediaFrame(markup,footer){return `<div class="chat-media-frame">${markup}${footer}</div>`;}
