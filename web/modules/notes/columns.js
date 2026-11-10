import React,{useRef,useState} from 'react';
import {Menu} from '@mantine/core';
import {changeColumns,columnWeight} from './column-actions.js';
export {columnsBlock} from './column-actions.js';
import {createReactBlockSpec,usePortalElement} from '@blocknote/react';
import {tText} from '../../core/i18n.js';
import './columns.css';

const h=React.createElement;
// Layout uses normal nested blocks, so the same document is editable through
// the existing CRDT, native editor and portable archives. No opaque HTML body.
export const columnList=createReactBlockSpec({type:'columnList',propSchema:{},content:'none'}, {
  render:({block,editor})=>h('div',{className:'note-columns-marker'},editor.isEditable&&h(ColumnMenu,{editor,group:block})),
  toExternalHTML:()=>h('div',{className:'column-list'}),
})();

function ColumnMenu({editor,group,index}){
  const portalElement=usePortalElement();
  const [opened,setOpened]=useState(false);
  const all=index===undefined,at=index??0;
  const item=(action,key,disabled=false)=>h(Menu.Item,{key:action,className:'bn-menu-item',disabled,onClick:()=>{setOpened(false);queueMicrotask(()=>{const fresh=editor.getBlock(group.id),current=all?0:fresh?.children.findIndex(c=>c.id===group.children[at]?.id);if(current>=0)changeColumns(editor,group.id,action,current);});}},tText('notes:'+key));
  return h(Menu,{opened,onChange:setOpened,position:'bottom-start',withinPortal:!!portalElement,portalProps:portalElement?{target:portalElement}:undefined,returnFocus:false,middlewares:{flip:true,shift:{padding:12}},shadow:'md'},
    h(Menu.Target,null,h('button',{type:'button',className:'note-column-menu',contentEditable:false,'aria-label':all?tText('notes:columns'):tText('notes:columnNumber',{number:at+1}),onPointerDown:event=>event.stopPropagation()},all?'▥':'⋯')),
    h(Menu.Dropdown,{contentEditable:false,className:'bn-menu-dropdown note-column-dropdown'},
      h(Menu.Label,null,all?tText('notes:columns'):tText('notes:columnNumber',{number:at+1})),
      item('before','addColumnBefore'),item('after','addColumnAfter'),
      !all&&item('left','columnLeft',at===0),!all&&item('right','columnRight',at===group.children.length-1),
      item('equal','equalColumns'),!all&&item('merge','mergeColumn'),h(Menu.Divider),item('unwrap','unwrapColumns')));
}
function Column({block,editor}){
  const ref=useRef(null),drag=useRef(null),[preview,setPreview]=useState(null);
  const parent=editor.getParentBlock(block),index=parent?.children.findIndex(child=>child.id===block.id);
  const pair=()=>{
    const parent=editor.getParentBlock(block),index=parent?.children.findIndex(child=>child.id===block.id);
    if(parent?.type!=='columnList'||index<0||index>=parent.children.length-1)return null;
    return [parent.children[index],parent.children[index+1]];
  };
  const resize=(ratio,columns)=>{
    if(!editor.isEditable||!columns)return;
    const [left,right]=columns,total=columnWeight(left)+columnWeight(right);
    const width=Math.max(.01,Math.min(.99,ratio))*total;
    editor.transact(()=>{editor.updateBlock(left,{props:{width}});editor.updateBlock(right,{props:{width:total-width}});});
  };
  const start=event=>{
    const columns=pair();if(!columns||!editor.isEditable)return;
    event.preventDefault();event.stopPropagation();
    const left=ref.current.closest('.bn-block-outer'),right=left.nextElementSibling;
    if(!right)return;
    const a=left.getBoundingClientRect(),b=right.getBoundingClientRect();
    drag.current={columns,start:event.clientX,ratio:a.width/(a.width+b.width),size:a.width+b.width};
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const width=columnWeight(block);
  const finish=()=>{const d=drag.current;drag.current=null;if(d?.preview!==undefined)resize(d.preview,d.columns);setPreview(null);};
  // Mutating the ProseMirror-owned outer DOM from a React effect makes its
  // mutation observer recreate the node view. Keep presentation in this view.
  let style=`.bn-block-outer[data-id="${CSS.escape(block.id)}"]{--column-width:${width}}`;
  if(preview){const columns=drag.current?.columns;if(columns){const total=columns.reduce((sum,c)=>sum+columnWeight(c),0);style+=columns.map((c,i)=>`.bn-root .bn-block-outer[data-id="${CSS.escape(c.id)}"]{--column-width:${total*(i?1-preview.ratio:preview.ratio)}}`).join('');}}
  return h('div',{className:'note-column-marker',ref},h('style',null,style),editor.isEditable&&parent?.type==='columnList'&&h(ColumnMenu,{editor,group:parent,index}),h('button',{
    type:'button',className:'note-column-resize',contentEditable:false,
    role:'separator','aria-orientation':'vertical','aria-label':tText('notes:resizeColumns'),
    'aria-valuemin':1,'aria-valuemax':99,'aria-valuenow':Math.round(100*columnWeight(block)/(pair()?.reduce((sum,c)=>sum+columnWeight(c),0)||columnWeight(block))),
    onPointerDown:start,onPointerMove:event=>{const d=drag.current;if(d){d.preview=Math.max(.01,Math.min(.99,d.ratio+(event.clientX-d.start)/d.size));setPreview({ratio:d.preview});}},
    onPointerUp:finish,onLostPointerCapture:()=>{drag.current=null;setPreview(null);},onPointerCancel:()=>{drag.current=null;setPreview(null);},
    onKeyDown:event=>{if(['ArrowLeft','ArrowRight','Home'].includes(event.key)){event.preventDefault();event.stopPropagation();const columns=pair();if(columns){const total=columns.reduce((s,c)=>s+columnWeight(c),0);resize(event.key==='Home'?.5:columnWeight(columns[0])/total+(event.key==='ArrowRight'?.05:-.05),columns);}}},
  }));
}
export const column=createReactBlockSpec({type:'column',propSchema:{width:{default:1}},content:'none'}, {
  render:Column,toExternalHTML:({block})=>h('div',{className:'column',style:{flexGrow:block.props.width}}),
})();
