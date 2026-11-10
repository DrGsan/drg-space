import {tText} from "../../core/i18n.js";
// Outline's Markdown export uses fenced directives, often in the same rich
// paragraph as the first line. Only balanced, known directives are converted.
export const calloutKinds={info:{title:tText("notes:notificationWithDetails"),aliases:['info',tText("notes:information"),tText("notes:details")]},success:{title:tText("notes:successNotification"),aliases:['success',tText("notes:success")]},warning:{title:tText("notes:warning"),aliases:['warning','danger',tText("notes:warning2")]},tip:{title:tText("notes:tip"),aliases:['tip','note',tText("notes:tip2")]}};
const aliases={info:'info',success:'success',warning:'warning',danger:'warning',error:'warning',tip:'tip',note:'tip'};
const text=content=>typeof content==='string'?content:(content||[]).map(part=>part.type==='link'?text(part.content):part.type==='text'?part.text:'\ufffc').join('');
function slice(content,start,end=Infinity){
 if(typeof content==='string')return content.slice(start,end);
 let offset=0;return(content||[]).flatMap(part=>{const length=text([part]).length,a=Math.max(0,start-offset),b=Math.min(length,end-offset);offset+=length;if(b<=a)return[];
  if(part.type==='link')return[{...part,content:slice(part.content,a,b)}];
  return[part.type==='text'?{...part,text:part.text.slice(a,b)}:{...part}];});
}
function opening(block){
 if(block.type!=='paragraph')return null;
 const source=text(block.content),match=/^ {0,3}:::(info|success|warning|danger|error|tip|note)[ \t]*(?:\n|$)/i.exec(source);
 if(!match)return null;
 const first=typeof block.content==='string'?null:block.content?.[0];if(first&&(first.type!=='text'||first.styles?.code))return null;
 return{kind:aliases[match[1].toLowerCase()],length:match[0].length};
}
function closing(block,start=0){
 if(block.type!=='paragraph')return null;
 const source=text(block.content),match=/(?:^|\n)[ \t]*:::[ \t]*$/.exec(source.slice(start));
 if(!match)return null;
 const index=start+match.index,tail=slice(block.content,index);
 if(Array.isArray(tail)&&tail.some(part=>part.type!=='text'||part.styles?.code))return null;
 return index;
}
export function normalizeOutlineCallouts(input){
 let changed=false;
 const walk=items=>{
  const blocks=items.map(block=>block.children?.length?{...block,children:walk(block.children)}:block),out=[];
  for(let i=0;i<blocks.length;i++){
   const first=blocks[i],open=opening(first);if(!open){out.push(first);continue;}
   let end=i,close=closing(first,open.length),depth=1;
   if(close===null)for(end=i+1;end<blocks.length;end++){
    if(opening(blocks[end]))depth++;
    const at=closing(blocks[end]);if(at!==null&&!--depth){close=at;break;}
   }
   if(close===null||end>=blocks.length){out.push(first);continue;}
   let children=[...(first.children||[])];
   if(end>i){
    const body=blocks.slice(i+1,end),last=blocks[end],remaining=slice(last.content,0,close);
    if(text(remaining).trim()||last.children?.length)body.push({...last,content:remaining});
    children.push(...walk(body));
   }
   out.push({...first,type:'callout',props:{...first.props,variant:open.kind},content:slice(first.content,open.length,end===i?close:Infinity),children});
   i=end;changed=true;
  }
  return out;
 };
 const blocks=walk(input||[]);return{blocks:changed?blocks:input,changed};
}
// Keep typed alerts portable when switching to Markdown or exporting notes.
export function blocksMarkdown(editor,blocks){
 const typed=block=>block.type==='callout'&&calloutKinds[block.props.variant];
 const contains=items=>items.some(b=>typed(b)||contains(b.children||[]));
 const parts=[];let run=[];
 const flush=()=>{if(run.length){parts.push(editor.blocksToMarkdownLossy(run).trimEnd());run=[];}};
 for(const block of blocks){
  if(!typed(block)&&!contains(block.children||[])){run.push(block);continue;}
  flush();
  const own=editor.blocksToMarkdownLossy([{...block,type:typed(block)?'paragraph':block.type,children:[]}]).trimEnd();
  const children=blocksMarkdown(editor,block.children||[]).trimEnd();
  if(typed(block))parts.push(':::'+block.props.variant+'\n'+[own,children].filter(Boolean).join('\n\n')+'\n:::');
  else parts.push(own+'\n\n'+children.split('\n').map(line=>line?'  '+line:line).join('\n'));
 }
 flush();return parts.join('\n\n');
}
