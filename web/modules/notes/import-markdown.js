import {marked} from 'marked';
import {normalizeToggleHTML} from './paste.js';

export function privateAttachmentUrl(value, origin){
  let url;
  try{url=new URL(value,origin);}catch{return null;}
  return url.origin===origin&&/^\/api\/notes\/[\w-]+\/files\/[\w-]+$/.test(url.pathname)&&!url.search&&!url.hash?url:null;
}

// Exported attachments already have private, authenticated URLs. Restore their
// block type without turning ordinary web links or inline references into files.
export function importedFileBlocks(blocks,origin=location.origin){
  return blocks.map(block=>{
    const children=importedFileBlocks(block.children||[],origin);
    const parts=Array.isArray(block.content)?block.content.filter(part=>part.type!=='text'||part.text.trim()):[];
    if(block.type==='paragraph'&&parts.length===1&&parts[0].type==='link'){
      const link=parts[0],url=privateAttachmentUrl(link.href,origin);
      if(url){
        const name=(link.content||[]).map(part=>part.text||'').join('');
        return {id:block.id,type:'file',props:{url:url.href,name},children};
      }
    }
    return {...block,children};
  });
}

function tableImages(html,origin){
  if(!html.includes('<table')||!html.includes('<img'))return null;
  const doc=new DOMParser().parseFromString(html,'text/html');
  let changed=false;
  for(const image of doc.querySelectorAll('td img, th img')){
    const url=privateAttachmentUrl(image.getAttribute('src'),origin);
    if(!url)continue;
    const marker=doc.createElement('span');
    marker.dataset.noteInlineImage=url.href;
    marker.dataset.alt=image.alt;
    const size=/^\s*=([1-9]\d{0,3})x[1-9]\d{0,3}\s*$/.exec(image.title);
    if(size)marker.dataset.width=size[1];
    image.replaceWith(marker);
    changed=true;
  }
  return changed?doc.body.innerHTML:null;
}

export function attachmentOrigins(value,origin){
  if(Array.isArray(value))return value.map(item=>attachmentOrigins(item,origin));
  if(!value||typeof value!=='object')return value;
  return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,(key==='url'||key==='href')&&typeof item==='string'&&/^\/api\/(?:notes\/[\w-]+\/(?:files|assets)\/[\w-]+|media\/[\w-]+)(?:$|[?#])/.test(item)?new URL(item,origin).href:attachmentOrigins(item,origin)]));
}
export function parseImportedMarkdown(editor,text,origin=location.origin){
  // Markdown exports may contain explicit HTML details. Code fences remain
  // escaped by marked and cannot accidentally become interactive blocks.
  const html=marked.parse(text),tables=tableImages(html,origin);
  const formatted=normalizeToggleHTML(tables||html)||tables;
  return importedFileBlocks(attachmentOrigins(formatted?editor.tryParseHTMLToBlocks(formatted):editor.tryParseMarkdownToBlocks(text),origin),origin);
}
