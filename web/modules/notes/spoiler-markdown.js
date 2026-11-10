// Use temporary plain-text tokens so the lossy exporter cannot discard marks
// or escape the spoiler delimiters. The rich document remains untouched.
export function spoilerMarkdown(blocks,serialize){
 const token='DRGSPOILER'+crypto.randomUUID().replaceAll('-',''),open=token+'OPEN',close=token+'CLOSE';
 const visit=value=>{
  if(Array.isArray(value))return value.flatMap(item=>{
   if(item?.type==='text'&&item.styles?.spoiler){const {spoiler,...styles}=item.styles;return [{type:'text',text:open,styles:{}},{...item,styles},{type:'text',text:close,styles:{}}];}
   return [visit(item)];
  });
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,visit(item)]));
  return value;
 };
 return serialize(visit(blocks)).replaceAll(close+open,'').replaceAll(open,'||').replaceAll(close,'||');
}

export function parseInlineSpoilers(blocks){
 function inline(parts){
  if(!Array.isArray(parts))return parts;
  let source='',delimiters=[];
  const collect=items=>{for(const part of items){if(part.type==='link'){collect(part.content||[]);continue;}if(part.type!=='text'){source+='\ufffc';continue;}
   if(!part.styles?.code)for(const match of part.text.matchAll(/(?<![\\|])\|\|(?!\|)/g))delimiters.push(source.length+match.index);
   source+=part.text;
  }};collect(parts);
  const pairs=[];for(let i=0;i+1<delimiters.length;i+=2)if(delimiters[i+1]>delimiters[i]+2)pairs.push([delimiters[i],delimiters[i+1]]);
  if(!pairs.length)return parts;
  let offset=0;
  const split=items=>items.flatMap(part=>{
   if(part.type==='link')return [{...part,content:split(part.content||[])}];
   if(part.type!=='text'){offset++;return [part];}
   const start=offset,end=offset+part.text.length;offset=end;const out=[];let cursor=start;
   const add=(a,b,hidden)=>{if(b>a)out.push({...part,text:part.text.slice(a-start,b-start),styles:{...part.styles,...(hidden?{spoiler:true}:{})}});};
   for(const [a,b] of pairs){if(b+2<=start||a>=end)continue;
    add(cursor,Math.min(a,end),false);const left=Math.max(cursor,a+2),right=Math.min(end,b);add(left,right,true);cursor=Math.min(end,b+2);
   }
   add(cursor,end,false);return out;
  });
  return split(parts);
 }
 const walk=value=>{
  if(Array.isArray(value))return value.length&&value.every(part=>['text','link','equation'].includes(part?.type))?inline(value):value.map(walk);
  if(!value||typeof value!=='object'||value.type==='codeBlock')return value;
  const out={...value};for(const [key,item] of Object.entries(out))out[key]=key==='content'&&Array.isArray(item)?inline(item):walk(item);return out;
 };
 return walk(blocks);
}
