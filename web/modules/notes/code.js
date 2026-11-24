import {tText} from "../../core/i18n.js";
import {toast} from '../../core/ui.js';
import {mermaidPreview} from './mermaid-preview.js';
import {inlineText} from './block-actions.js';
import {pageMenuIcon} from './page-menu.js';
import {createCodeBlockSpec,createExtension} from '@blocknote/core';
import {createHighlightPlugin} from 'prosemirror-highlight';
import {createParser} from 'prosemirror-highlight/shiki';
import {createHighlighterCore} from 'shiki/core';
import {createJavaScriptRegexEngine} from 'shiki/engine/javascript';
import light from 'shiki/themes/github-light-high-contrast.mjs';
import dark from 'shiki/themes/github-dark-high-contrast.mjs';
import python from 'shiki/langs/python.mjs';
import sql from 'shiki/langs/sql.mjs';
import bash from 'shiki/langs/bash.mjs';
import javascript from 'shiki/langs/javascript.mjs';
import typescript from 'shiki/langs/typescript.mjs';
import json from 'shiki/langs/json.mjs';
import html from 'shiki/langs/html.mjs';
import css from 'shiki/langs/css.mjs';
import yaml from 'shiki/langs/yaml.mjs';
import markdown from 'shiki/langs/markdown.mjs';
import dockerfile from 'shiki/langs/dockerfile.mjs';
const languages=Object.assign(Object.create(null),{text:{name:tText("notes:plainText")},mermaid:{name:'Mermaid Diagram'},sql:{name:'SQL'},python:{name:'Python',aliases:['py']},bash:{name:'Bash',aliases:['sh','shell']},javascript:{name:'JavaScript',aliases:['js']},typescript:{name:'TypeScript',aliases:['ts']},json:{name:'JSON'},html:{name:'HTML'},css:{name:'CSS'},yaml:{name:'YAML',aliases:['yml']},markdown:{name:'Markdown',aliases:['md']},docker:{name:'Dockerfile',aliases:['dockerfile']}});
export const codeBlock=createCodeBlockSpec({defaultLanguage:'text',supportedLanguages:languages});
// Notion can store bold/italic annotations inside code. A plain-only node
// silently drops them when a synced copy is opened in the editor.
codeBlock.config={...codeBlock.config,content:'inline',propSchema:{...codeBlock.config.propSchema,wrapLines:{default:true}}};

export function codeLanguages(){return Object.entries(languages);}

export async function copyCode(editor,blockId){
  const block=editor.getBlock(blockId);
  if(block?.type!=='codeBlock')throw new Error(tText('notes:couldNotCopyContent'));
  try{await navigator.clipboard.writeText(inlineText(block.content));}
  catch{throw new Error(tText('notes:couldNotCopyContent'));}
}
// Preserve BlockNote's DOM wrapper and code editing shortcuts.
const renderCode=codeBlock.implementation.render;
codeBlock.implementation.render=function(block,editor){
  const language=block.props.language;
  if(!(language in languages))languages[language]={name:language};
  const rendered=renderCode.call(this,block,editor);
  rendered.dom.querySelector('select')?.setAttribute('aria-label',tText("notes:codeLanguage"));
  if(this.renderType!=='nodeView')return rendered;

  const controls=document.createElement('span');
  controls.className='note-code-actions';
  controls.contentEditable='false';
  const button=document.createElement('button');
  button.type='button';
  button.innerHTML=pageMenuIcon('copy');
  const label=tText('notes:copyCode');
  button.setAttribute('aria-label',label);
  const tooltip=document.createElement('span');
  tooltip.className='note-code-tooltip';
  tooltip.setAttribute('aria-hidden','true');
  tooltip.textContent=label;
  controls.append(button,tooltip);
  rendered.dom.append(controls);
  let disposed=false;
  const hideTooltip=()=>{controls.dataset.tipDismissed='true';};
  const resetTooltip=()=>{delete controls.dataset.tipDismissed;};
  button.onpointerleave=resetTooltip;
  button.onblur=resetTooltip;
  button.onkeydown=event=>{if(event.key==='Escape')hideTooltip();};
  button.onclick=async()=>{
    hideTooltip();
    let message;
    try{await copyCode(editor,block.id);message=tText('notes:codeCopied');}
    catch(error){message=error.message;}
    if(!disposed)toast(message);
  };
  const withPreview=mermaidPreview(rendered,block,editor);
  return {...withPreview,
    stopEvent(event){return controls.contains(event.target)||withPreview.stopEvent?.(event)||false;},
    ignoreMutation(mutation){return controls.contains(mutation.target)||withPreview.ignoreMutation?.(mutation)||false;},
    destroy(){disposed=true;withPreview.destroy?.();},
  };
};
let highlighter;
export const codeHighlighter=()=>highlighter??=createHighlighterCore({themes:[light,dark],langs:[python,sql,bash,javascript,typescript,json,html,css,yaml,markdown,dockerfile],engine:createJavaScriptRegexEngine()});
// The default extension filters out annotated code blocks. Use the same
// cached decoration engine with an explicit node type, without discarding marks.
export const syntaxHighlighting=createExtension(()=>{
  let parser,ready;
  const plain=new Set(['text','none','plaintext','txt']);
  return {key:'syntaxHighlighting',prosemirrorPlugins:[createHighlightPlugin({nodeTypes:['codeBlock'],parser:options=>{
    if(!options.language||plain.has(options.language))return [];
    if(parser)return parser(options);
    ready??=codeHighlighter().then(highlighter=>{
      const parse=createParser(highlighter,{themes:{light:light.name,dark:dark.name},defaultColor:false});
      parser=options=>highlighter.getLoadedLanguages().includes(options.language)?parse(options):[];
      return parser;
    });
    return ready.then(parse=>parse(options));
  }})]};
})();
