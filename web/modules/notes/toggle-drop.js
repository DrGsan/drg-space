import {selectedBlockRoots} from './block-actions.js';
const dragged=new WeakMap();
const toggle=block=>block.type==='toggleListItem'||block.type==='heading'&&block.props.isToggleable;
export function beginBlockDrag(editor,block){dragged.set(editor,selectedBlockRoots(editor,block).map(item=>item.id));}
export function clearBlockDrag(editor){dragged.delete(editor);}
export function draggedBlockIds(editor){return dragged.get(editor);}
export function moveIntoToggle(editor,ids,targetId){
 const target=editor.getBlock(targetId),blocks=ids.map(id=>editor.getBlock(id)).filter(Boolean);
 if(!target||!toggle(target)||!blocks.length)return false;
 const contains=(block,id)=>block.id===id||block.children.some(child=>contains(child,id));
 if(blocks.some(block=>contains(block,target.id)))return false;
 editor.transact(()=>{editor.removeBlocks(blocks);const current=editor.getBlock(target.id);editor.updateBlock(current,{children:[...current.children,...blocks]});});
 editor.setTextCursorPosition(blocks[0].id,'start');editor.focus();return true;
}
export function bindEmptyToggleDrop(host,editor){
 let highlighted;
 const clear=()=>{highlighted?.remove();highlighted=null;};
 const target=event=>{
  if(!editor.isEditable||!dragged.has(editor))return;
  const button=event.target.closest?.('.bn-toggle-add-block-button');if(!button||!host.contains(button))return;
  const id=button.closest('.bn-block-outer')?.dataset.id,block=id&&editor.getBlock(id);
  if(block&&toggle(block)&&!block.children.length)return {button,id};
 };
 const over=event=>{const value=target(event);if(!value){clear();return;}event.preventDefault();event.stopPropagation();event.dataTransfer.dropEffect='move';
  // Keep decorations outside the ProseMirror DOM: changing the placeholder's
  // attributes would make its NodeView redraw in the middle of a drop.
  if(!highlighted){highlighted=document.createElement('div');highlighted.className='drg-toggle-drop-target';document.body.append(highlighted);}
  const rect=value.button.getBoundingClientRect();Object.assign(highlighted.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px'});
 };
 const drop=event=>{const value=target(event);if(!value)return;event.preventDefault();event.stopPropagation();const ids=dragged.get(editor);clear();clearBlockDrag(editor);editor.prosemirrorView.dragging=null;moveIntoToggle(editor,ids,value.id);};
 const end=()=>{clear();clearBlockDrag(editor);};
 host.addEventListener('dragover',over,true);host.addEventListener('drop',drop,true);document.addEventListener('dragend',end,true);
 return ()=>{end();host.removeEventListener('dragover',over,true);host.removeEventListener('drop',drop,true);document.removeEventListener('dragend',end,true);};
}
