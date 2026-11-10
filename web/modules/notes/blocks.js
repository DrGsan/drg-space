import {tText} from "../../core/i18n.js";
import React from "react";
import { createRoot } from "react-dom/client";
import {
  BlockNoteEditor,
  BlockNoteSchema,
  defaultBlockSpecs,
  defaultInlineContentSpecs,
  defaultStyleSpecs,
  createStyleSpecFromTipTapMark,
  COLORS_DEFAULT,
  COLORS_DARK_MODE_DEFAULT,
  defaultProps,
  filterSuggestionItems,
} from "@blocknote/core";
import { withCollaboration, blocksToYDoc } from "@blocknote/core/yjs";
import {ru,en} from "@blocknote/core/locales";
const blockNoteLocales={ru,en};
import {language} from "../../core/i18n.js";
import {
  createReactBlockSpec,
  getDefaultReactSlashMenuItems,
  SuggestionMenuController,
  SideMenuController,
  FormattingToolbarController,
} from "@blocknote/react";
import { BlockNoteView } from "@blocknote/mantine";
import "@blocknote/core/fonts/inter.css";
import "@blocknote/mantine/style.css";
import "./code-theme.css";
import "./block-layout.css";
import './callouts.css';
import {normalizeOutlineCallouts,calloutKinds,blocksMarkdown} from './outline-callouts.js';
import {calloutIcon} from './callout-icons.js';
import {codeBlock,syntaxHighlighting} from './code.js';
import {createBlockSideMenu} from './block-menu.js';
import {blockSideMenuPosition} from './side-menu-position.js';
import {equationInline,TextFormattingToolbar,closeEquationDialogs} from './text-formatting.js';
import {equationTextProjection,parseInlineEquations} from './equation-markdown.js';
import {spoilerStyle,spoilerShortcuts,bindSpoilers} from './spoilers.js';
import {spoilerMarkdown,parseInlineSpoilers} from './spoiler-markdown.js';
import {bindEmptyToggleDrop} from './toggle-drop.js';
import {linkPreviews,linkPasteHandler,linkParagraphSpec} from './link-preview.js';
import {parseImportedMarkdown} from './import-markdown.js';
import {normalizeToggleHTML} from './paste.js';
import {pageIconUrl} from '../../core/page-assets.js';
import {column,columnList} from './columns.js';
import {wrapColumns} from './column-actions.js';
import {bindColumnDrop} from './column-drop.js';
const h = React.createElement;
const contexts = new WeakMap();
const PageLink = createReactBlockSpec(
  {
    type: "pageLink",
    propSchema: {
      pageId: { default: "" },
      title: { default: tText("notes:newPage") },
    },
    content: "none",
  },
  {
    render: ({ block, editor }) => {
      const ctx = contexts.get(editor) || {};
      const page = ctx.getPage?.(block.props.pageId);
      const iconUrl=page&&pageIconUrl(page);
      return h(
        "button",
        {
          type: "button",
          className: "inline-page-link",
          disabled: !ctx.openPage || (!!ctx.getPage && !page),
          onClick: () => ctx.openPage?.(block.props.pageId),
        },
        h('span',{className:'inline-page-link-icon','aria-hidden':true},iconUrl?h('img',{className:'page-custom-icon',src:iconUrl,alt:''}):page?.icon||'📄'),
        h('span',{className:'inline-page-link-title'},page?.title || (ctx.getPage ? tText("notes:pageNotAvailable") : block.props.title)),
      );
    },
  },
)();
function CalloutContent({block,editor,contentRef}) {
  const key=block.props.backgroundColor&&block.props.backgroundColor!=='default'?block.props.backgroundColor:'gray';
  const light=COLORS_DEFAULT[key]?.background||(CSS.supports('color',key)?key:COLORS_DEFAULT.gray.background);
  const dark=COLORS_DARK_MODE_DEFAULT[key]?.background||light;
  const instance=React.useId();
  const selector=`.bn-container .bn-block:has(>.react-renderer>.bn-block-content>.block-callout[data-callout-instance="${CSS.escape(instance)}"])`;
  const pick=contexts.get(editor)?.pickCalloutIcon;
  const editable=editor.isEditable&&block.props.variant==='default'&&!!pick;
  return h(
    "aside",
    { className: "block-callout", "data-callout-kind":block.props.variant,"data-callout-instance":instance },
    block.props.variant==='default'&&h('style',null,`${selector}{--note-callout-background:${light}}:root[data-scheme="dark"] ${selector}{--note-callout-background:color-mix(in srgb,${dark} 25%,var(--white))}`),
    h(editable?'button':'span', {contentEditable:false,className:'callout-symbol',...editable?{type:'button','aria-label':tText('notes:calloutIcon'),onClick:()=>pick(icon=>{if(editor.isEditable&&editor.getBlock(block.id))editor.updateBlock(block.id,{props:{icon}});})}:{}}, calloutKinds[block.props.variant]?calloutIcon(block.props.variant):block.props.icon),
    h("div", { ref: contentRef }),
  );
}

const Callout = createReactBlockSpec(
  {
    type: "callout",
    propSchema: { ...defaultProps, icon: { default: "💡" }, variant:{default:"default"} },
    content: "inline",
  },
  {
    render: props=>h(CalloutContent,props),
  },
)();
export const schema = BlockNoteSchema.create({
  blockSpecs: { ...defaultBlockSpecs, paragraph:linkParagraphSpec(defaultBlockSpecs.paragraph), codeBlock, pageLink: PageLink, callout: Callout, column, columnList },
  inlineContentSpecs:{...defaultInlineContentSpecs,equation:equationInline},
  styleSpecs:{...defaultStyleSpecs,code:createStyleSpecFromTipTapMark(defaultStyleSpecs.code.implementation.mark.extend({excludes:''}),'boolean'),spoiler:spoilerStyle},
});
export function mountBlockEditor(host, page, options = {}) {
  const normalized=normalizeOutlineCallouts(page.blocks);
  const previewController=new AbortController();
  const config = {schema,extensions:[syntaxHighlighting,spoilerShortcuts(),...options.secret?[]:[linkPreviews({signal:previewController.signal,readOnly:!!options.readOnly})]],dictionary:blockNoteLocales[language()]||blockNoteLocales.en,tabBehavior:'prefer-indent',initialContent:normalized.blocks?.length?normalized.blocks:undefined,
    uploadFile:options.uploadFile,
    resolveFileUrl:async value=>{
      if(options.resolveFileUrl)return options.resolveFileUrl(value);
      const url=new URL(value,location.origin);
      const match=url.origin===location.origin&&/^\/api\/notes\/[^/]+\/files\/([^/]+)$/.exec(url.pathname);
      return match?(options.fileUrl?.(match[1])||location.origin+'/api/notes/'+page.id+'/files/'+match[1]):value;
    },
    pasteHandler:({event,editor,defaultPasteHandler})=>{
      if(!options.readOnly&&!options.secret&&linkPasteHandler(event,editor))return true;
      const html=event.clipboardData?.getData('text/html');
      const normalized=html&&!event.clipboardData.types.includes('blocknote/html')&&normalizeToggleHTML(html);
      if(normalized){
        const blocks=editor.tryParseHTMLToBlocks(normalized),current=editor.getTextCursorPosition().block;
        // ProseMirror's open paste slice joins the first toggle to the current
        // paragraph and discards its type. Insert complete block trees instead.
        const empty=current.type==='paragraph'&&!current.content?.length&&!current.children.length;
        const inserted=empty?editor.replaceBlocks([current],blocks).insertedBlocks:editor.insertBlocks(blocks,current,'after');
        if(inserted?.length)editor.setTextCursorPosition(inserted.at(-1),'end');
        return true;
      }
      return defaultPasteHandler({prioritizeMarkdownOverHTML:false});
    },
  };
  const editor=BlockNoteEditor.create(options.collaboration?withCollaboration({...config,collaboration:options.collaboration}):config);
  if (!options.collaboration && (page.blocks === null || page.blocks === undefined)) {
    const parsed=normalizeOutlineCallouts(parseImportedMarkdown(editor,page.body || "",options.fileOrigin));
    normalized.changed ||= parsed.changed;
    const blocks = parseInlineEquations(parseInlineSpoilers(parsed.blocks));
    const importedFiles=items=>items.some(item=>item.type==='file'||importedFiles(item.children||[]));
    normalized.changed ||= importedFiles(blocks);
    if (blocks.length) editor.replaceBlocks(editor.document, blocks);
  }
  // Repair only affected ranges in an existing canonical shared document.
  // Unrelated blocks keep their Yjs identities and concurrent edits.
  const stopNormalize=options.collaboration&&!options.readOnly?editor.onMount(()=>{
    const original=editor.document,converted=normalizeOutlineCallouts(original);
    if(converted.changed){
      const positions=new Map(original.map((block,index)=>[block.id,index])),changes=[];let i=0,j=0;
      while(i<original.length){
        if(j<converted.blocks.length&&JSON.stringify(original[i])===JSON.stringify(converted.blocks[j])){i++;j++;continue;}
        let a=original.length,b=converted.blocks.length;
        for(let k=j;k<converted.blocks.length;k++){const index=positions.get(converted.blocks[k].id);if(index>=i&&JSON.stringify(original[index])===JSON.stringify(converted.blocks[k])){a=index;b=k;break;}}
        changes.push([original.slice(i,a),converted.blocks.slice(j,b)]);i=a;j=b;
      }
      editor.transact(()=>{for(const [before,after] of changes.reverse())editor.replaceBlocks(before,after);});
    }
  }):()=>{};
  contexts.set(editor, options);
  const root = createRoot(host);
  const blockSideMenu = createBlockSideMenu(options, page);
  const slashItems = async (query) => {
    const items = getDefaultReactSlashMenuItems(editor);
    for(const count of [2,3,4,5])items.push({title:tText('notes:columnsCount',{count}),aliases:['columns','layout',String(count)],group:tText('notes:columns'),onItemClick:()=>wrapColumns(editor,[editor.getTextCursorPosition().block],count)});
    items.push({
      title: tText("notes:note"),
      subtext: tText("notes:highlightTheImportantThought"),
      aliases: ["callout", tText("notes:note2")],
      group: tText("notes:mainBlocks"),
      onItemClick: () =>
        editor.updateBlock(editor.getTextCursorPosition().block, {
          type: "callout",
        }),
    });
    for(const [variant,kind] of Object.entries(calloutKinds))items.push({title:kind.title,aliases:kind.aliases,group:tText("notes:notifications"),icon:calloutIcon(variant),onItemClick:()=>editor.updateBlock(editor.getTextCursorPosition().block,{type:"callout",props:{variant}})});
    items.push({title:"Mermaid Diagram",subtext:tText("notes:diagramFromTextDescription"),aliases:["mermaid","diagram",tText("notes:diagram"),tText("notes:scheme")],group:tText("notes:media"),onItemClick:()=>editor.updateBlock(editor.getTextCursorPosition().block,{type:"codeBlock",props:{language:"mermaid"},content:`flowchart LR\n  A[${tText('notes:diagramStart')}] --> B[${tText('notes:diagramResult')}]`})});
    if (options.createSubpage)
      items.push({
        title: tText("notes:subpage"),
        subtext: tText("notes:createAPageWithinThisOne"),
        aliases: ["page", tText("notes:page"), "subpage"],
        group: tText("notes:pages"),
        onItemClick: () =>
          options.createSubpage(editor.getTextCursorPosition().block.id),
      });
    if(options.createDatabase)items.push({title:tText("notes:noteBase"),subtext:tText("notes:tableBoardCalendarGalleryAndScale"),aliases:['database',tText("notes:base")],group:tText("notes:pages"),onItemClick:()=>options.createDatabase(editor.getTextCursorPosition().block.id)});
    return filterSuggestionItems(items, query);
  };
  const render = () => root.render(
    h(
      BlockNoteView,
      {
        editor,
        theme: document.documentElement.dataset.scheme || "light",
        editable: !options.readOnly,
        slashMenu: false,
        sideMenu: false,
        formattingToolbar:false,
        onChange: () => options.onChange?.(editor),
      },
      !options.readOnly &&
        h(SuggestionMenuController, {
          triggerCharacter: "/",
          getItems: slashItems,
        }),
      !options.readOnly && h(SideMenuController, {sideMenu: blockSideMenu,floatingUIOptions:{useFloatingOptions:{middleware:[blockSideMenuPosition]}}}),
      !options.readOnly && h(FormattingToolbarController,{formattingToolbar:TextFormattingToolbar}),
    ),
  );
  render();
  const clearToggleDrop=bindEmptyToggleDrop(host,editor);
  const clearColumnDrop=bindColumnDrop(host,editor);
  const clearSpoilers=bindSpoilers(host);
  window.addEventListener("drg-appearance", render);
  // A semantic name for browser tools and assistive technologies.
  const observer = new MutationObserver(() => {
    const editable = host.querySelector("[contenteditable=true]");
    if (editable) {
      editable.setAttribute("aria-label", tText("notes:pageContent"));
      editable.setAttribute("role", "textbox");
      editable.setAttribute("aria-multiline", "true");
    }
  });
  observer.observe(host, { childList: true, subtree: true });
  const insertFiles=async event=>{
    const transfer=event.dataTransfer||event.clipboardData;
    if(!transfer?.files?.length)return;
    event.preventDefault();event.stopPropagation();
    if(!options.uploadFile||!editor.isEditable)return;
    let anchor=event.target.closest?.('.bn-block-outer[data-id]')?.dataset.id||editor.getTextCursorPosition().block.id;
    for(const file of transfer.files){
      if(!host.isConnected||!editor.isEditable)return;
      const previous=anchor;
      const type=/^image\/(png|jpeg|webp|gif)$/.test(file.type)?'image':/^video\/(mp4|webm)$/.test(file.type)?'video':/^audio\/(mpeg|ogg|wav)$/.test(file.type)?'audio':'file';
      const placeholder=editor.insertBlocks([{type,props:{name:file.name}}],anchor,'after')[0];anchor=placeholder.id;
      try{const url=await options.uploadFile(file);if(host.isConnected&&editor.getBlock(placeholder.id))editor.updateBlock(placeholder.id,{props:{url}});}
      catch(error){if(host.isConnected){if(editor.getBlock(placeholder.id))editor.removeBlocks([placeholder]);anchor=editor.getBlock(previous)?previous:editor.document.at(-1).id;}options.toast?.(error.message||tText("notes:failedToLoadFile"));}
    }
  };
  host.addEventListener('drop',insertFiles,true);host.addEventListener('paste',insertFiles,true);
  const dragOver=event=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();event.dataTransfer.dropEffect=options.readOnly?'none':'copy';}};
  host.addEventListener('dragover',dragOver);
  return {
    editor,
    normalizedOnLoad:normalized.changed,
    document: () => editor.document,
    markdown: () => spoilerMarkdown(equationTextProjection(editor.document),blocks=>blocksMarkdown(editor,blocks)),
    replaceMarkdown(text) {
      const blocks = parseInlineEquations(parseInlineSpoilers(normalizeOutlineCallouts(parseImportedMarkdown(editor,text)).blocks));
      editor.replaceBlocks(
        editor.document,
        blocks.length ? blocks : [{ type: "paragraph", content: "" }],
      );
    },
    destroy() {
      stopNormalize();
      closeEquationDialogs(editor);
      clearToggleDrop();
      clearColumnDrop();
      clearSpoilers();
      previewController.abort();
      window.removeEventListener("drg-appearance", render);
      observer.disconnect();
      host.removeEventListener('drop',insertFiles,true);host.removeEventListener('paste',insertFiles,true);host.removeEventListener('dragover',dragOver);
      root.unmount();
      contexts.delete(editor);
    },
  };
}

export function seedCollaborativeDoc(page){
  const editor=BlockNoteEditor.create({schema,dictionary:blockNoteLocales[language()]||blockNoteLocales.en,initialContent:page.blocks?.length?normalizeOutlineCallouts(page.blocks).blocks:undefined});
  if(!page.blocks){const blocks=parseInlineEquations(parseInlineSpoilers(normalizeOutlineCallouts(parseImportedMarkdown(editor,page.body||'')).blocks));if(blocks.length)editor.replaceBlocks(editor.document,blocks);}
  const doc=blocksToYDoc(editor,editor.document);
  doc.getMap('meta').set('title',page.title||tText("notes:untitled"));
  editor._tiptapEditor.destroy();
  return doc;
}

export function pageBlocks(page,origin=location.origin){
  const editor=BlockNoteEditor.create({schema,dictionary:blockNoteLocales[language()]||blockNoteLocales.en,initialContent:page.blocks?.length?normalizeOutlineCallouts(page.blocks).blocks:undefined});
  if(!page.blocks){const blocks=parseInlineEquations(parseInlineSpoilers(normalizeOutlineCallouts(parseImportedMarkdown(editor,page.body||'',origin)).blocks));if(blocks.length)editor.replaceBlocks(editor.document,blocks);}
  const document=structuredClone(editor.document);editor._tiptapEditor.destroy();return document;
}

export {pageLinkIds,subpagePlaceholder} from './block-utils.js';
