import {tText} from "../../core/i18n.js";
import React,{useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {createReactInlineContentSpec,FormattingToolbar,getFormattingToolbarItems,BasicTextStyleButton,useBlockNoteEditor,useComponentsContext,useEditorState} from '@blocknote/react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import './text-formatting.css';
const h=React.createElement,dialogs=new WeakMap();
export function closeEquationDialogs(editor){dialogs.get(editor)?.();}
const mathOptions={throwOnError:true,trust:false,strict:'error',maxSize:20,maxExpand:500,output:'htmlAndMathml'};
export function equationHTML(source){return katex.renderToString(source,{...mathOptions,macros:{}});}
function MathView({source}){
 try{return h('span',{className:'drg-equation-render',dangerouslySetInnerHTML:{__html:equationHTML(source)}});}catch{return h('span',{className:'drg-equation-invalid'},source);}
}
function equationDialog(editor,source,apply){
 closeEquationDialogs(editor);
 const savedSelection=editor.prosemirrorView.state.selection.getBookmark(),container=document.createElement('div');container.className='drg-equation-overlay';container.setAttribute('role','dialog');container.setAttribute('aria-modal','true');container.setAttribute('aria-label',tText("notes:formula"));document.body.append(container);
 const root=createRoot(container),previous=document.activeElement;let closed=false;
 const close=()=>{if(closed)return;closed=true;dialogs.delete(editor);root.unmount();container.remove();previous?.isConnected&&previous.focus?.();};dialogs.set(editor,close);
 function Dialog(){
  const [value,setValue]=React.useState(source),[error,setError]=React.useState(''),input=useRef(null);
  React.useEffect(()=>{input.current?.focus();input.current?.select();},[]);
  const save=event=>{
   event.preventDefault();const latex=value.trim().replace(/^\$\$?([\s\S]*?)\$\$?$/,'$1').trim();
   if(!latex||latex.length>4096){setError(tText("notes:enterAFormulaUpTo4096CharactersLong"));return;}
   try{equationHTML(latex);}catch{setError(tText("notes:checkTheLatexFormula"));return;}
   try{editor.focus();editor.transact(tr=>tr.setSelection(savedSelection.resolve(tr.doc)));apply(latex);close();}catch{setError(tText("notes:textHasChangedSelectItAgain"));}
  };
  return h('form',{className:'drg-equation-dialog',onSubmit:save,onKeyDown:event=>{if(event.key==='Escape'){event.preventDefault();close();}else if(event.key==='Tab'){const controls=[...event.currentTarget.querySelectorAll('textarea,button')],first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}}},
   h('h2',null,tText("notes:formula")),h('label',null,tText("notes:latexFormula"),h('textarea',{ref:input,value,maxLength:4096,rows:3,placeholder:'E = mc^2',onChange:event=>{setValue(event.target.value);setError('');}})),
   h('div',{className:'drg-equation-preview','aria-label':tText("notes:previewFormula")},h(MathView,{source:value||'E=mc^2'})),
   h('p',{role:'status',className:'drg-equation-error'},error),h('div',{className:'drg-equation-actions'},h('button',{type:'button',className:'secondary',onClick:close},tText("notes:cancel")),h('button',{type:'submit',className:'primary'},tText("notes:done"))));
 }
 root.render(h(Dialog));
}
export const equationInline=createReactInlineContentSpec({type:'equation',propSchema:{latex:{default:''}},content:'none'}, {
 render:({inlineContent,editor,updateInlineContent})=>h('span',{className:'drg-inline-equation',contentEditable:false,role:editor.isEditable?'button':undefined,tabIndex:editor.isEditable?0:undefined,'aria-label':tText("notes:formula2", {value0:(inlineContent.props.latex)}),title:editor.isEditable?tText("notes:changeFormula"):undefined,
  onClick:()=>{if(editor.isEditable)equationDialog(editor,inlineContent.props.latex,latex=>updateInlineContent({type:'equation',props:{latex}}));},
  onKeyDown:event=>{if(editor.isEditable&&(event.key==='Enter'||event.key===' ')){event.preventDefault();equationDialog(editor,inlineContent.props.latex,latex=>updateInlineContent({type:'equation',props:{latex}}));}}},h(MathView,{source:inlineContent.props.latex})),
 parse:element=>element.hasAttribute('data-equation')?{latex:element.getAttribute('data-equation')}:undefined,
 toExternalHTML:({inlineContent})=>h('span',{'data-equation':inlineContent.props.latex},'$'+inlineContent.props.latex+'$'),
});
function FormattingExtras(){
 const editor=useBlockNoteEditor(),Components=useComponentsContext();
 const state=useEditorState({editor,selector:({editor})=>({hidden:!!editor.getActiveStyles().spoiler,text:editor.getSelectedText()})});
 if(!editor.isEditable||!state.text)return null;
 return h(React.Fragment,null,
  h(Components.FormattingToolbar.Button,{className:'bn-button drg-spoiler-button',label:tText("notes:hidden"),mainTooltip:tText("notes:hiddenText"),secondaryTooltip:'Ctrl / ⌘ + Shift + P',isSelected:state.hidden,onClick:()=>{editor.focus();editor.toggleStyles({spoiler:true});},icon:h('span',{className:'drg-format-spoiler'},h('svg',{viewBox:'0 0 24 24',fill:'none',stroke:'currentColor',strokeWidth:1.7,'aria-hidden':true},h('path',{d:'M3 3l18 18M10.6 10.6a2 2 0 002.8 2.8M9.9 5.2A11 11 0 0112 5c5 0 9 7 9 7a19 19 0 01-3.2 3.9M6.2 6.2A25 25 0 003 12s4 7 9 7a10 10 0 004.1-.9'})),tText("notes:hidden")),'data-format-spoiler':true}),
  h(BasicTextStyleButton,{basicTextStyle:'code'}),
  h(Components.FormattingToolbar.Button,{className:'bn-button',label:tText("notes:formula"),mainTooltip:tText("notes:markAsFormula"),onClick:()=>equationDialog(editor,editor.getSelectedText(),latex=>editor.insertInlineContent([{type:'equation',props:{latex}}])),icon:h('span',{className:'drg-format-equation','aria-hidden':true},'√x') ,'data-format-equation':true}),
  h(Components.FormattingToolbar.Button,{className:'bn-button',label:tText("notes:clearFormatting"),mainTooltip:tText("notes:clearFormatting"),onClick:()=>{editor.focus();editor.transact(tr=>tr.removeMark(tr.selection.from,tr.selection.to));},icon:h('span',{className:'drg-format-clear','aria-hidden':true},'Tₓ'),'data-format-clear':true}));
}
export function TextFormattingToolbar(){
 const items=getFormattingToolbarItems(),primary=new Set(['blockTypeSelect','boldStyleButton','italicStyleButton','underlineStyleButton','strikeStyleButton','textAlignLeftButton','textAlignCenterButton','textAlignRightButton']);
 return h(FormattingToolbar,null,h('div',{className:'drg-format-row'},...items.filter(item=>primary.has(item.key))),h('div',{className:'drg-format-row'},h(FormattingExtras,{key:'drg-formatting'}),...items.filter(item=>!primary.has(item.key))));
}
