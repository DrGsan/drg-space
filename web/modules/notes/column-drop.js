import {draggedBlockIds,clearBlockDrag} from './toggle-drop.js';
import {columnsBlock,newColumn,columnWeight,changeColumns} from './column-actions.js';

const contains=(block,id)=>block.id===id||(block.children||[]).some(child=>contains(child,id));
export function moveBesideBlock(editor,ids,targetId,side){
  const target=editor.getBlock(targetId),blocks=ids.map(id=>editor.getBlock(id));
  if(!editor.isEditable||!target||!blocks.length||blocks.some(block=>!block||['column','columnList'].includes(block.type)||contains(block,targetId)))return false;
  let parent=editor.getParentBlock(target),column=target;
  while(parent&&parent.type!=='columnList'){column=parent;parent=editor.getParentBlock(parent);}
  editor.transact(()=>{
    editor.removeBlocks(blocks);
    if(parent?.type==='columnList'){
      const current=editor.getBlock(parent.id),columns=[...current.children],index=columns.findIndex(item=>item.id===column.id);
      const width=columnWeight(columns[index])/2;
      columns[index]={...columns[index],props:{...columns[index].props,width}};
      columns.splice(index+(side==='right'?1:0),0,{...newColumn(blocks),props:{width}});
      editor.updateBlock(current,{children:columns});
    }else{
      const group=columnsBlock(2,side==='left'?blocks:[target]);group.children[1].children=side==='left'?[target]:blocks;
      editor.replaceBlocks([editor.getBlock(target.id)],[group]);
    }
    const empty=[];
    const visit=items=>items.forEach(item=>{visit(item.children||[]);if(item.type==='columnList')empty.push(item.id);});visit(editor.document);
    for(const id of empty){
      let current=editor.getBlock(id),index;
      while(current?.type==='columnList'&&(index=current.children.findIndex(col=>!col.children.length))>=0){
        if(current.children.length<2)break;
        changeColumns(editor,id,'merge',index);current=editor.getBlock(id);
      }
    }
  });
  editor.setTextCursorPosition(blocks[0].id,'start');editor.focus();return true;
}
export function bindColumnDrop(host,editor){
  let guide;
  const clear=()=>{guide?.remove();guide=null;};
  const target=event=>{
    const ids=draggedBlockIds(editor);if(!editor.isEditable||!ids?.length||innerWidth<=640||event.dataTransfer?.files.length)return;
    const outer=event.target.closest?.('.bn-block-outer[data-id]');if(!outer||!host.contains(outer))return;
    let block=editor.getBlock(outer.dataset.id);if(!block||['column','columnList'].includes(block.type))return;
    let element=outer,parent=editor.getParentBlock(block),column=block;
    while(parent&&parent.type!=='columnList'){column=parent;parent=editor.getParentBlock(parent);}
    if(parent?.type==='columnList')element=host.querySelector(`.bn-block-outer[data-id="${CSS.escape(column.id)}"]`)||outer;
    const rect=element.getBoundingClientRect(),left=event.clientX-rect.left,right=rect.right-event.clientX;
    if(Math.min(left,right)>20||left< -8||right< -8)return;
    if(ids.some(id=>{const source=editor.getBlock(id);return !source||contains(source,block.id);}))return;
    return {id:block.id,rect,side:left<right?'left':'right'};
  };
  const over=event=>{const value=target(event);if(!value){clear();return;}event.preventDefault();event.stopPropagation();event.dataTransfer.dropEffect='move';
    if(!guide){guide=document.createElement('div');guide.className='drg-column-drop-guide';document.body.append(guide);}
    Object.assign(guide.style,{left:(value.side==='left'?value.rect.left-3:value.rect.right)+'px',top:value.rect.top+'px',height:value.rect.height+'px'});
  };
  const drop=event=>{const value=target(event);if(!value)return;event.preventDefault();event.stopPropagation();const ids=draggedBlockIds(editor);clear();clearBlockDrag(editor);editor.prosemirrorView.dragging=null;moveBesideBlock(editor,ids,value.id,value.side);};
  host.addEventListener('dragover',over,true);host.addEventListener('drop',drop,true);document.addEventListener('dragend',clear,true);
  return()=>{clear();host.removeEventListener('dragover',over,true);host.removeEventListener('drop',drop,true);document.removeEventListener('dragend',clear,true);};
}
