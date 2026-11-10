import {marked} from 'marked';
import {normalizeToggleHTML} from './paste.js';

// Exported attachments already have private, authenticated URLs. Restore their
// block type without turning ordinary web links or inline references into files.
export function importedFileBlocks(blocks,origin=location.origin){
  return blocks.map(block=>{
    const children=importedFileBlocks(block.children||[],origin);
    const parts=Array.isArray(block.content)?block.content.filter(part=>part.type!=='text'||part.text.trim()):[];
    if(block.type==='paragraph'&&parts.length===1&&parts[0].type==='link'){
      const link=parts[0];let url;
      try{url=new URL(link.href,origin);}catch{}
      if(url?.origin===origin&&/^\/api\/notes\/[\w-]+\/files\/[\w-]+$/.test(url.pathname)&&!url.search&&!url.hash){
        const name=(link.content||[]).map(part=>part.text||'').join('');
        return {id:block.id,type:'file',props:{url:url.href,name},children};
      }
    }
    return {...block,children};
  });
}

export function attachmentOrigins(value,origin){
  if(Array.isArray(value))return value.map(item=>attachmentOrigins(item,origin));
  if(!value||typeof value!=='object')return value;
  return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,(key==='url'||key==='href')&&typeof item==='string'&&/^\/api\/(?:notes\/[\w-]+\/(?:files|assets)\/[\w-]+|media\/[\w-]+)(?:$|[?#])/.test(item)?new URL(item,origin).href:attachmentOrigins(item,origin)]));
}
export function parseImportedMarkdown(editor,text,origin=location.origin){
  // Markdown exports may contain explicit HTML details. Code fences remain
  // escaped by marked and cannot accidentally become interactive blocks.
  const html=marked.parse(text),toggles=normalizeToggleHTML(html);
  return importedFileBlocks(attachmentOrigins(toggles?editor.tryParseHTMLToBlocks(toggles):editor.tryParseMarkdownToBlocks(text),origin),origin);
}
