export function pageLinkIds(blocks){
  return [...new Set((blocks||[]).flatMap(block=>[...(block.type==='pageLink'?[block.props.pageId]:[]),...pageLinkIds(block.children)]))].sort();
}

export function subpagePlaceholder(editor,id){
  if(!id)return null;
  const block=editor.getBlock(id),content=block?.content;
  // Slash insertion may race with a cursor move. Only an empty paragraph is
  // replaceable; an existing text block must survive creation of a child.
  const empty=typeof content==='string'?!content.trim():Array.isArray(content)&&content.every(part=>part.type==='text'&&!part.text.trim());
  return block?.type==='paragraph'&&empty?id:null;
}
