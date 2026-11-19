import {marked,Marked} from 'marked';
import {normalizeToggleHTML} from './paste.js';

// Parse directive lines before the Markdown parser folds soft line breaks.
// The block normalizer then matches the fences around parsed rich content.
const outlineFence=/^ {0,3}:::(?:info|success|warning|danger|error|tip|note)?[ \t]*(?:\r?\n|$)/im;
const firstOutlineFence=new RegExp(outlineFence.source,'i');
const outlineMarkdown=new Marked({extensions:[{
  name:'outlineFence',
  level:'block',
  start(source){return source.search(outlineFence);},
  tokenizer(source){
    const match=firstOutlineFence.exec(source);
    if(match)return {type:'outlineFence',raw:match[0],text:match[0].trim()};
  },
  renderer(token){return '<p data-outline-fence>'+token.text+'</p>\n';},
}]});

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

function besideInlineText(image){
  for(const direction of ['previousSibling','nextSibling']){
    for(let node=image[direction];node;node=node[direction]){
      if(node.nodeName==='BR')continue;
      if(node.nodeType===1&&!node.matches('a,b,code,del,em,i,s,span,strong,sub,sup,u'))break;
      if(node.textContent.trim())return true;
    }
  }
  return false;
}

function importMarkup(html,origin){
  const hasFences=html.includes('<p data-outline-fence>');
  if(!hasFences&&!html.includes('<img'))return null;
  const doc=new DOMParser().parseFromString(html,'text/html');
  // Keep the alert's first rich paragraph in its visible body. Subsequent
  // paragraphs and lists stay separate blocks for the existing normalizer.
  for(const fence of doc.querySelectorAll('p[data-outline-fence]')){
    const body=fence.nextElementSibling;
    if(fence.textContent===':::'||!body?.matches('p:not([data-outline-fence])'))continue;
    fence.append(doc.createElement('br'),...body.childNodes);
    body.remove();
  }
  let changed=hasFences;
  for(const image of doc.querySelectorAll('img')){
    if(!image.closest('td,th')&&!besideInlineText(image))continue;
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
  const html=(text.includes(':::')?outlineMarkdown:marked).parse(text),markup=importMarkup(html,origin);
  const formatted=normalizeToggleHTML(markup||html)||markup;
  return importedFileBlocks(attachmentOrigins(formatted?editor.tryParseHTMLToBlocks(formatted):editor.tryParseMarkdownToBlocks(text),origin),origin);
}
