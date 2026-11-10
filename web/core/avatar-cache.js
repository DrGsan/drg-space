// Avatars stay in this document's memory only. Their versioned source URL is
// shared by every visible copy; authenticated HTTP responses remain no-store.
const assets=new Map(),profiles=new Map();
let generation=0,observer;
const sourceSelector='img[data-avatar-source]';
export function cachedAvatar(url){return assets.get(url)?.url;}
export function currentAvatar(person){return profiles.has(person?.id)?profiles.get(person.id):person?.avatar_url;}
function collect(){
  if(assets.size<=128)return;
  const visible=new Set([...document.querySelectorAll(sourceSelector)].map(img=>img.dataset.avatarSource));
  for(const [source,asset] of assets){if(assets.size<=128)break;if(!visible.has(source)&&(asset.url||asset.retryAt)){if(asset.url)URL.revokeObjectURL(asset.url);assets.delete(source);}}
}
async function load(source){
  if(assets.has(source)){const previous=assets.get(source);if(!previous.retryAt||Date.now()<previous.retryAt)return previous.promise;assets.delete(source);}
  const version=generation,asset={};assets.set(source,asset);
  asset.promise=(async()=>{
    try{
      const path=new URL(source,location.origin);
      if(path.origin!==location.origin||!/^\/api\/(?:users\/[^/]+|account|social\/families\/[^/]+)\/avatar$/.test(path.pathname))return;
      const response=await fetch(path,{credentials:'same-origin',cache:'no-store',signal:(asset.abort=new AbortController()).signal});
      if(!response.ok)throw Error('Avatar unavailable');
      const blob=await response.blob();if(!blob.type.startsWith('image/')||blob.size>1024*1024)throw Error('Invalid avatar');
      if(generation!==version||assets.get(source)!==asset)return;
      asset.url=URL.createObjectURL(blob);
      document.querySelectorAll(sourceSelector).forEach(img=>{if(img.dataset.avatarSource===source)img.src=asset.url;});
      collect();return asset.url;
    }catch{asset.retryAt=Date.now()+30000;/* Keep initials; don't retry once per message. */}
  })();return asset.promise;
}
function hydrate(root=document){
  const images=[...(root.matches?.(sourceSelector)?[root]:[]),...root.querySelectorAll?.(sourceSelector)||[]];
  for(const img of images){const source=img.dataset.avatarSource;if(!source)continue;const cached=cachedAvatar(source);if(cached){if(img.getAttribute('src')!==cached)img.src=cached;}else void load(source);}
}
export function refreshAvatarProfiles(people){
  for(const person of people){if(!Object.hasOwn(person,'avatar_url'))continue;profiles.set(person.id,person.avatar_url||null);
    document.querySelectorAll('[data-person-avatar]').forEach(el=>{if(el.dataset.personAvatar!==person.id)return;let img=el.querySelector(sourceSelector);if(!person.avatar_url){img?.remove();return;}if(!img){img=document.createElement('img');img.alt='';el.append(img);}if(img.dataset.avatarSource!==person.avatar_url){img.removeAttribute('src');img.dataset.avatarSource=person.avatar_url;}hydrate(img);});
  }
}
export function clearAvatarCache(){
  generation++;for(const asset of assets.values()){asset.abort?.abort();if(asset.url)URL.revokeObjectURL(asset.url);}assets.clear();profiles.clear();
  if(typeof document.querySelectorAll==='function')document.querySelectorAll(sourceSelector).forEach(img=>img.removeAttribute('src'));
}
if(typeof MutationObserver!=='undefined'){
  observer=new MutationObserver(records=>{for(const record of records){if(record.type==='attributes')hydrate(record.target);else for(const node of record.addedNodes)if(node.nodeType===1)hydrate(node);}});
  observer.observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['data-avatar-source']});
  document.addEventListener('drg-presence-change',e=>refreshAvatarProfiles(e.detail));
  window.addEventListener('pagehide',clearAvatarCache);
  window.addEventListener('pageshow',()=>hydrate());
}
