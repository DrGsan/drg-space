// Layout is ordinary block structure. Every operation preserves child IDs and
// runs in one editor transaction so undo and collaboration see one change.
export const columnWeight=column=>Number.isFinite(column.props?.width)&&column.props.width>0?column.props.width:1;
const paragraph=()=>({id:crypto.randomUUID(),type:'paragraph',content:[],children:[]});
export const newColumn=(children=[paragraph()])=>({id:crypto.randomUUID(),type:'column',props:{width:1},children});
export function columnsBlock(count=2,first){
  if(!Number.isInteger(count)||count<2||count>2000)throw new RangeError('columns');
  return {id:crypto.randomUUID(),type:'columnList',children:Array.from({length:count},(_,index)=>newColumn(index===0&&first?.length?first:undefined))};
}
export function wrapColumns(editor,blocks,count=2){
  if(!editor.isEditable||!blocks.length)return false;
  blocks=blocks.map(block=>editor.getBlock(block.id));if(blocks.some(block=>!block))return false;
  const parent=editor.getParentBlock(blocks[0]),siblings=parent?.children||editor.document;
  const start=siblings.findIndex(item=>item.id===blocks[0].id);
  if(blocks.some((item,index)=>siblings[start+index]?.id!==item.id||['column','columnList'].includes(item.type)))return false;
  const group=columnsBlock(count,blocks);
  editor.transact(()=>editor.replaceBlocks(blocks,[group]));
  editor.setTextCursorPosition(blocks[0].id,'start');editor.focus();return true;
}
export function changeColumns(editor,id,action,index=0){
  const group=editor.getBlock(id);if(!editor.isEditable||group?.type!=='columnList')return false;
  let columns=group.children.map(column=>({...column,props:{...column.props},children:[...column.children]}));
  if(index<0||index>=columns.length)return false;
  if(action==='unwrap'){
    const children=columns.flatMap(column=>column.children);
    editor.transact(()=>editor.replaceBlocks([group],children.length?children:[paragraph()]));return true;
  }
  if(action==='equal')columns=columns.map(column=>({...column,props:{...column.props,width:1}}));
  else if(action==='before'||action==='after'){
    // Split the current column's weight instead of progressively shrinking all
    // its neighbours. Ratios remain valid regardless of their original scale.
    const width=columnWeight(columns[index])/2;columns[index].props.width=width;
    columns.splice(index+(action==='after'?1:0),0,{...newColumn(),props:{width}});
  }else if(action==='left'||action==='right'){
    const next=index+(action==='left'?-1:1);if(next<0||next>=columns.length)return false;
    [columns[index],columns[next]]=[columns[next],columns[index]];
  }else if(action==='merge'){
    if(columns.length<2)return false;
    const next=index?index-1:1,target=columns[next],source=columns[index];
    target.children=index?[...target.children,...source.children]:[...source.children,...target.children];
    target.props.width=columnWeight(target)+columnWeight(source);columns.splice(index,1);
    if(columns.length===1){editor.transact(()=>editor.replaceBlocks([group],columns[0].children));return true;}
  }else return false;
  editor.transact(()=>editor.updateBlock(group,{children:columns}));return true;
}
