// Markdown is a projection; convert a copy so the rich document keeps its atoms.
export function equationTextProjection(value){
 if(Array.isArray(value))return value.map(equationTextProjection);
 if(value&&typeof value==='object'){
  if(value.type==='equation')return {type:'text',text:'$'+value.props.latex+'$',styles:{}};
  return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,equationTextProjection(item)]));
 }
 return value;
}
export function parseInlineEquations(blocks){
 const visit=items=>{for(const block of items){
  if(block.type!=='codeBlock'&&Array.isArray(block.content))block.content=block.content.flatMap(part=>{
   if(part.type!=='text'||part.styles?.code||part.styles?.spoiler)return [part];
   const out=[];let start=0;
   for(const match of part.text.matchAll(/(?<![\\$])\$(?![\s$])([^$\n]{1,4096}?)(?<![\\\s])\$(?![\d$])/g)){
    if(match.index>start)out.push({...part,text:part.text.slice(start,match.index)});
    out.push({type:'equation',props:{latex:match[1]}});start=match.index+match[0].length;
   }
   if(start<part.text.length)out.push({...part,text:part.text.slice(start)});
   return out.length?out:[part];
  });
  visit(block.children||[]);
 }};visit(blocks);return blocks;
}
