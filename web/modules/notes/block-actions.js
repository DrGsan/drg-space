// Selecting a parent already includes its subtree. Operate on its root once.
export function selectedBlockRoots(editor,block){
  const selected=editor.getSelection()?.blocks;
  const blocks=selected?.some(item=>item.id===block.id)?selected:[block];
  const descendants=new Set();
  const visit=items=>{for(const item of items||[]){descendants.add(item.id);visit(item.children);}};
  for(const item of blocks)visit(item.children);
  return blocks.filter(item=>!descendants.has(item.id));
}

export function inlineText(value){
  if(typeof value==='string')return value;
  if(Array.isArray(value))return value.map(inlineText).join('');
  if(value&&typeof value==='object')return typeof value.text==='string'?value.text:inlineText(value.content);
  return '';
}

export function blockText(block){
  const content=block.type==='table'?(block.content?.rows||[]).map(row=>(row.cells||[]).map(cell=>inlineText(cell.content??cell)).join(' ')).join('\n'):inlineText(block.content);
  return [content,...(block.children||[]).map(blockText)].filter(Boolean).join('\n');
}

export function duplicateBlockTree(block){
  const copy=structuredClone(block);
  const clear=items=>{for(const item of items){delete item.id;clear(item.children||[]);}};
  clear([copy]);
  return copy;
}
