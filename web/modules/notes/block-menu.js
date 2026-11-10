import {tText,locale} from "../../core/i18n.js";
import React, {useEffect, useRef, useState} from 'react';
import {Menu as MantineMenu} from '@mantine/core';
import {SideMenuExtension} from '@blocknote/core/extensions';
import {
  AddBlockButton, TableColumnHeaderItem, TableRowHeaderItem,
  useBlockNoteEditor, useComponentsContext, useExtension, useExtensionState, usePortalElement,
} from '@blocknote/react';
import './block-menu.css';
import {selectedBlockRoots,blockText,duplicateBlockTree} from './block-actions.js';
import {beginBlockDrag,clearBlockDrag,moveIntoToggle} from './toggle-drop.js';

import {wrapColumns} from './column-actions.js';
import {calloutKinds} from './outline-callouts.js';
import {calloutIcon} from './callout-icons.js';
const h=React.createElement;
const paths={
  convert:['m7 7 3-3 3 3','M10 4v12','m17 17-3 3-3-3','M14 20V8'],
  color:['M4 3h12l4 4-8 8-8-4V3Z','m12 15-6 6','m3 19 2 2'],
  link:['m10 13 4-4','M8 16H6a4 4 0 0 1-3-7l4-4a4 4 0 0 1 6 0','M16 8h2a4 4 0 0 1 3 7l-4 4a4 4 0 0 1-6 0'],
  duplicate:['M8 8h12v12H8z','M16 8V4H4v12h4'],
  move:['M4 19V8a3 3 0 0 1 3-3h13','m16 1 4 4-4 4'],
  trash:['M3 6h18','M9 6V3h6v3','M6 6l1 15h10l1-15','M10 10v7','M14 10v7'],
  comment:['M4 3h16v13H9l-5 5V3Z','M8 7h8','M8 11h5'],
  up:['m6 14 6-6 6 6'], down:['m6 10 6 6 6-6'],
};
function icon(name){return h('svg',{width:18,height:18,viewBox:'0 0 24 24',fill:'none',stroke:'currentColor',strokeWidth:1.6,strokeLinecap:'round',strokeLinejoin:'round','aria-hidden':true},(paths[name]||[]).map((d,i)=>h('path',{d,key:i})));}

const conversions=[
  [tText("notes:text"),'paragraph',{},'T'],
  [tText("notes:heading1"),'heading',{level:1,isToggleable:false},'H₁'],
  [tText("notes:heading2"),'heading',{level:2,isToggleable:false},'H₂'],
  [tText("notes:heading3"),'heading',{level:3,isToggleable:false},'H₃'],
  [tText("notes:unorderedList"),'bulletListItem',{},'•'],
  [tText("notes:numberedList"),'numberedListItem',{},'1.'],
  [tText("notes:taskList"),'checkListItem',{},'☑'],
  [tText("notes:dropdown"),'toggleListItem',{},'▸'],
  [tText("notes:dropdownHeading1"),'heading',{level:1,isToggleable:true},'▸ H₁'],
  [tText("notes:dropdownHeading2"),'heading',{level:2,isToggleable:true},'▸ H₂'],
  [tText("notes:dropdownHeading3"),'heading',{level:3,isToggleable:true},'▸ H₃'],
  [tText("notes:quote"),'quote',{},'❝'],
  [tText("notes:note"),'callout',{variant:'default'},'💡'],
  ...Object.entries(calloutKinds).map(([variant,kind])=>[kind.title,'callout',{variant},calloutIcon(variant)]),
  ['Mermaid Diagram','codeBlock',{language:'mermaid'},'◇'],
  [tText("notes:code"),'codeBlock',{},'</>'],
];
const typeLabels={paragraph:tText("notes:text"),heading:tText("notes:heading"),bulletListItem:tText("notes:unorderedList"),numberedListItem:tText("notes:numberedList"),checkListItem:tText("notes:taskList"),toggleListItem:tText("notes:dropdown"),quote:tText("notes:quote"),callout:tText("notes:note"),codeBlock:tText("notes:code"),table:tText("notes:table"),image:tText("notes:image"),video:tText("notes:video"),audio:tText("notes:audio"),file:tText("notes:file"),divider:tText("notes:separator"),pageLink:tText("notes:pageLink")};
const colors=[['default',tText("notes:default")],['gray',tText("notes:gray")],['brown',tText("notes:brown")],['orange',tText("notes:orange")],['yellow',tText("notes:yellow")],['green',tText("notes:green")],['blue',tText("notes:blue")],['purple',tText("notes:purple")],['pink',tText("notes:pink")],['red',tText("notes:red")]];

function BlockActionsContent({options,page}){
  const editor=useBlockNoteEditor();
  const Components=useComponentsContext();
  const Menu=Components.Generic.Menu;
  const hovered=useExtensionState(SideMenuExtension,{editor,selector:state=>state?.block});
  const selected=useRef(null);
  if(!selected.current&&hovered)selected.current=selectedBlockRoots(editor,hovered).map(item=>item.id);
  const [query,setQuery]=useState('');
  const search=useRef(null);
  useEffect(()=>{const frame=requestAnimationFrame(()=>search.current?.focus());return()=>cancelAnimationFrame(frame);},[]);
  if(!hovered)return null;
  const blocks=(selected.current||[hovered.id]).map(id=>editor.getBlock(id)).filter(Boolean);
  if(!blocks.length)return null;
  const first=blocks[0];
  const matches=(...labels)=>!query.trim()||labels.some(label=>label.toLocaleLowerCase(locale()).includes(query.trim().toLocaleLowerCase(locale())));
  const run=fn=>()=>{if(!editor.isEditable)return;Promise.resolve().then(fn).catch(error=>options.toast?.(error.message||tText("notes:failedToExecuteBlockAction")));};
  const select=items=>items.length>1?editor.setSelection(items[0],items.at(-1)):editor.setTextCursorPosition(items[0],'end');
  const item=(name,label,action,extra={})=>h(Menu.Item,{key:name,className:'bn-menu-item drg-block-menu-item',icon:icon(name),onClick:run(action),...extra},label);
  // Mantine submenus must remain DOM descendants for pointer and keyboard
  // handling. Their parent must not clip the floating panel.
  const submenu=(name,label,children)=>h(MantineMenu.Sub,{key:name,position:'right-start',floatingStrategy:'fixed',middlewares:{flip:true,shift:{crossAxis:true,padding:12},size:{padding:12}}},h(Menu.Trigger,{sub:true},h(Menu.Item,{className:'bn-menu-item drg-block-menu-item',subTrigger:true,icon:icon(name)},label)),h(Menu.Dropdown,{sub:true,className:'bn-menu-dropdown drg-block-submenu'},children));
  const canConvert=blocks.every(block=>['inline','plain'].includes(editor.schema.blockSpecs[block.type]?.config.content));
  const convertItems=conversions.filter(([label,type])=>editor.schema.blockSpecs[type]&&matches(tText("notes:convert"),label)).map(([label,type,props,symbol])=>h(Menu.Item,{key:label,className:'bn-menu-item drg-block-menu-item',icon:h('span',{className:'drg-block-type-icon','aria-hidden':true},symbol),checked:blocks.every(block=>block.type===type&&Object.entries(props).every(([key,value])=>block.props[key]===value)),onClick:run(()=>{editor.focus();editor.transact(()=>blocks.forEach(block=>editor.updateBlock(block,{type,props})));})},label));
  const colorSections=['textColor','backgroundColor'].flatMap(prop=>{
    const compatible=blocks.filter(block=>editor.schema.blockSpecs[block.type]?.config.propSchema[prop]);
    if(!compatible.length)return [];
    const title=prop==='textColor'?tText("notes:textColor"):tText("notes:backgroundColor");
    const visible=colors.filter(([,label])=>matches(tText("notes:color"),title,label));
    return visible.length?[h(Menu.Label,{key:prop},title),...visible.map(([color,label])=>h(Menu.Item,{key:prop+color,className:'bn-menu-item drg-block-menu-item',icon:h('span',{className:'bn-color-icon drg-block-color','data-text-color':prop==='textColor'?color:'default','data-background-color':prop==='backgroundColor'?color:'default','aria-hidden':true},'A'),checked:compatible.every(block=>block.props[prop]===color),onClick:run(()=>editor.transact(()=>compatible.forEach(block=>editor.updateBlock(block,{props:{[prop]:color}}))))},label))]:[];
  });
  const moveItems=[];
  const targets=[],excluded=new Set();
  const exclude=items=>items.forEach(block=>{excluded.add(block.id);exclude(block.children||[]);});exclude(blocks);
  const visit=items=>{for(const block of items){if((block.type==='toggleListItem'||block.type==='heading'&&block.props.isToggleable)&&!excluded.has(block.id))targets.push(block);visit(block.children||[]);}};
  visit(editor.document);
  if(targets.length)moveItems.push(submenu('move',tText("notes:toExpandableSection"),targets.map(target=>item('move',blockText({...target,children:[]}).slice(0,70)||tText("notes:untitled"),()=>{moveIntoToggle(editor,blocks.map(block=>block.id),target.id);},{key:'toggle-'+target.id}))));
  moveItems.push(item('move',tText("notes:increaseIndentTab"),()=>{select(blocks);if(editor.canNestBlock())editor.nestBlock();editor.focus();}));
  moveItems.push(item('move',tText("notes:decreaseIndentShiftTab"),()=>{select(blocks);if(editor.canUnnestBlock())editor.unnestBlock();editor.focus();}));
  if(editor.getPrevBlock(first))moveItems.push(item('up',tText("notes:toTheLineAbove"),()=>{select(blocks);editor.moveBlocksUp();editor.focus();}));
  if(editor.getNextBlock(blocks.at(-1)))moveItems.push(item('down',tText("notes:toTheLineBelow"),()=>{select(blocks);editor.moveBlocksDown();editor.focus();}));
  if(options.moveBlocks)moveItems.push(item('move',tText("notes:toAnotherPage"),()=>options.moveBlocks(blocks.map(block=>block.id))));
  const text=blocks.map(blockText).join('\n');
  const words=text.trim()?text.trim().split(/\s+/u).length:0;
  const actions=[];
  if(canConvert&&convertItems.length)actions.push(submenu('convert',tText("notes:convertTo"),convertItems));
  if(colorSections.length)actions.push(submenu('color',tText("notes:color"),colorSections));
  if(blocks.length===1&&first.type==='table'){
    if(matches(tText("notes:headingRow")))actions.push(h(TableRowHeaderItem,{key:'table-row'},tText("notes:headingRow")));
    if(matches(tText("notes:headingColumn")))actions.push(h(TableColumnHeaderItem,{key:'table-column'},tText("notes:headingColumn")));
  }
  if(actions.length&&matches(tText("notes:copyBlockLink"),'Copy link',tText("notes:createCopy"),tText("notes:duplicate"),'Duplicate',tText("notes:move"),'Move',tText("notes:lineAbove"),tText("notes:lineBelow"),tText("notes:goToAnotherPage"),tText("notes:delete"),'Delete'))actions.push(h(Menu.Divider,{key:'transform-divider'}));
  if(matches(tText("notes:copyBlockLink"),'Copy link'))actions.push(item('link',tText("notes:copyBlockLink"),async()=>{
    if(options.copyBlockLink)return options.copyBlockLink(first.id);
    if(!page.id||page.id.startsWith('draft_'))throw new Error(tText("notes:saveThePageFirst"));
    const url=new URL('/page/'+encodeURIComponent(page.id),location.origin);url.hash='block='+encodeURIComponent(first.id);
    await navigator.clipboard.writeText(url.href);options.toast?.(tText("notes:blockLinkCopied"));
  }));
  if(blocks.every(block=>!['column','columnList'].includes(block.type))&&matches(tText('notes:columns')))actions.push(submenu('columns',tText('notes:columns'),[2,3,4,5].map(count=>item('columns'+count,tText('notes:columnsCount',{count}),()=>wrapColumns(editor,blocks,count)))));
  if(matches(tText("notes:createCopy"),tText("notes:duplicate"),'Duplicate'))actions.push(item('duplicate',tText("notes:createCopy"),()=>{editor.focus();const current=blocks.map(block=>editor.getBlock(block.id)).filter(Boolean);if(!current.length)return;const copies=editor.insertBlocks(current.map(duplicateBlockTree),current.at(-1),'after');select(copies);}));
  if(moveItems.length&&matches(tText("notes:move"),'Move',tText("notes:lineAbove"),tText("notes:lineBelow"),tText("notes:goToAnotherPage")))actions.push(submenu('move',tText("notes:move"),moveItems));
  if(matches(tText("notes:delete"),'Delete'))actions.push(item('trash',tText("notes:delete"),()=>{editor.focus();editor.removeBlocks(blocks);},{className:'bn-menu-item drg-block-menu-item drg-block-menu-danger'}));
  if(options.onComment&&matches(tText("notes:comment"),'Comment')){
    if(actions.length)actions.push(h(Menu.Divider,{key:'comment-divider'}));
    actions.push(item('comment',tText("notes:comment"),()=>options.onComment(first.id)));
  }
  return h(React.Fragment,null,
    h('div',{className:'drg-block-menu-search'},h('input',{ref:search,type:'search',value:query,autoFocus:true,placeholder:tText("notes:searchingForActions"),'aria-label':tText("notes:searchingForBlockActions"),onChange:event=>setQuery(event.target.value),onKeyDown:event=>{
      if(event.key==='Escape')return;
      event.stopPropagation();
      if(['ArrowDown','Enter'].includes(event.key)){
        event.preventDefault();const firstItem=event.currentTarget.closest('.drg-block-menu')?.querySelector('[role="menuitem"]');firstItem?.focus();if(event.key==='Enter')firstItem?.click();
      }
    }})),
    h(Menu.Label,null,blocks.length>1?tText("notes:selectedBlocks", {value0:(blocks.length)}):typeLabels[first.type]||first.type),
    h('div',{className:'drg-block-menu-actions'},actions.length?actions:h('p',{className:'drg-block-menu-empty',role:'status'},tText("notes:noActionsFound"))),
    h(Menu.Divider,null),
    h('div',{className:'drg-block-menu-meta'},tText("notes:wordsCharacters", {value0:(words), value1:(Array.from(text).length)})),
  );
}

function BlockActionsMenu({options,page}){
  const editor=useBlockNoteEditor();
  const Components=useComponentsContext();
  const sideMenu=useExtension(SideMenuExtension,{editor});
  // Native DragHandleButton freezes/unfreezes the side menu on every opening.
  // The Dropdown component itself stays mounted while closed. Keep its action
  // content inside that lifecycle so hover cannot leave a stale block snapshot,
  // and each opening gets a fresh search field and selection.
  const block=useExtensionState(SideMenuExtension,{editor,selector:state=>sideMenu.menuFrozen?state?.block:undefined});
  return h(Components.Generic.Menu.Dropdown,{className:'bn-menu-dropdown bn-drag-handle-menu drg-block-menu'},
    block?h(BlockActionsContent,{options,page,key:block.id}):null,
  );
}

function BlockDragHandle({options,page}){
  const editor=useBlockNoteEditor();
  const Components=useComponentsContext();
  const portalElement=usePortalElement();
  const sideMenu=useExtension(SideMenuExtension,{editor});
  const pressedBlock=useRef(null);
  const block=useExtensionState(SideMenuExtension,{editor,selector:state=>state?.block});
  if(!block)return null;
  return h(Components.Generic.Menu.Root,{
    position:'left',portalElement,
    // BlockNote documents this option for surfaces whose input owns focus.
    // Otherwise Mantine's delayed FocusTrap chooses its initial placeholder
    // after the search field focuses and steals that focus on slower frames.
    preventFocusOnOpen:true,
    onOpenChange:open=>open?sideMenu.freezeMenu():sideMenu.unfreezeMenu(),
  },
    h(Components.Generic.Menu.Trigger,null,
      h(Components.SideMenu.Button,{
        className:'bn-button',label:editor.dictionary.side_menu.drag_handle_label,
        draggable:true,
        // The same handle follows hovered blocks. Freeze its identity on press,
        // before crossing another block can trigger the native drag threshold.
        onPointerDown:event=>{if(event.button===0){pressedBlock.current=block;sideMenu.freezeMenu();}},
        onDragStart:event=>{sideMenu.blockDragStart(event,pressedBlock.current||block);beginBlockDrag(editor,pressedBlock.current||block);},
        onDragEnd:()=>{pressedBlock.current=null;clearBlockDrag(editor);sideMenu.blockDragEnd();sideMenu.unfreezeMenu();editor.focus();},
        icon:h('svg',{width:24,height:24,viewBox:'0 0 24 24','data-test':'dragHandle','aria-hidden':true,fill:'currentColor'},
          [5,12,19].flatMap(y=>[9,15].map(x=>h('circle',{key:x+'-'+y,cx:x,cy:y,r:1.7})))),
      }),
    ),
    h(BlockActionsMenu,{options,page}),
  );
}

export function createBlockSideMenu(options,page){
  return function BlockSideMenu(){
    const Components=useComponentsContext();
    return h(Components.SideMenu.Root,{className:'bn-side-menu'},h(AddBlockButton),h(BlockDragHandle,{options,page}));
  };
}
