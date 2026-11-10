import {tText,tHtml,locale} from "../../core/i18n.js";
import {watchNoteCatalog} from './catalog-refresh.js';
import {notionImportDialog} from '../portability/notion.js';
import {mountNotionSource} from '../portability/notion-source.js';
import {pageLayout} from './page-layout.js';
import {statusBadges} from './status-badges.js';
import {trashPopover} from './trash.js';
import {deletionScreen} from '../social/lifecycle.js';
import {spaceTemplate} from '../../core/space-templates.js';
import {mobileApp} from '../../core/mobile.js';
import {pageIcon,pageCover,coverAttribution} from '../../core/page-assets.js';
import {iconPicker,coverPicker} from './decoration.js';
import {csrfCookie} from "../../core/auth.js";
import {saveNoteRecovery,loadNoteRecovery} from '../../core/note-recovery.js';
import { $, esc, date } from "../../core/ui.js";
import { collaborativePage } from "./collaboration.js";
import {mountHistory} from "./history.js";
import { loadEditor,mountBlockEditor,pageBlocks,pageLinkIds,subpagePlaceholder } from "./editor-runtime.js";
import { mountDatabase, recordForm, display } from './database.js';
import { mountDiscussions, permissionsDialog, mentionsDialog } from './discussions.js';
import { templates } from "./templates.js";
import "./workspace.css";
import {personAvatar,personName} from '../../core/people.js';
import {readSidebar,saveSidebar,customizeSidebar,sidebarSections} from './sidebar.js';
import { pageWordCount, pageActions, showPageMenu, pageMenuIcon, readPageView, savePageView, transferPageView, applyPageView } from "./page-menu.js";
import "./page-menu.css";
import {mountVault,moveIntoVault,unlockVaultNotes,subscribeVaultNotes,vaultNotesActivity} from '../security/vault.js';
import {FederatedNotes} from '../federation/notes.js';
let active;
// Keep each account's tree open only for this loaded app, across view remounts.
// A reload creates a fresh module and starts every tree collapsed.
const expandedPages = new Map();
export const notesController = {
  destroy() {
    active?.destroy();
    active = null;
  },
  async newPage() {
    const workspace=active;await workspace?.ready;
    if(workspace?.mounted&&!workspace.disposed&&active===workspace)return workspace.newPage();
  },
  flush() {
    return active?.flush() ?? Promise.resolve(true);
  },
};
export function mountNotes(host, ctx) {
  active?.destroy();
  active = new NotesWorkspace(host, ctx);
  active.ready=active.mount();
}
function hasContent(payload){
  if(payload.title!==tText("notes:untitled"))return true;
  if(payload.blocks===null)return !!payload.body.trim();
  const content=value=>typeof value==='string'?!!value.trim():Array.isArray(value)?value.some(content):value&&typeof value==='object'?Object.entries(value).some(([key,v])=>!['styles','type'].includes(key)&&content(v)):false;
  const blocks=items=>(items||[]).some(b=>['table','divider','pageLink'].includes(b.type)||(['image','video','audio','file'].includes(b.type)&&b.props?.url)||content(b.content)||blocks(b.children));
  return blocks(payload.blocks);
}
class NotesWorkspace {
  constructor(host, ctx) {
    this.host = host;
    this.ctx = ctx;
    this.mode = "blocks";
    this.sequence = 0;
    const account=ctx.state.me.id;
    if(!expandedPages.has(account))expandedPages.set(account,new Set());
    this.expanded=expandedPages.get(account);
    const selected=ctx.state.notes.find(n=>n.id===ctx.state.selected);
    if(selected)ctx.state.notesSpace=selected.space_id||'private';
    ctx.state.notesSpace ||= 'private';
    if(ctx.state.notesSpace!=='private'&&!(ctx.state.spaces||[]).some(s=>s.id===ctx.state.notesSpace))ctx.state.notesSpace='private';
    this.sidebarConfig=readSidebar(account);
    this.disposed = false;
    this.cleanups = [];
    this.secretExpanded=new Set();
    this.secretItems=null;
    if(ctx.state.notesNavigation?.account!==account)ctx.state.notesNavigation={account,back:[]};
    this.navigation=ctx.state.notesNavigation;
  }
  get state() {
    return this.ctx.state;
  }
  async mount() {
    this.mounted=false;
    this.host.innerHTML = `<div class="notes-shell"><div class="notes-navigation"><h1 class="mobile-notes-title">${tHtml("notes:notes")}</h1><button type="button" class="notes-main-menu" id="notes-main-menu" title="${mobileApp()?tText("notes:toNotesList"):tText("notes:mainMenu")}" aria-label="${mobileApp()?tText("notes:toNotesList"):tText("notes:mainMenu")}">${pageMenuIcon('back')}<span class="notes-main-menu-label">${mobileApp()?tText("notes:notes"):tText("notes:mainMenu")}</span></button><button class="icon-btn" id="toggle-pages-sidebar" aria-label="${tHtml("notes:collapseNoteMenu")}" aria-expanded="true" aria-controls="pages-sidebar" title="${tHtml("notes:collapseNoteMenu")}">☰</button>${this.ctx.notesInboxButton||''}</div><div class="notes-workspace"><aside class="pages-sidebar" id="pages-sidebar" aria-label="${tHtml("notes:notePages")}" tabindex="-1"><div class="pages-sidebar-head"><select id="notes-space" aria-label="${tHtml("notes:noteWorkspace")}"><option value="private">${tHtml("notes:personalWorkspace")}</option>${(this.state.spaces||[]).filter(s=>s.kind==='family').map(s=>`<option value="${esc(s.id)}">${esc(spaceTemplate(s).label)} · ${esc(s.name)}</option>`).join('')}</select><button class="icon-btn" id="add-root-page" aria-label="${tHtml("notes:createPage")}" title="${tHtml("notes:createPage")}">＋</button></div><input id="search-pages" type="search" maxlength="200" placeholder="${tHtml("notes:searchPages")}" aria-label="${tHtml("notes:searchNotes")}" title="${tHtml("notes:searchByNameAndContent")}"><div id="page-tree" aria-live="polite"></div><div class="pages-sidebar-footer"><button class="text-link" id="customize-sidebar">${pageMenuIcon("settings")}<span>${tHtml("notes:customizeThePanel")}</span></button><details class="pages-tools" id="pages-tools"><summary aria-label="${tHtml("notes:pageTools")}">${pageMenuIcon("dots")}<span>${tHtml("notes:tools")}</span></summary><div class="pages-tool-menu"><button type="button" id="show-templates">${tHtml("notes:templates")}</button><button type="button" id="create-database">${tHtml("notes:newDatabase")}</button><button type="button" id="show-mentions">${tHtml("notes:mentions")}</button></div></details><button class="text-link" id="show-trash">${pageMenuIcon("trash")}<span>${tHtml("notes:trash")}</span></button></div></aside><main class="notion-page" id="notion-page"></main></div></div>`;
    const pendingControls=[...this.host.querySelectorAll('button,input,select')].filter(node=>node.id!=='notes-main-menu');
    for(const node of pendingControls)node.disabled=true;
    $('#notes-main-menu',this.host).onclick=()=>this.ctx.navigate('home');
    $('#notion-page',this.host).setAttribute('aria-busy','true');
    try{await loadEditor();}catch(error){
      if(!this.disposed){const area=$('#notion-page',this.host),message=document.createElement('p');message.setAttribute('role','alert');message.textContent=error.message;area.append(message);for(const [key,action] of [['app:tryLoadingAgain',()=>{this.ready=this.mount();}],['server:reloadPage',()=>location.reload()]]){const button=document.createElement('button');button.className='secondary';button.textContent=tText(key);button.onclick=action;area.append(button);}}
      return;
    }
    if(this.disposed)return;
    this.mounted=true;
    for(const node of pendingControls)node.disabled=false;
    $('#notion-page',this.host).removeAttribute('aria-busy');
    $('#notes-main-menu',this.host).onclick=async()=>{if(mobileApp())await this.back();else this.ctx.navigate('home');};
    $('#toggle-pages-sidebar',this.host).onclick=()=>this.sidebar($('#pages-sidebar',this.host).hidden);
    const narrow=matchMedia('(max-width:800px)'),adapt=()=>this.sidebar(mobileApp()?(!this.state.selected||this.state.notesList===true):!narrow.matches);this.narrowSidebar=narrow;
    narrow.addEventListener('change',adapt);this.cleanups.push(()=>narrow.removeEventListener('change',adapt));adapt();
    const tools=$('#pages-tools',this.host),closeTools=e=>{
      if(!tools.contains(e.target))tools.open=false;
      if(!mobileApp()&&narrow.matches&&!$('#pages-sidebar',this.host).contains(e.target)&&!$('#toggle-pages-sidebar',this.host).contains(e.target))this.sidebar(false);
    };
    document.addEventListener('pointerdown',closeTools);this.cleanups.push(()=>document.removeEventListener('pointerdown',closeTools));
    tools.onkeydown=e=>{if(e.key==='Escape'){e.stopPropagation();tools.open=false;$('summary',tools).focus();}};
    $('#pages-sidebar',this.host).onkeydown=e=>{if(narrow.matches&&e.key==='Escape'){this.sidebar(false);$('#toggle-pages-sidebar',this.host).focus();}};
    const tool=action=>()=>{
      tools.open=false;
      if(narrow.matches){this.sidebar(false);$('#toggle-pages-sidebar',this.host).focus();}
      else $('summary',tools).focus();
      return action();
    };
    const online=()=>{if(this.state.dirty)this.flush();};
    const leave=()=>this.releaseLease();window.addEventListener('pagehide',leave);this.cleanups.push(()=>window.removeEventListener('pagehide',leave));
    const linked=()=>this.page&&this.revealLinkedBlock();window.addEventListener('hashchange',linked);this.cleanups.push(()=>window.removeEventListener('hashchange',linked));
    window.addEventListener('online',online);this.cleanups.push(()=>window.removeEventListener('online',online));
    $("#add-root-page", this.host).onclick = () => this.newPage();
    $('#create-database',this.host).onclick=tool(()=>this.newDatabase());
    $('#show-mentions',this.host).onclick=tool(()=>mentionsDialog(this.ctx));
    $("#show-trash", this.host).onclick = tool(() => this.trash());
    $("#show-templates", this.host).onclick = tool(() => this.templatePicker());
    $("#search-pages", this.host).oninput = () => this.search();
    $('#notes-space',this.host).value=this.workspaceScope();
    $('#notes-space',this.host).onchange=async e=>{
      const next=e.target.value;
      if(!(await this.flush())){e.target.value=this.workspaceScope();return;}
      await this.releaseLease(true);
      this.discardDraft();this.state.notesSpace=next;this.state.selected=null;this.page=null;this.database?.destroy();this.discussions?.destroy();clearInterval(this.readTimer);this.live?.destroy();this.live=null;this.handle?.destroy();this.handle=null;
      history.replaceState({},'', '/');
      $('#search-pages',this.host).value='';this.search();this.tree();this.empty();
    };
    $('#customize-sidebar',this.host).onclick=()=>this.customizeSidebar();
    this.cleanups.push(subscribeVaultNotes(this.ctx,items=>{this.secretItems=items;if(!items)this.secretExpanded.clear();if(!this.disposed)this.tree();}));
    this.ctx.api('/secret-vault').then(status=>{if(!this.disposed){this.vaultEnabled=status.enabled;this.tree();}}).catch(error=>this.ctx.toast(error.message));
    this.tree();
    this.remote=new FederatedNotes(this,this.ctx);this.remote.mount();
    const sharedChanged=async event=>{if(this.page?.id!==event.detail?.noteId)return;if(await this.flush())await this.open(this.page.id,false);};document.addEventListener('drg-federation-grants-change',sharedChanged);this.cleanups.push(()=>document.removeEventListener('drg-federation-grants-change',sharedChanged));
    const page = this.state.notes.find((n) => n.id === this.state.selected || n.navigation_aliases?.includes(this.state.selected));
    if(page&&location.pathname==='/page/'+page.id+'/history'){this.page={...page};this.history(false);}
    else if (page&&(!mobileApp()||!this.state.notesList)) this.open(page.id, false);
    else this.empty();
    this.cleanups.push(watchNoteCatalog(this.ctx,(notes,spaces)=>this.applyCatalog(notes,spaces)));
  }
  applyCatalog(notes,spaces){
    const previous=this.state.notes;
    const merged=notes.map(note=>{const old=previous.find(n=>n.id===note.id);return old&&old.version>note.version&&JSON.stringify(old.navigation_aliases||[])===JSON.stringify(note.navigation_aliases||[])?old:{...old,...note};});
    for(const draft of previous.filter(n=>n.isDraft&&!merged.some(item=>item.id===n.id)))merged.push(draft);
    if(JSON.stringify(merged)===JSON.stringify(previous)&&JSON.stringify(spaces)===JSON.stringify(this.state.spaces))return;
    this.state.notes=merged;this.state.spaces=spaces;
    const latest=merged.find(n=>n.id===this.page?.id || n.navigation_aliases?.includes(this.page?.id));
    if(latest){this.page.participants=latest.participants;this.page.external=latest.external;}
    if(latest?.notion_sync&&this.page?.notion_sync&&this.state.dirty&&JSON.stringify(latest.notion_sync)!==JSON.stringify(this.page.notion_sync)){
      this.page.notion_sync=latest.notion_sync;
      mountNotionSource($('#notion-page',this.host),this.page,this.ctx,{openPage:id=>this.open(id,false),refresh:()=>this.refresh(),beforeCopy:()=>this.flush()});
      if(latest.notion_sync.read_only&&!this.recoveryBlocked)this.retainNotionDraft().catch(error=>this.ctx.toast(error.message));
    }
    if(latest&&this.page?.notion_sync&&!this.state.dirty&&!this.notionRefresh&&(latest.version!==this.page.version||JSON.stringify(latest.notion_sync)!==JSON.stringify(this.page.notion_sync))){
      const id=this.page.id;this.notionRefresh=this.open(id,false).catch(error=>this.ctx.toast(error.message)).finally(()=>{this.notionRefresh=null;});
    }
    const sidebar=$('#pages-sidebar',this.host),scroll=sidebar.scrollTop,focused=document.activeElement;
    const focusId=focused?.dataset.openPage||focused?.dataset.expand;
    const focusType=focused?.dataset.openPage?'data-open-page':'data-expand';
    this.tree();sidebar.scrollTop=scroll;
    if(focusId)[...this.host.querySelectorAll('['+focusType+']')].find(el=>el.getAttribute(focusType)===focusId)?.focus({preventScroll:true});
  }
  participantAvatars(page){
    const members=page.participants||(this.state.spaces||[]).find(s=>s.id===page.space_id)?.members||[];
    return `<button type="button" class="page-participants" id="page-participants" aria-label="${tHtml("notes:participantsAndAccessRights")}" title="${esc(members.map(personName).join(', '))}">${members.slice(0,4).map(m=>personAvatar(m)).join('')}${members.length>4?`<span class="participants-more">+${members.length-4}</span>`:''}</button>`;
  }
  workspaceScope(){
    return (this.state.spaces||[]).some(s=>s.kind==='family'&&s.id===this.state.notesSpace)?this.state.notesSpace:'private';
  }
  customizeSidebar(){
    this.customizing=true;
    const input=$('#search-pages',this.host);input.hidden=true;
    customizeSidebar($('#page-tree',this.host),this.sidebarConfig,{
      secret:this.vaultEnabled&&this.workspaceScope()==='private',
      change:value=>{this.sidebarConfig=value;if(!saveSidebar(this.state.me.id,value))this.ctx.toast(tText("notes:theBrowserDidNotAllowSavingThePanel"));},
      done:()=>{this.customizing=false;input.hidden=false;this.tree();$('#customize-sidebar',this.host).focus();},
    });
  }
  async createInScope(scope){
    if(scope!=='shared')return this.newPage(null,templates[0],scope);
    const friends=(this.state.spaces||[]).filter(s=>s.kind==='friends');
    const el=this.ctx.modal(`<h2>${tHtml("notes:mainPage")}</h2><p class="hint">${tHtml("notes:youWillBeTheOwnerOfThePage")}</p>${friends.length?friends.map(s=>`<button class="sidebar-share-choice" data-create-shared="${esc(s.id)}">${personAvatar(s.members.find(m=>m.id!==this.state.me.id))}<span>${esc(s.name)}</span></button>`).join(''):`<p>${tHtml("notes:firstAddTheFriendInTheCommunicationSettings")}</p>`}`);
    el.querySelectorAll('[data-create-shared]').forEach(b=>b.onclick=()=>{$('.modal-close',el).click();this.newPage(null,templates[0],b.dataset.createShared);});
  }
  sidebar(open) {
    if(mobileApp()){
      this.state.notesList=open;
      if(open){this.navigation.back=[];history.replaceState({},'', '/');}
    }
    $('.notes-shell',this.host).classList.toggle('mobile-notes-list',open);
    const sidebar=$('#pages-sidebar',this.host),button=$('#toggle-pages-sidebar',this.host);
    if(!open&&sidebar.contains(document.activeElement))button.focus();
    sidebar.hidden=!open;$('.notes-workspace',this.host).classList.toggle('pages-collapsed',!open);
    $('.notes-shell',this.host).classList.toggle('pages-collapsed',!open);
    const label=open?tText("notes:collapseNoteMenu"):tText("notes:showNotesMenu");
    button.setAttribute('aria-expanded',String(open));button.setAttribute('aria-label',label);button.title=label;
  }
  async back(){
    if(this.goingBack)return;
    this.goingBack=true;
    try{
      if(!await this.flush())return;
      while(this.navigation.back.length){
        const id=this.navigation.back.pop();
        if(id!==this.page?.id&&this.state.notes.some(note=>note.id===id&&!note.deleted))return await this.open(id,false);
      }
      const parent=this.page?.parent_id;
      if(parent&&this.state.notes.some(note=>note.id===parent&&!note.deleted))return await this.open(parent,false);
      if(this.page?.kind==='database'){
        this.database?.destroy();this.database=null;this.discussions?.destroy();clearInterval(this.readTimer);
        this.page=null;this.state.selected=null;this.empty();this.tree();
      }
      this.sidebar(true);$('#pages-sidebar',this.host).focus();
    }finally{this.goingBack=false;}
  }
  destroy() {
    this.remote?.destroy();
    this.vault?.destroy();this.vault=null;
    this.closeTrash?.(false);
    this.historyView?.destroy();
    this.closeContext?.();
    this.closePageMenu?.(false);
    this.linkObserver?.disconnect();
    clearTimeout(this.linkTimer);
    this.discardDraft();
    this.releaseLease();
    clearTimeout(this.lockTimer);
    this.disposed = true;
    clearTimeout(this.timer);clearTimeout(this.maxSaveTimer);
    clearTimeout(this.searchTimer);
    this.database?.destroy();this.discussions?.destroy();clearInterval(this.readTimer);
    this.live?.destroy();
    this.handle?.destroy();
    this.cleanups.forEach((fn) => fn());
  }
  empty() {
    if(this.remote?.active)this.remote.close();
    this.vault?.destroy();this.vault=null;
    const space=(this.state.spaces||[]).find(s=>s.id===this.state.notesSpace);
    if(space?.deletion){$('#add-root-page',this.host).disabled=true;deletionScreen($('#notion-page',this.host),space,this.ctx,async()=>{this.state.spaces=(await this.ctx.api('/social')).spaces;this.state.notes=await this.ctx.api('/notes');this.mount();});return;}
    $('#add-root-page',this.host).disabled=false;
    if(mobileApp())this.sidebar(true);
    $("#notion-page", this.host).innerHTML =
      `<div class="page-welcome"><span class="welcome-icon">✎</span><h1>${tHtml("notes:spaceForYourThoughts")}</h1><p class="muted">${tHtml("notes:eachNoteIsAPageEachParagraphIs")}<br>${tHtml("notes:startWithABlankPageOrChooseA")}</p><button class="primary" id="create-first-page">${tHtml("notes:createPage2")}</button><div class="template-grid">${templates
        .slice(1)
        .map(
          (t) =>
            `<button data-template="${t.id}" class="template-card"><span>${t.icon}</span><strong>${t.name}</strong></button>`,
        )
        .join("")}</div><p class="hint">${tHtml("notes:typeToAddABlockDragBlocksBy")}</p></div>`;
    $("#create-first-page", this.host).onclick = () => this.newPage();
    this.host.querySelectorAll("[data-template]").forEach(
      (b) =>
        (b.onclick = () =>
          this.newPage(
            null,
            templates.find((t) => t.id === b.dataset.template),
          )),
    );
  }
  tree() {
    const breadcrumbs=$('.breadcrumbs',this.host);
    if(this.page&&breadcrumbs){
      breadcrumbs.innerHTML=this.breadcrumbs();
      breadcrumbs.querySelectorAll('[data-crumb]').forEach(button=>button.onclick=()=>this.open(button.dataset.crumb));
    }
    const search = $("#search-pages", this.host).value.trim();
    if(this.customizing)return;
    const scope=this.workspaceScope(),friends=new Set((this.state.spaces||[]).filter(s=>s.kind==='friends').map(s=>s.id));
    const notes=this.state.notes.filter(n=>scope==='private'?(!n.space_id||friends.has(n.space_id)||n.external):n.space_id===scope);
    const linked=n=>!!n?.notion_sync&&n.notion_sync.status!=='detached';
    const row = (n, depth = 0) => {
      const children = notes.filter((x) => x.parent_id === n.id);
      const expanded = this.expanded.has(n.id);
      return `<div class="page-tree-row ${this.page?.id === n.id ? "selected" : ""}" data-tree-row="${esc(n.id)}" draggable="true" style="--depth:${depth}"><button class="tree-toggle" data-expand="${esc(n.id)}" aria-label="${tHtml("notes:subpages", {value0:(esc(n.title))})}" ${children.length ? `aria-expanded="${expanded}"` : "disabled"}>${children.length ? (expanded ? "▾" : "▸") : "·"}</button><button class="tree-open" data-open-page="${esc(n.id)}"><span class="tree-page-icon">${pageIcon(n)}</span><span class="tree-page-title">${esc(n.title)}</span></button>${n.space_id&&n.owner?`<span class="tree-page-owner" title="${tHtml("notes:owner2", {value0:(esc(personName(n.owner)))})}">${personAvatar(n.owner)}</span>`:""}<button class="tree-add" data-add-child="${esc(n.id)}" aria-label="${tHtml("notes:createASubpage", {value0:(esc(n.title))})}">＋</button></div>${expanded ? children.map((x) => row(x, depth + 1)).join("") : ""}`;
    };
    const simple=n=>`<button class="favorite-page" data-open-page="${esc(n.id)}"><span class="tree-page-icon">${pageIcon(n)}</span><span class="tree-page-title">${esc(n.title)}</span></button>`;
    const section=(key,items,sectionScope)=>`<section class="notes-tree-section" data-sidebar-section="${key}"><div class="tree-section-heading"><span class="tree-section-label">${scope!=='private'&&key==='private'?tText("notes:pages"):sidebarSections[key]}</span>${['private','shared'].includes(key)?`<button class="icon-btn" data-create-scope="${esc(sectionScope)}" aria-label="${tHtml("notes:addPage", {value0:(key==='shared'?tText("notes:shared"):scope==='private'?tText("notes:personal"):tText("notes:family"))})}">＋</button>`:''}</div>${items}</section>`;
    const roots=items=>items.filter(n=>!n.parent_id||!notes.some(parent=>parent.id===n.parent_id)).map(n=>row(n)).join('');
    const sections={
      favorites:()=>{const items=notes.filter(n=>n.favorite),secret=scope==='private'&&this.vaultEnabled&&!this.sidebarConfig.hidden.includes('secret')?(this.secretItems||[]).filter(n=>n.favorite):[];return items.length||secret.length?section('favorites',items.map(simple).join('')+secret.map(n=>`<button class="favorite-page" data-secret-page="${esc(n.id)}"><span class="tree-page-icon">${esc(n.icon?.startsWith('image:')?'📄':n.icon||'📄')}</span><span class="tree-page-title">${esc(n.title)}</span></button>`).join('')):'';},
      recent:()=>section('recent',[...notes].filter(n=>!n.isDraft).sort((a,b)=>b.updated-a.updated).slice(0,8).map(simple).join('')||`<p class="hint">${tHtml("notes:noPagesYet")}</p>`),
      shared:()=>scope==='private'?section('shared',roots(notes.filter(n=>n.space_id))||`<p class="hint">${tHtml("notes:commonPagesWithFriendsWillAppearHere")}</p>`,'shared'):'',
      private:()=>section('private',`<div class="tree-root-drop" data-root-scope="${esc(scope)}" data-root-position="first"></div>${roots(notes.filter(n=>scope!=='private'||!n.space_id))||`<p class="hint">${tHtml("notes:noPages")}</p>`}<div class="tree-end-drop" data-root-scope="${esc(scope)}" aria-label="${tHtml("notes:moveToTheEndOfTheList")}"></div>`,scope),
    };
    $('#page-tree',this.host).innerHTML=search
      ? (this.searching?`<p class="hint">${tHtml("notes:searchingForNotes")}</p>`:this.searchError?`<p class="hint">${esc(this.searchError)}</p>`:`<div class="tree-section-label">${tHtml("notes:found", {value0:(this.searchTotal||0)})}</div>${(this.searchResults||[]).map(n=>`<button class="note-search-result ${this.page?.id===n.id?'selected':''}" data-open-page="${esc(n.id)}"><strong>${pageIcon(n)} ${esc(n.title)}</strong><span>${esc(n.excerpt||tText("notes:withoutText"))}</span></button>`).join('')||`<p class="hint">${tHtml("notes:nothingFoundTryOtherWords")}</p>`}${this.searchTotal>100?`<p class="hint">${tHtml("notes:showingTheFirst100ResultsRefineYourQuery")}</p>`:''}`)
      : this.sidebarConfig.order.filter(key=>!this.sidebarConfig.hidden.includes(key)).map(key=>sections[key]?.()||'').join('');
    if(this.vaultEnabled&&scope==='private'&&!this.sidebarConfig.hidden.includes('secret')){
      const secretRow=(item,depth=0)=>{const children=(this.secretItems||[]).filter(x=>x.parent_id===item.id),expanded=this.secretExpanded.has(item.id);return `<div class="page-tree-row ${this.vault?.note?.id===item.id?'selected':''}" data-secret-row="${esc(item.id)}" style="--depth:${depth}"><button class="tree-toggle" data-secret-expand="${esc(item.id)}" aria-label="${tHtml("notes:subpages", {value0:(esc(item.title))})}" ${children.length?`aria-expanded="${expanded}"`:'disabled'}>${children.length?(expanded?'▾':'▸'):'·'}</button><button class="tree-open" data-secret-page="${esc(item.id)}"><span class="tree-page-icon">${esc(item.kind==='database'?'▦':item.icon?.startsWith('image:')?'📄':item.icon||'📄')}</span><span class="tree-page-title">${esc(item.title)}</span></button><button class="tree-add" data-secret-add="${esc(item.id)}" aria-label="${tHtml("notes:createASecretSubpage", {value0:(esc(item.title))})}">＋</button></div>${expanded?children.map(x=>secretRow(x,depth+1)).join(''):''}`;};
      const content=this.secretItems?(this.secretItems.filter(x=>!x.parent_id).map(x=>secretRow(x)).join('')||`<p class="hint">${tHtml("notes:noSecretPages")}</p>`):'';
      const secretHTML=`<section class="notes-tree-section" data-sidebar-section="secret"><div class="tree-section-heading">${this.secretItems?`<span class="tree-section-label">${tHtml("notes:private")}</span>`:`<button class="tree-section-label secret-section-title" data-secret-notes>${tHtml("notes:private")}</button>`}<button class="icon-btn" data-secret-add="" aria-label="${tHtml("notes:createASecretPage")}">＋</button></div>${content}</section>`;
      const next=this.sidebarConfig.order.slice(this.sidebarConfig.order.indexOf('secret')+1).map(key=>$('[data-sidebar-section='+key+']',this.host)).find(Boolean);
      if(next)next.insertAdjacentHTML('beforebegin',secretHTML);else $('#page-tree',this.host).insertAdjacentHTML('beforeend',secretHTML);
      const secretSection=$('[data-sidebar-section=secret]',this.host);for(const type of ['pointerdown','keydown','wheel'])secretSection.addEventListener(type,event=>{if(event.isTrusted)vaultNotesActivity(this.ctx);},{passive:true});
      $('[data-secret-notes]',this.host)?.addEventListener('click',async()=>{try{if(await unlockVaultNotes(this.ctx)&&this.vault?.locked){this.empty();this.tree();}}catch(error){this.ctx.toast(error.message);}});
      this.host.querySelectorAll('[data-secret-expand]').forEach(b=>b.onclick=()=>{const id=b.dataset.secretExpand;this.secretExpanded.has(id)?this.secretExpanded.delete(id):this.secretExpanded.add(id);this.tree();});
      this.host.querySelectorAll('[data-secret-page]').forEach(b=>b.onclick=async()=>{if(await this.openVault()){await this.vault.openNote(b.dataset.secretPage);this.tree();}});
      this.host.querySelectorAll('[data-secret-add]').forEach(b=>b.onclick=async()=>{if(await this.openVault()){const parent=b.dataset.secretAdd||null;if(parent)this.secretExpanded.add(parent);const created=await this.vault.newPage(parent);if(!created&&!this.vault.note)this.empty();else await this.vault.refreshTree();this.tree();}});
    }
    this.host.querySelectorAll('[data-create-scope]').forEach(b=>b.onclick=()=>this.createInScope(b.dataset.createScope));
    this.host
      .querySelectorAll("[data-open-page]")
      .forEach((b) => (b.onclick = () => this.open(b.dataset.openPage)));
    this.host.querySelectorAll('[data-open-page]').forEach(button=>button.oncontextmenu=e=>{e.preventDefault();this.contextMenu(button.dataset.openPage,e.clientX,e.clientY);});
    this.host.querySelectorAll("[data-expand]").forEach(
      (b) =>
        (b.onclick = () => {
          this.expanded.has(b.dataset.expand)
            ? this.expanded.delete(b.dataset.expand)
            : this.expanded.add(b.dataset.expand);
          this.tree();
        }),
    );
    this.host
      .querySelectorAll("[data-add-child]")
      .forEach((b) => (b.onclick = () => this.newPage(b.dataset.addChild)));
    this.host.querySelectorAll("[data-tree-row]").forEach((row) => {
      const item=notes.find(n=>n.id===row.dataset.treeRow);
      if(linked(item)&&!item.notion_sync.can_reorder)row.draggable=false;
      row.ondragstart = (e) => {
        if(linked(item)&&!item.notion_sync.can_reorder){e.preventDefault();return;}
        this.draggedPageId=row.dataset.treeRow;
        e.dataTransfer.setData("application/drg-page", row.dataset.treeRow);
        e.dataTransfer.effectAllowed = "move";
      };
      row.ondragover = (e) => {
        if (e.dataTransfer.types.includes("application/drg-page")) {
          const source=notes.find(n=>n.id===this.draggedPageId);
          const localOrder=linked(source)||linked(item);
          if(localOrder&&source&&source.parent_id!==item.parent_id){clear();return;}
          e.preventDefault();
          const rect=row.getBoundingClientRect(),ratio=(e.clientY-rect.top)/rect.height;
          const zone=localOrder?(ratio<.5?'before':'after'):ratio<.3?'before':ratio>.7?'after':'inside';
          row.dataset.dropZone=zone;
          row.classList.toggle('drop-target',zone==='inside');
        }
      };
      const clear=()=>{row.classList.remove('drop-target');delete row.dataset.dropZone;};
      row.ondragleave = e => {if(!row.contains(e.relatedTarget))clear();};
      row.ondragend = () => {this.draggedPageId=null;this.host.querySelectorAll('[data-drop-zone],.drop-root').forEach(el=>{delete el.dataset.dropZone;el.classList.remove('drop-target','drop-root');});};
      row.ondrop = async (e) => {
        e.preventDefault();
        e.stopPropagation();
        let zone=row.dataset.dropZone;clear();
        const id = e.dataTransfer.getData("application/drg-page");
        this.draggedPageId=null;
        if (!id||id===row.dataset.treeRow)return;
        const target=notes.find(n=>n.id===row.dataset.treeRow);
        const source=notes.find(n=>n.id===id);
        if(!target||!source)return;
        if(linked(source)||linked(target)){
          if(source.parent_id!==target.parent_id)return;
          const rect=row.getBoundingClientRect();zone=e.clientY<rect.top+rect.height/2?'before':'after';
        }
        if((notes.find(n=>n.id===id)?.space_id||null)!==(target.space_id||null)){this.ctx.toast(tText("notes:pagesCanOnlyBeMovedWithinYourWorkspace"));return;}
        const siblings=notes.filter(n=>n.parent_id===target.parent_id&&n.space_id===target.space_id&&n.id!==id);
        const before=zone==='before'?target.id:zone==='after'?siblings[siblings.findIndex(n=>n.id===target.id)+1]?.id??null:null;
        await this.move(id,zone==='inside'?target.id:target.parent_id,before);
      };
    });
    for(const root of this.host.querySelectorAll('[data-root-scope]')){
      root.ondragover = (e) => {const source=notes.find(n=>n.id===this.draggedPageId);if(linked(source)&&source.parent_id)return;if(e.dataTransfer.types.includes('application/drg-page')){e.preventDefault();root.classList.add('drop-root');}};
      root.ondragleave=()=>root.classList.remove('drop-root');
      root.ondrop = (e) => {
        e.preventDefault();
        root.classList.remove('drop-root');
        const id = e.dataTransfer.getData("application/drg-page");
        this.draggedPageId=null;
        const source=notes.find(n=>n.id===id);
        if(linked(source)&&source.parent_id)return;
        if(source&&(source.space_id||'private')===root.dataset.rootScope)this.move(id,null,root.dataset.rootPosition==='first'?notes.find(n=>!n.parent_id&&n.space_id===source.space_id&&n.id!==id)?.id??null:null);
      };
    }
  }
  search() {
    clearTimeout(this.searchTimer);
    const sequence=(this.searchSequence||0)+1;this.searchSequence=sequence;
    const query=$('#search-pages',this.host).value.trim();
    this.searchError=null;this.searching=!!query;this.tree();
    if(!query)return;
    this.searchTimer=setTimeout(async()=>{
      try{
        if(!(await this.flush()))throw new Error(tText("notes:firstSaveThePageChanges"));
        if(this.disposed||sequence!==this.searchSequence)return;
        const result=await this.ctx.api('/notes/search?q='+encodeURIComponent(query)+'&space_id='+encodeURIComponent(this.workspaceScope()==='private'?'personal':this.workspaceScope()));
        if(this.disposed||sequence!==this.searchSequence)return;
        this.searchResults=result.items;this.searchTotal=result.total;
      }catch(error){if(this.disposed||sequence!==this.searchSequence)return;this.searchError=error.message;}
      this.searching=false;this.tree();
    },220);
  }
  async refresh() {
    this.state.notes = await this.ctx.api("/notes");
    if (!this.disposed){
      const latest=this.state.notes.find(n=>n.id===this.page?.id);if(latest){this.page.participants=latest.participants;this.page.external=latest.external;const button=$('#page-participants',this.host);if(button){const holder=document.createElement('div');holder.innerHTML=this.participantAvatars(this.page);button.innerHTML=holder.firstElementChild.innerHTML;button.title=holder.firstElementChild.title;}}
      this.tree();
    }
  }
  async newDatabase(parent=null,blockId=null){
    if(this.vault&&!this.vault.disposed){if(await this.openVault())await this.vault.newDatabase(parent,blockId);return;}
    blockId=subpagePlaceholder(this.handle?.editor,blockId);
    const previous=this.page?.id;
    if(parent===previous&&this.page?.isDraft){if(!(await this.persistDraft()))return;parent=this.page.id;}
    if(!(await this.flush()))return;
    const el=this.ctx.modal(`<h2>${tHtml("notes:newNotesBase")}</h2><form id="create-database-form"><label for="database-title">${tHtml("notes:baseName")}</label><input id="database-title" required maxlength="200" placeholder="${tHtml("notes:projectsBooksPlans")}"><button class="primary" type="submit">${tHtml("notes:createBase")}</button><p class="error" role="alert"></p></form>`);
    $('#create-database-form',el).onsubmit=async e=>{e.preventDefault();try{const d=await this.ctx.api('/databases','POST',{title:$('#database-title',el).value.trim(),parent_id:parent,space_id:parent?(this.state.notes.find(n=>n.id===parent)?.space_id||null):this.workspaceScope()==='private'?null:this.workspaceScope()});$('.modal-close',el).click();await this.refresh();if(blockId&&this.handle){this.handle.editor.updateBlock(blockId,{type:'pageLink',props:{pageId:d.id,title:$('#database-title',el).value.trim()}});this.changed();if(!(await this.flush()))return;}await this.open(d.id,false);}catch(error){$('.error',el).textContent=error.message;}};
  }
  async createChild(parent,template=templates[0],blockId=null){
    if(this.subpageCreating)return null;this.subpageCreating=true;
    try{
      const latest=await this.ctx.api('/notes/'+parent);
      const result=await this.ctx.api('/notes/'+parent+'/subpages','POST',{
        version:latest.version,title:template.id==='blank'?tText("notes:untitled"):template.name,body:template.body,
        parent_blocks:latest.blocks===null?pageBlocks(latest):null,replace_block_id:blockId,
      });
      const index=this.state.notes.findIndex(note=>note.id===parent);if(index>=0)this.state.notes[index]=result.parent;
      // The server owns creation of both records; leaving the parent must not
      // send the old editor snapshot over the newly inserted shortcut.
      if(this.page?.id===parent){Object.assign(this.page,result.parent);this.state.dirty=false;}
      return result.page;
    }finally{this.subpageCreating=false;}
  }
  async repairChildLinks(page){
    if(page.isDraft||page.notion_sync||page.kind==='database'||page.role!=='editor'||page.locked&&!(page.unlock_until>Date.now()/1000))return page;
    const children=this.state.notes.filter(note=>note.parent_id===page.id).map(note=>note.id);
    const links=pageLinkIds(page.blocks),dangling=links.some(id=>!this.state.notes.some(note=>note.id===id));
    if(!dangling&&children.every(id=>links.includes(id)))return page;
    const latest=await this.ctx.api('/notes/'+page.id);
    const missing=children.filter(id=>!pageLinkIds(latest.blocks).includes(id));if(!dangling&&!missing.length)return latest;
    const result=await this.ctx.api('/notes/'+page.id+'/subpages','POST',{version:latest.version,repair_ids:missing,parent_blocks:latest.blocks===null?pageBlocks(latest):null});
    const index=this.state.notes.findIndex(note=>note.id===page.id);if(index>=0)this.state.notes[index]=result.parent;
    return result.parent;
  }
  async syncSubpageTree(){
    if(!this.handle||this.disposed)return;
    const key=JSON.stringify(pageLinkIds(this.handle.document()));
    if(key===this.subpageLinksKey)return;
    this.subpageLinksKey=key;await this.refresh();
  }
  async newPage(parent = null, template = templates[0], scope = this.workspaceScope()) {
    if(this.vault){if(!await this.vault.flush())return;this.vault.destroy();this.vault=null;}
    if(this.transferBusy){await this.flush();return;}
    const previousTitle = $("#page-title", this.host);
    const previousDisabled = previousTitle?.disabled;
    if (previousTitle) previousTitle.disabled = true;
    try {
      if (!(await this.flush()) || this.transferBusy) {
        return;
      }
      if(parent?.startsWith('draft_')){if(!(await this.persistDraft()))return;parent=this.page.id;}
      this.discardDraft();
      const input={
        title: template.id === "blank" ? tText("notes:untitled") : template.name,
        body: template.body,
        parent_id: parent,
        space_id: parent?(this.state.notes.find(n=>n.id===parent)?.space_id||null):scope==='private'?null:scope,
      };
      const stamp=Date.now()/1000;
      const page=parent?await this.createChild(parent,template):template.id==='blank'&&!input.space_id?{...input,id:'draft_'+crypto.randomUUID(),isDraft:true,icon:'📄',cover:'',blocks:null,kind:'page',role:'editor',manage:true,version:1,favorite:0,full_width:0,locked:0,created:stamp,updated:stamp}:await this.ctx.api('/notes','POST',input);
      if(!page)return;
      if (template.icon !== "📄")
        Object.assign(
          page,
          await this.ctx.api("/notes/" + page.id, "PATCH", {
            icon: template.icon,
            version: page.version,
          }),
        );
      this.state.notes.push(page);
      if (parent) this.expanded.add(parent);
      await this.open(page.id);
      $("#page-title", this.host)?.focus();
      $("#page-title", this.host)?.select();
    } catch (e) {
      this.ctx.toast(e.message);
    } finally {
      if(previousTitle?.isConnected&&!this.transferBusy)previousTitle.disabled=previousDisabled;
    }
  }
  async open(id, flush = true) {
    id=this.state.notes.find(n=>n.navigation_aliases?.includes(id))?.id||id;
    this.closePageMenu?.(false);
    const current=this.page?.id;
    if (flush && !(await this.flush())) return;
    if(this.remote?.active)this.remote.close();
    this.vault?.destroy();this.vault=null;
    if(id===current&&current?.startsWith('draft_'))id=this.page.id;
    if(this.page?.id!==id)await this.releaseLease(true);
    if(this.page?.id!==id)this.discardDraft();
    let page = this.state.notes.find((n) => n.id === id);
    if (!page) {
      this.ctx.toast(tText("notes:pageIsUnavailable"));
      return;
    }
    try{
      // Catalog refreshes carry metadata only. Linked pages must load their
      // current document before rendering a newer source version.
      if(page.notion_sync){page=await this.ctx.api('/notes/'+id);const index=this.state.notes.findIndex(note=>note.id===id);if(index>=0)this.state.notes[index]=page;}
      page=await this.repairChildLinks(page);
    }catch(error){this.ctx.toast(error.message);}
    clearTimeout(this.timer);
    this.live?.destroy();this.live=null;
    this.handle?.destroy();this.handle=null;
    this.state.notesSpace=page.space_id||'private';$('#notes-space',this.host).value=this.workspaceScope();
    const previous=this.page?.id;
    if(flush&&previous&&previous!==id&&!this.state.notesList&&this.state.notes.some(note=>note.id===previous)){
      this.navigation.back.push(previous);if(this.navigation.back.length>100)this.navigation.back.shift();
    }
    this.page = { ...page };
    this.state.selected = id;
    this.state.notesList = false;
    this.state.dirty = false;
    this.mode = "blocks";
    this.sourceDirty = false;
    this.sequence = 0;
    const fragment=location.pathname==='/page/'+id?location.hash:'';
    history.replaceState({}, "", page.isDraft?"/":"/page/" + id + fragment);
    this.renderPage();
    this.recoveryBlocked=false;this.recoverySnapshot=null;
    if(!page.isDraft)loadNoteRecovery(this.ctx,page.id).then(snapshot=>{if(snapshot&&!this.disposed&&this.page?.id===page.id&&!this.recoveryBlocked)this.showNoteRecovery(page.id,snapshot,false,true);}).catch(error=>{if(!this.disposed&&this.page?.id===page.id)this.ctx.toast(error.message);});
    if(this.narrowSidebar.matches)this.sidebar(false);
    this.tree();
  }
  async recoverSharedConflict(error){
    if(error.status!==409||this.live||!this.page||this.page.isDraft)return false;
    const id=this.page.id;let latest;try{latest=await this.ctx.api('/notes/'+id);}catch{return false;}
    if(this.disposed||this.page?.id!==id||!latest.federation_shared)return false;
    clearTimeout(this.timer);clearTimeout(this.maxSaveTimer);this.recoveryBlocked=true;
    const title=$('#page-title',this.host).value.trim()||tText("notes:untitled"),body=this.sourceDirty?$('#markdown-source',this.host).value:this.handle.markdown(),blocks=this.sourceDirty?pageBlocks({title,body,blocks:null}):structuredClone(this.handle.document()),snapshot={title,body,blocks,base_version:this.page.version,created:Date.now()};
    this.handle.editor.isEditable=false;$('#page-title',this.host).disabled=true;$('#markdown-source',this.host).readOnly=true;this.recoverySnapshot=snapshot;
    let saved=true;try{await saveNoteRecovery(this.ctx,id,snapshot);}catch(problem){saved=false;this.ctx.toast(problem.message);}
    if(this.disposed||this.page?.id!==id)return true;this.showNoteRecovery(id,snapshot,true,saved);$('#page-save-status',this.host).textContent=saved?tText("notes:localDraftSaved"):tText("notes:copyLocalText");$('#retry-page-save',this.host).hidden=true;return true;
  }
  async retainNotionDraft(){
    if(!this.page?.notion_sync||!this.state.dirty||this.recoveryBlocked)return;
    const id=this.page.id,title=$('#page-title',this.host).value.trim()||tText("notes:untitled"),body=this.sourceDirty?$('#markdown-source',this.host).value:this.handle.markdown(),blocks=this.sourceDirty?pageBlocks({title,body,blocks:null}):structuredClone(this.handle.document()),snapshot={title,body,blocks,base_version:this.page.version,created:Date.now(),notion_sync:true};
    clearTimeout(this.timer);clearTimeout(this.maxSaveTimer);this.recoveryBlocked=true;this.recoverySnapshot=snapshot;
    this.handle.editor.isEditable=false;$('#page-title',this.host).disabled=true;$('#markdown-source',this.host).readOnly=true;
    let saved=true;try{await saveNoteRecovery(this.ctx,id,snapshot);}catch(error){saved=false;this.ctx.toast(error.message);}
    if(this.disposed||this.page?.id!==id)return;
    this.showNoteRecovery(id,snapshot,true,saved);$('#page-save-status',this.host).textContent=saved?tText('notes:localDraftSaved'):tText('notes:copyLocalText');$('#retry-page-save',this.host).hidden=true;
  }
  showNoteRecovery(id,snapshot,blocked,saved){
    if(this.disposed||this.page?.id!==id)return;this.recoverySnapshot=snapshot;
    $('[data-note-recovery]',this.host)?.remove();const banner=document.createElement('details');banner.dataset.noteRecovery='';banner.className='note-status-chip note-recovery-chip';
    banner.innerHTML=`<p>${blocked?tText(snapshot.notion_sync?'portability:notionUnsavedKept':"notes:thePageHasEnteredCollaborativeModeSavingThe"):tText("notes:yourPreviousLocalDraftIsSavedOnThis")}</p><p class="hint">${saved?tText("notes:theDraftIsEncryptedAndIsOnlyAccessible"):tText("notes:failedToSaveTheDraftToTheDevice")}</p><div class="federation-person-actions"><button class="secondary compact" data-recovery-view>${tHtml("notes:viewAndCopyDraft")}</button>${blocked?`<button class="primary compact" data-recovery-open>${tHtml(snapshot.notion_sync?'portability:notionOpenSaved':"notes:openTheSharedVersion")}</button>`:''}<button class="text-link danger-link" data-recovery-discard>${tHtml("notes:deleteLocalDraft")}</button></div><p class="error" data-recovery-error role="alert"></p>`;
    const content=document.createElement('div');content.className='note-status-popover';content.innerHTML=banner.innerHTML;banner.replaceChildren();const summary=document.createElement('summary');summary.textContent=(blocked?'⚠ ':'')+tText('notes:localDraft');summary.dataset.badgeIcon=blocked?'⚠':'▤';summary.setAttribute('aria-label',tText('notes:localDraft'));summary.title=tText('notes:localDraft');banner.append(summary,content);statusBadges(this.host).append(banner);
    $('[data-recovery-view]',banner).onclick=()=>{const dialog=this.ctx.modal(`<h2>${tHtml("notes:yourLocalDraft")}</h2><p class="hint">${tHtml("notes:thisDoesNotReplaceTheMainVersionCopy")}</p><textarea rows="10" readonly data-recovery-text aria-label="${tHtml("notes:localDraft")}"></textarea><button class="primary" data-recovery-copy>${tHtml("notes:copyText")}</button>`);$('[data-recovery-text]',dialog).value=snapshot.body;$('[data-recovery-copy]',dialog).onclick=()=>navigator.clipboard.writeText(snapshot.body).then(()=>this.ctx.toast(tText("notes:textCopied"))).catch(()=>{$('[data-recovery-text]',dialog).select();this.ctx.toast(tText("notes:manuallyCopyTheSelectedText"));});};
    const action=fn=>async event=>{const button=event.currentTarget;button.disabled=true;try{await fn();}catch(problem){$('[data-recovery-error]',banner).textContent=problem.message;}finally{button.disabled=false;}};
    $('[data-recovery-open]',banner)?.addEventListener('click',action(async()=>{if(!saved&&!confirm(tText("notes:localDraftNotSavedFirstCopyTheRequired")))return;const latest=await this.ctx.api('/notes/'+id),index=this.state.notes.findIndex(note=>note.id===id);if(index>=0)this.state.notes[index]=latest;this.recoveryBlocked=false;this.state.dirty=false;await this.open(id,false);}));
    $('[data-recovery-discard]',banner).onclick=action(async()=>{if(!confirm(tText("notes:deleteLocalDraftTheMainVersionWillRemain")))return;await saveNoteRecovery(this.ctx,id,null);if(blocked){const latest=await this.ctx.api('/notes/'+id),index=this.state.notes.findIndex(note=>note.id===id);if(index>=0)this.state.notes[index]=latest;this.recoveryBlocked=false;this.state.dirty=false;await this.open(id,false);}else banner.remove();});
  }
  breadcrumbs() {
    const out = [];
    let n = this.page;
    const seen = new Set();
    while (n && !seen.has(n.id)) {
      seen.add(n.id);
      out.unshift(n);
      n = this.state.notes.find((x) => x.id === n.parent_id);
    }
    return out
      .map(
        (n, i) =>
          `<button class="crumb" data-crumb="${esc(n.id)}">${pageIcon(n)} ${esc(n.title)}</button>${i < out.length - 1 ? '<span class="muted">/</span>' : ""}`,
      )
      .join("");
  }
  async renderPage() {
    this.closePageMenu?.(false);
    this.linkObserver?.disconnect();
    clearTimeout(this.linkTimer);
    clearTimeout(this.lockTimer);
    this.database?.destroy();this.database=null;this.discussions?.destroy();clearInterval(this.readTimer);
    this.live?.destroy();this.live=null;
    const page = this.page;
    const screen=$('#notion-page',this.host);
    const sourceOpened=screen.dataset.renderedNote===page.id&&Boolean($('.notion-source-banner',screen)?.open);
    screen.dataset.renderedNote=page.id;
    $("#notion-page",this.host).classList.toggle("is-database-page",page.kind==='database');
    $("#notion-page", this.host).classList.toggle(
      "full-width",
      !!page.full_width,
    );
    $("#notion-page", this.host).classList.toggle("has-cover", !!page.cover);
    $("#notion-page", this.host).innerHTML =
      pageLayout(page,{breadcrumbs:this.breadcrumbs(),participants:page.space_id?this.participantAvatars(page):''});
    if(page.kind==='database'){const heading=document.createElement('div');heading.className='database-page-heading';const title=$('#page-title',this.host);title.before(heading);heading.append($('#change-icon',this.host),title);}
    this.viewPreferences=readPageView(this.state.me?.id,page.id);
    const participants=$('#page-participants',this.host);if(participants)participants.onclick=()=>permissionsDialog(page,this.ctx,()=>this.refresh());
    applyPageView($('#notion-page',this.host),this.viewPreferences);
    $('#note-lock',this.host)?.addEventListener('click',()=>this.unlockDialog());
    $('#favorite-page',this.host).onclick=()=>this.meta({favorite:!this.page.favorite});
    if(page.unlock_until){
      this.lockTimer=setTimeout(async()=>{if(this.disposed||this.page?.id!==page.id)return;if(this.state.dirty)this.ctx.toast(tText("notes:lockingTimeHasExpiredUnsavedTextRemainsIn"));else{await this.releaseLease(true);const latest=await this.ctx.api('/notes/'+page.id);this.page=latest;this.state.notes[this.state.notes.findIndex(n=>n.id===page.id)]=latest;this.handle?.destroy();this.renderPage();}},Math.max(0,page.unlock_until*1000-Date.now()));
    }
    $('#retry-page-save',this.host).onclick=()=>this.flush();
    const discussions=$('#discussions-panel',this.host);
    this.discussions=null;
    discussions.ontoggle=()=>{
      if(this.disposed||this.page?.id!==page.id||!discussions.isConnected)return;
      if(page.isDraft){discussions.open=false;return;}
      if(discussions.open&&!this.discussions)this.discussions=mountDiscussions($('#page-discussions',this.host),page,this.ctx,()=>this.handle?.editor.getTextCursorPosition().block.id);
      this.discussions?.setOpen(discussions.open);
    };
    if(page.database_id){
      this.ctx.api('/databases/'+page.database_id).then(db=>{if(this.disposed||this.page?.id!==page.id)return;const record=db.records.find(r=>r.note_id===page.id);if(!record)return;const box=$('#record-properties-panel',this.host);box.innerHTML='<div class="record-property-list">'+db.properties.map(p=>'<div><span>'+esc(p.name)+'</span><strong>'+esc(display(record.properties[p.id])||'—')+'</strong></div>').join('')+'</div>'+((page.role==='editor'&&db.role==='editor'&&!page.locked)?`<button class="secondary compact" id="edit-record-properties">${tHtml("notes:entryProperties")}</button>`:'');$('#edit-record-properties',box)?.addEventListener('click',()=>recordForm(db,record,this.ctx,()=>this.open(page.id)));}).catch(e=>this.ctx.toast(e.message));
    }
    mountNotionSource($('#notion-page',this.host),page,this.ctx,{openPage:id=>this.open(id,false),refresh:()=>this.refresh(),beforeCopy:()=>this.flush()});
    if(sourceOpened&&$('.notion-source-banner',screen))$('.notion-source-banner',screen).open=true;
    const readonly=(page.role&&page.role!=='editor')||(page.locked&&!page.unlock_until);
    if(readonly){
      this.handle=mountBlockEditor($('#block-editor',this.host),page,{readOnly:true,getPage:id=>this.state.notes.find(n=>n.id===id),openPage:id=>this.open(id)});
      $('#page-title',this.host).disabled=true;
      $('#change-icon',this.host).disabled=true;
      $('#change-cover',this.host).hidden=true;
      $('#page-actions',this.host).onclick=()=>this.actions();
      $('.editor-help',this.host).textContent=page.locked?tText("notes:finalPageIsLockedClickTheLockTo"):tText("notes:pageIsReadOnly");
      if(page.kind==='database'){$('#block-editor',this.host).hidden=!(page.blocks?.length||page.body);this.database=mountDatabase($('#database-container',this.host),readonly?{...page,role:'viewer'}:page,this.ctx,()=>this.refresh());}
      if(page.space_id)$('#live-presence',this.host).textContent=tText("notes:updatingEvery5Seconds", {value0:(page.role==='viewer'?tText("notes:readOnly"):tText("notes:viewAndComments"))});
      $('#share-page',this.host).onclick=()=>this.ctx.sharePanel(page.id,{database:page.kind==='database'});
      if(!page.notion_sync)this.readTimer=setInterval(async()=>{try{const latest=await this.ctx.api('/notes/'+page.id);if(this.disposed||this.page?.id!==page.id)return;if(latest.role!==page.role||latest.locked!==page.locked||latest.unlock_until!==page.unlock_until){await this.refresh();await this.open(page.id,false);return;}if(latest.version!==this.page.version){this.page=latest;$('#page-title',this.host).value=latest.title;if(latest.kind!=='database'){this.handle?.destroy();this.handle=mountBlockEditor($('#block-editor',this.host),latest,{readOnly:true,getPage:id=>this.state.notes.find(n=>n.id===id),openPage:id=>this.open(id)});}}}catch(e){clearInterval(this.readTimer);this.ctx.toast(e.message);}},5000);
      this.host.querySelectorAll('[data-crumb]').forEach(b=>b.onclick=()=>this.open(b.dataset.crumb));
      this.revealLinkedBlock();return;
    }
    if(page.kind==='database'){
      $('.editor-help',this.host).textContent=tText("notes:eachEntryIsASeparatePageWithBlocks");
      $('#block-editor',this.host).hidden=true;
      if(page.blocks?.length||page.body){$('#block-editor',this.host).hidden=false;this.handle=mountBlockEditor($('#block-editor',this.host),page,{readOnly:true,getPage:id=>this.state.notes.find(n=>n.id===id),openPage:id=>this.open(id)});}
      if(page.space_id)$('#live-presence',this.host).textContent=tText("notes:mainDatabaseUpdatingEvery5Seconds");
      this.database=mountDatabase($('#database-container',this.host),readonly?{...page,role:'viewer'}:page,this.ctx,()=>this.refresh());
      $('#page-title',this.host).oninput=()=>this.changed();
      $('#share-page',this.host).onclick=async()=>{if(await this.flushDatabase())this.ctx.sharePanel(page.id,{database:true});};

      $('#favorite-page',this.host).onclick=()=>this.meta({favorite:!this.page.favorite});$('#page-actions',this.host).onclick=()=>this.actions();$('#change-icon',this.host).onclick=()=>this.iconPicker();$('#change-cover',this.host).onclick=()=>this.coverPicker();
      this.host.querySelectorAll('[data-crumb]').forEach(b=>b.onclick=()=>this.open(b.dataset.crumb));return;
    }
    if(page.space_id||page.federation_shared){
      $('#page-title',this.host).disabled=true;
      $('#share-page',this.host).disabled=true;
      const live=collaborativePage(page,this.ctx,{
        onDirty:()=>this.changed(),
        onVersion:v=>{if(v&&this.page?.id===page.id){this.page.version=Math.max(this.page.version||0,v);const p=this.state.notes.find(n=>n.id===page.id);if(p)p.version=Math.max(p.version||0,v);}},
        onTitle:title=>{if(this.page?.id!==page.id)return;this.page.title=title;const p=this.state.notes.find(n=>n.id===page.id);if(p)p.title=title;const input=$('#page-title',this.host);if(input&&input.value!==title)input.value=title;this.tree();},
        onStatus:status=>{if(this.page?.id!==page.id)return;const el=$('#page-save-status',this.host);if(el)el.textContent={saved:tText("notes:savedOnline"),saving:tText("notes:saving"),offline:tText("notes:noConnection2"),error:tText("notes:saveError")}[status];const retry=$('#retry-page-save',this.host);if(retry)retry.hidden=!['offline','error'].includes(status);},
        onPresence:names=>{const el=$('#live-presence',this.host);if(el)el.textContent=tText("notes:togetherOnThePage", {value0:([...new Set(names)].join(', '))});},
        onError:e=>{this.ctx.toast(e.message);if([401,403,404,423].includes(e.status)){if(e.status===403||e.status===423)this.refresh().then(()=>{if(!this.disposed&&this.page?.id===page.id)this.open(page.id,false);});const title=$('#page-title',this.host);if(title)title.disabled=true;if(this.handle)this.handle.editor.isEditable=false;}},
        onReset:async()=>{if(this.disposed)return;this.ctx.toast(tText("notes:anotherVersionOfThePageHasBeenRestored"));try{await this.refresh();await this.open(page.id,false);}catch(e){this.ctx.toast(e.message);}}
      });
      this.live=live;
      try{await live.ready;}catch(e){if(!this.disposed&&this.live===live){$('#live-presence',this.host).textContent=e.message;}return;}
      if(this.disposed||this.live!==live||this.page?.id!==page.id)return;
      $('#page-title',this.host).value=live.doc.getMap('meta').get('title')||tText("notes:untitled");$('#page-title',this.host).disabled=false;
      $('#markdown-source',this.host).readOnly=true;
    }
    this.handle = mountBlockEditor($("#block-editor", this.host), page, {
      collaboration:this.live?.collaboration,
      onChange: () => this.changed(),
      getPage: (id) => this.state.notes.find((n) => n.id === id),
      openPage: (id) => this.open(id),
      createDatabase: blockId=>this.newDatabase(page.id,blockId),
      createSubpage: (blockId) => this.createInlineSubpage(blockId),
      onComment: blockId => this.commentBlock(blockId),
      copyBlockLink: blockId => this.copyPageLink(blockId),
      pickCalloutIcon: onPick => iconPicker(this,{onPick,emojiOnly:true,title:tText('notes:calloutIcon')}),
      moveBlocks: blockIds => this.moveBlocks(blockIds),
      toast: message => this.ctx.toast(message),
      uploadFile: async file => {
        if(file.size>50*1024*1024)throw new Error(tText("notes:theFileMustBeNoMoreThan50"));
        if(this.disposed||this.page!==page)throw new Error(tText("notes:pageIsAlreadyClosed"));
        if(!(await this.persistDraft()))throw new Error(tText("notes:couldNotSaveThePageBeforeUploading"));
        if(this.disposed||this.page!==page)throw new Error(tText("notes:pageIsAlreadyClosed"));
        const saved=await this.ctx.api('/notes/'+page.id+'/files?name='+encodeURIComponent(file.name),'POST',file);
        return new URL(saved.url,location.origin).href;
      },
    });
    this.subpageLinksKey=JSON.stringify(pageLinkIds(this.handle.document()));
    if(this.handle.normalizedOnLoad&&!this.live)this.changed();
    $("#page-title", this.host).oninput = () => {if(this.live)this.live.title($("#page-title",this.host).value);this.changed();};
    $("#markdown-source", this.host).oninput = () => {
      this.sourceDirty = true;
      this.changed();
    };
    $("#share-page", this.host).onclick = async () => {
      if(!this.live&&this.page.blocks==null&&(this.page.body||this.sourceDirty)){
        if(this.sourceDirty){this.handle.replaceMarkdown($("#markdown-source",this.host).value);this.sourceDirty=false;}
        this.changed();
      }
      if (await this.persistDraft() && await this.flush()) this.ctx.sharePanel(this.page.id);
    };
    $("#share-page", this.host).disabled=false;
    $("#favorite-page", this.host).onclick = () =>
      this.meta({ favorite: !this.page.favorite });
    $("#page-actions", this.host).onclick = () => this.actions();
    $("#change-icon", this.host).onclick = () => this.iconPicker();
    $("#change-cover", this.host).onclick = () => this.coverPicker();
    this.host
      .querySelectorAll("[data-crumb]")
      .forEach((b) => (b.onclick = () => this.open(b.dataset.crumb)));

    this.revealLinkedBlock();
  }
  setMode(mode) {
    if (this.mode === mode) return;
    if (mode === "markdown") {
      $("#markdown-source", this.host).value = this.handle.markdown();
      this.sourceDirty = false;
    } else if (this.sourceDirty) {
      this.handle.replaceMarkdown($("#markdown-source", this.host).value);
      this.sourceDirty = false;
      this.changed();
    }
    this.mode = mode;
    $("#block-editor", this.host).hidden = mode !== "blocks";
    $("#markdown-source", this.host).hidden = mode !== "markdown";
  }
  changed() {
    // Toggling Tiptap's editability can deliver a queued onChange. The source
    // is frozen during a transfer; remote revisions are tracked by onVersion.
    if (this.disposed || this.transferBusy) return;
    this.sequence++;
    this.state.dirty = true;
    $("#page-save-status", this.host).textContent = tText("notes:changesPending");
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), 350);
    this.maxSaveTimer ??= setTimeout(()=>{this.maxSaveTimer=null;this.flush();},2000);
  }
  async flushDatabase(notify=false){
    if(this.dbPending){const ok=await this.dbPending;if(!ok)return false;return this.state.dirty?this.flushDatabase(notify):true;}
    if(!this.state.dirty)return true;
    $('#retry-page-save',this.host).hidden=true;$('#page-save-status',this.host).textContent=tText("notes:saving");
    const seq=this.sequence,title=$('#page-title',this.host).value.trim()||tText("notes:untitled");
    this.dbPending=(async()=>{try{const saved=await this.ctx.api('/notes/'+this.page.id+'/database-title','PATCH',{title,version:this.page.version});Object.assign(this.page,saved);Object.assign(this.state.notes.find(n=>n.id===this.page.id),saved);this.state.dirty=this.sequence!==seq;if(!this.disposed){$('#page-save-status',this.host).textContent=this.state.dirty?tText("notes:changesPending"):tText("notes:saved");this.tree();if(notify)this.ctx.toast(tText("notes:databaseNameSaved"));}return true;}catch(e){this.ctx.toast(e.message);if(!this.disposed){$('#page-save-status',this.host).textContent=tText("notes:couldNotSave");$('#retry-page-save',this.host).hidden=false;}return false;}finally{this.dbPending=null;}})();
    const ok=await this.dbPending;return ok&&this.state.dirty?this.flushDatabase(notify):ok;
  }
  async flush(notify = false) {
    if(this.recoveryBlocked)return false;
    if(this.remote?.active)return this.remote.flush(notify);
    if(this.vault)return this.vault.flush();
    if(this.transferBusy){
      if(!this.transferNotice){this.ctx.toast(tText("notes:blocksAreBeingMovedWaitForItTo"));this.transferNotice=true;}
      return false;
    }
    clearTimeout(this.timer);clearTimeout(this.maxSaveTimer);this.maxSaveTimer=null;
    if(this.page?.role&&this.page.role!=='editor')return true;
    if(this.page?.kind==='database')return this.flushDatabase(notify);
    if (this.pending) {
      const ok = await this.pending;
      if (!ok) return false;
      if (this.state.dirty) return this.flush(notify);
      return true;
    }
    if(this.page?.space_id||this.page?.federation_shared){
      // A freshly opened, unchanged page can be left while its live connection
      // is still starting. Local edits must always wait for server acknowledgement.
      if(!notify&&!this.state.dirty&&!this.live?.hasUnsavedChanges)return true;
      if(!this.live||!this.handle)return false;
      const ok=await this.live.flush();
      if(!ok){if(notify)this.ctx.toast(tText("notes:changesHaveNotBeenConfirmedByTheServer"));if(!this.disposed)$('#retry-page-save',this.host).hidden=false;return false;}
      try{
        const saved=await this.ctx.api('/notes/'+this.page.id);this.page={...saved};const idx=this.state.notes.findIndex(n=>n.id===saved.id);if(idx>=0)this.state.notes[idx]=saved;
        this.state.dirty=!this.live.isSaved;if(this.state.dirty)return this.flush(notify);await this.syncSubpageTree();if(!this.disposed){this.tree();if(notify)this.ctx.toast(tText("notes:mainPageSaved"));}return true;
      }catch(e){this.ctx.toast(e.message);return false;}
    }
    if (!this.page || (!this.state.dirty&&!this.forceDraft)) return true;
    const seq = this.sequence;
    const title = $("#page-title", this.host).value.trim() || tText("notes:untitled");
    const payload = {
      title,
      body: this.sourceDirty
        ? $("#markdown-source", this.host).value
        : this.handle.markdown(),
      blocks: this.sourceDirty ? null : this.handle.document(),
      version: this.page.version,
    };
    if(this.page.isDraft&&!this.forceDraft&&!hasContent(payload)){
      this.state.dirty=false;$('#page-save-status',this.host).textContent=tText("notes:emptyPageNotSaved");return true;
    }
    const oldId=this.page.id,isDraft=this.page.isDraft;
    if(isDraft){delete payload.version;payload.parent_id=this.page.parent_id;}
    $("#page-save-status", this.host).textContent = tText("notes:saving");
    $("#retry-page-save",this.host).hidden=true;
    this.error = null;
    this.pending = (async () => {
      try {
        const saved = await this.ctx.api(
          isDraft?"/notes":"/notes/" + oldId,
          isDraft?"POST":"PUT",
          payload,
        );
        const joinCollaboration=!!saved.federation_shared&&!this.live;
        Object.assign(this.page,saved);delete this.page.isDraft;
        // A remote reader may have initialized an inherited database record
        // while this owner was editing its previous private representation.
        // Drain any newer local snapshot before changing save protocols.
        if(joinCollaboration)this.page.federation_shared=false;
        const idx = this.state.notes.findIndex((n) => n.id === oldId);
        if(isDraft){transferPageView(this.state.me?.id,oldId,saved.id);this.state.selected=saved.id;history.replaceState({},'', '/page/'+saved.id);}
        if (idx >= 0) this.state.notes[idx] = saved;
        await this.syncSubpageTree();
        if (!this.disposed) {
          this.state.dirty = this.sequence !== seq;
          $("#page-save-status", this.host).textContent = this.state.dirty
            ? tText("notes:changesPending")
            : tText("notes:saved");
          this.tree();
          if (notify) this.ctx.toast(tText("notes:pageSaved"));
          if(joinCollaboration&&!this.state.dirty){
            if(this.handle)this.handle.editor.isEditable=false;
            $('#page-title',this.host).disabled=true;
            await this.open(saved.id,false);
          }
        }
        return true;
      } catch (e) {
        this.error = e;
        if(e.status===423&&this.page?.notion_sync){
          try{const latest=await this.ctx.api('/notes/'+this.page.id);this.page.notion_sync=latest.notion_sync;mountNotionSource($('#notion-page',this.host),this.page,this.ctx,{openPage:id=>this.open(id,false),refresh:()=>this.refresh(),beforeCopy:()=>this.flush()});await this.retainNotionDraft();}catch(problem){this.ctx.toast(problem.message);}return false;
        }
        if(await this.recoverSharedConflict(e))return false;
        if (!this.disposed) {
          $("#page-save-status", this.host).textContent =
            e.status === 409 ? tText("notes:versionConflict") : tText("notes:couldNotSave");
          $("#retry-page-save",this.host).hidden=false;
          this.ctx.toast(e.message);
        }
        return false;
      } finally {
        this.pending = null;
      }
    })();
    const ok = await this.pending;
    if (ok && this.state.dirty && !this.disposed) return this.flush(notify);
    return ok;
  }
  discardDraft(){
    if(!this.page?.isDraft)return;
    this.state.notes=this.state.notes.filter(n=>n.id!==this.page.id);
    if(this.state.selected===this.page.id)this.state.selected=null;
  }
  async persistDraft(){
    if(!this.page?.isDraft)return true;
    this.forceDraft=true;
    try{return await this.flush();}finally{this.forceDraft=false;}
  }
  contextMenu(id,x,y){
    this.closeContext?.();
    const note=this.state.notes.find(n=>n.id===id);if(!note)return;
    const menu=document.createElement('div');menu.className='page-context-menu';menu.setAttribute('role','menu');
    const editable=note.role==='editor'&&(!note.locked||note.unlock_until);
    menu.innerHTML=`<button role="menuitem" data-context="open">${tHtml("notes:open2")}</button>${!note.isDraft?'<button role="menuitem" data-context="favorite">'+(note.favorite?tText("notes:removeFromFavorites"):tText("notes:addToFavorites"))+'</button>':''}${editable?`<button role="menuitem" class="danger" data-context="trash">${tHtml("notes:deleteToTrash")}</button>`:''}`;
    document.body.append(menu);menu.style.left=Math.max(8,Math.min(x,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(y,innerHeight-menu.offsetHeight-8))+'px';
    const outside=e=>{if(!menu.contains(e.target))close();},keys=e=>{if(e.key==='Escape'){close();}if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const items=[...menu.querySelectorAll('button')],i=items.indexOf(document.activeElement);items[(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length].focus();}};
    const close=()=>{menu.remove();document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',keys);this.closeContext=null;};this.closeContext=close;
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',keys);menu.querySelector('button').focus();
    menu.querySelectorAll('[data-context]').forEach(b=>b.onclick=async()=>{close();try{if(b.dataset.context==='open')return this.open(id);if(!(await this.flush()))return;const target=note.isDraft?this.page.id:id;if(b.dataset.context==='trash')return this.removePage(target);await this.ctx.api('/notes/'+id,'PATCH',{favorite:!note.favorite});await this.refresh();if(this.page?.id===id)this.page.favorite=!note.favorite;}catch(e){this.ctx.toast(e.message);}});
  }
  async removePage(id){
    if(!await this.flush())return;
    const note=this.state.notes.find(n=>n.id===id);if(!note)return;
    if(!note.isDraft&&!confirm(tText("notes:moveThePageAndItsSubpagesToThe")))return;
    if(!note.isDraft)await this.ctx.api('/notes/'+id,'DELETE');
    if(note.isDraft)this.discardDraft();else await this.refresh();
    if(this.disposed)return;
    const selected=this.page?.id;
    if(selected===id||!this.state.notes.some(n=>n.id===selected)){
      this.state.selected=null;this.state.dirty=false;this.page=null;
      this.database?.destroy();this.discussions?.destroy();clearInterval(this.readTimer);this.live?.destroy();this.live=null;this.handle?.destroy();this.handle=null;history.replaceState({},'','/');this.empty();
    }
    else if(selected&&!this.state.dirty)await this.open(selected,false);
    this.tree();this.ctx.toast(note.isDraft?tText("notes:emptyPageClosed"):tText("notes:pageInTrash"));
  }
  releaseLease(wait=false){
    clearTimeout(this.lockTimer);
    if(!this.page?.unlock_until)return Promise.resolve();
    const id=this.page.id;this.page.unlock_until=null;const n=this.state.notes.find(n=>n.id===id);if(n)n.unlock_until=null;
    const pending=wait?this.ctx.api('/notes/'+id+'/lock','POST',{mode:'relock'}):fetch('/api/notes/'+id+'/lock',{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRF-Token':csrfCookie()},body:JSON.stringify({mode:'relock'})});
    return pending.catch(e=>this.ctx.toast(e.message));
  }
  unlockDialog(){
    const el=this.ctx.modal(`<h2>${tHtml("notes:unlockNote")}</h2><p class="muted">${tHtml("notes:temporaryAccessIsValidFor15MinutesAnd")}</p><div class="page-action-list"><button data-unlock="unlock">${tHtml("notes:unlockCompletely")}</button><button data-unlock="temporary">${tHtml("notes:unlockTemporarily")}</button><button data-unlock="relock">${tHtml("notes:leaveLocked")}</button></div>`);
    el.querySelectorAll('[data-unlock]').forEach(b=>b.onclick=async()=>{try{if(this.page.unlock_until>Date.now()/1000&&!(await this.flush()))return;const status=await this.ctx.api('/notes/'+this.page.id+'/lock','POST',{mode:b.dataset.unlock});Object.assign(this.page,status);$('.modal-close',el).click();if(this.state.dirty&&!(await this.flush()))return;await this.refresh();await this.open(this.page.id,false);}catch(e){this.ctx.toast(e.message);}});
  }
  async meta(values) {
    try {
      if (!(await this.persistDraft()) || !(await this.flush())) return;
      const previous=this.page;
      const page = await this.ctx.api("/notes/" + this.page.id, "PATCH", {
        ...values,
        version: this.page.version,
      });
      this.page = page;
      const idx = this.state.notes.findIndex((n) => n.id === page.id);
      this.state.notes[idx] = page;
      const sameDocument=page.title===previous.title&&page.body===previous.body&&JSON.stringify(page.blocks)===JSON.stringify(previous.blocks);
      const sameAccess=page.role===previous.role&&page.locked===previous.locked&&page.unlock_until===previous.unlock_until;
      if(sameDocument&&sameAccess&&Object.keys(values).every(key=>key==='favorite'||key==='full_width')){
        if('full_width' in values)$('#notion-page',this.host).classList.toggle('full-width',!!page.full_width);
        const favorite=$('#favorite-page',this.host),label=page.favorite?tText("notes:removeFromFavorites"):tText("notes:addToFavorites2");
        favorite.innerHTML=pageMenuIcon('star',!!page.favorite);favorite.setAttribute('aria-label',label);favorite.setAttribute('aria-pressed',String(!!page.favorite));favorite.title=label;
        this.tree();return;
      }
      this.handle?.destroy();
      this.renderPage();
      this.tree();
    } catch (e) {
      this.ctx.toast(e.message);
    }
  }
  async move(id, parent, before=null) {
    try {
      const draftId=this.page?.isDraft?this.page.id:null;
      if(draftId&&(id===draftId||parent===draftId||before===draftId)){if(!(await this.persistDraft()))return;if(id===draftId)id=this.page.id;if(parent===draftId)parent=this.page.id;if(before===draftId)before=this.page.id;}
      if (!(await this.flush())) return;
      const note = this.state.notes.find((n) => n.id === id);
      await this.ctx.api("/notes/" + id + '/move', "POST", {
        parent_id: parent,
        before_id: before,
        version: note.version,
      });
      if (parent) this.expanded.add(parent);
      await this.refresh();
      if (this.page?.id) await this.open(this.page.id, false);
    } catch (e) {
      this.ctx.toast(e.message);
    }
  }
  reorderNote(id,direction){
    const note=this.state.notes.find(n=>n.id===id);
    if(!note?.notion_sync?.can_reorder)return;
    const siblings=this.state.notes.filter(n=>n.parent_id===note.parent_id&&!n.deleted&&n.space_id===note.space_id),index=siblings.findIndex(n=>n.id===id);
    if(direction<0&&index>0)return this.move(id,note.parent_id,siblings[index-1].id);
    if(direction>0&&index>=0&&index<siblings.length-1)return this.move(id,note.parent_id,siblings[index+2]?.id||null);
  }
  templatePicker() {
    const el = this.ctx.modal(
      `<h2>${tHtml("notes:startWithATemplate")}</h2><div class="template-grid">${templates
          .map(
            (t) =>
              `<button class="template-card" data-template="${t.id}"><span>${t.icon}</span><strong>${t.name}</strong></button>`,
          )
          .join("")}</div>`,
    );
    el.querySelectorAll("[data-template]").forEach(
      (b) =>
        (b.onclick = () => {
          $(".modal-close", el).click();
          this.newPage(
            null,
            templates.find((t) => t.id === b.dataset.template),
          );
        }),
    );
  }
  iconPicker(){iconPicker(this);}
  coverPicker(){coverPicker(this);}
  actions() {
    if(this.closePageMenu){this.closePageMenu();this.closePageMenu=null;return;}
    const page=this.page,editable=(!page.role||page.role==='editor')&&(!page.locked||page.unlock_until);
    const actions=pageActions(page,editable);
    if(page.notion_sync?.can_reorder){
      const roots=this.state.notes.filter(n=>n.parent_id===page.parent_id&&!n.deleted&&n.space_id===page.space_id),index=roots.findIndex(n=>n.id===page.id);
      for(const action of actions){if(action.name==='move-up')action.disabled=index<=0;if(action.name==='move-down')action.disabled=index<0||index===roots.length-1;}
    }
    const words=pageWordCount(this.sourceDirty?null:this.handle?.document(),this.sourceDirty?$('#markdown-source',this.host).value:page.body||'');
    const close=showPageMenu({anchor:$('#page-actions',this.host),page,view:{...this.viewPreferences},mode:this.mode,editable,actions,words,
      onAction:action=>this.pageAction(action,page),
      onClose:()=>{this.closePageMenu=null;},
      onMode:mode=>{if(this.page?.id===page.id)this.setMode(mode);},
      onView:value=>{
        if(this.page?.id!==page.id)return;
        this.viewPreferences={...value};applyPageView($('#notion-page',this.host),value);
        if(!savePageView(this.state.me?.id,page.id,value))this.ctx.toast(tText("notes:configurationAppliedTheBrowserDidNotAllowSaving"));
      },
    });
    this.closePageMenu=(focus=true)=>{close(focus);this.closePageMenu=null;};
  }
  async pageAction(action,page){
    try{
      if(this.page?.id!==page.id)return;
      if(action==='copy-link')return this.copyPageLink();
      if(action==='copy-content')return this.copyPageContents();
      if(!(await this.flush())||this.page?.id!==page.id)return;
      if(action==='trash'&&this.page.isDraft)return this.removePage(this.page.id);
      if(!(await this.persistDraft()))return;
      if(action==='permissions')return permissionsDialog(this.page,this.ctx,()=>this.refresh());
      if(action==='lock'){
        if(this.page.locked)return this.unlockDialog();
        await this.ctx.api('/notes/'+this.page.id+'/lock','POST',{mode:'lock'});await this.refresh();return this.open(this.page.id,false);
      }
      if(action==='child')return this.newPage(this.page.id);
      if(action==='move')return this.moveDialog();
      if(action==='move-up'||action==='move-down')return this.reorderNote(page.id,action==='move-up'?-1:1);
      if(action==='history')return this.history();
      if(action==='width')return this.meta({full_width:!this.page.full_width});
      if(action==='export')return this.export();
      if(action==='import')return this.import();
      if(action==='duplicate'){
        const copy=await this.ctx.api('/notes/'+this.page.id+'/duplicate','POST');
        savePageView(this.state.me?.id,copy.id,this.viewPreferences);
        await this.refresh();return this.open(copy.id);
      }
      if(action==='trash')return this.removePage(this.page.id);
    }catch(error){this.ctx.toast(error.message);}
  }
  async clipboard(text,message){
    try{await navigator.clipboard.writeText(text);this.ctx.toast(message);return true;}
    catch{
      const el=this.ctx.modal(`<h2>${tHtml("notes:copy")}</h2><p class="hint">${tHtml("notes:theSelectedTextCanBeCopiedManually")}</p><textarea id="note-copy-value" rows="6" readonly aria-label="${tHtml("notes:textToCopy")}"></textarea>`);
      const field=$('#note-copy-value',el);field.value=text;field.focus();field.select();return false;
    }
  }
  async copyPageLink(blockId){
    const current=this.page;
    if(!current)return false;
    if(!(await this.flush())||this.page?.id!==current.id||!(await this.persistDraft()))return false;
    const url=new URL('/page/'+this.page.id,location.origin);
    if(blockId)url.hash='block='+encodeURIComponent(blockId);
    return this.clipboard(url.href,blockId?tText("notes:blockLinkCopied"):tText("notes:pageLinkCopied"));
  }
  async copyPageContents(){
    const page=this.page;if(!page)return;
    const title=$('#page-title',this.host).value.trim()||tText("notes:untitled");
    const body=this.sourceDirty?$('#markdown-source',this.host).value:this.handle?.markdown()??page.body;
    return this.clipboard('# '+title+'\n\n'+body,tText("notes:pageContentCopied"));
  }
  async commentBlock(blockId){
    const current=this.page;
    if(!current||!(await this.flush())||this.page?.id!==current.id||!(await this.persistDraft()))return;
    this.handle?.editor.setTextCursorPosition(blockId);
    const panel=$('#discussions-panel',this.host);panel.open=true;
    if(!this.discussions)this.discussions=mountDiscussions($('#page-discussions',this.host),this.page,this.ctx,()=>this.handle?.editor.getTextCursorPosition().block.id);
    this.discussions.setOpen(true);$('#comment-selected-block',this.host)?.click();
    $('#comment-body',this.host)?.focus();panel.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  revealLinkedBlock(){
    this.linkObserver?.disconnect();clearTimeout(this.linkTimer);
    const blockId=new URLSearchParams(location.hash.slice(1)).get('block');
    if(!blockId||blockId.length>100)return;
    const host=$('#block-editor',this.host);
    const reveal=()=>{
      const block=[...host.querySelectorAll('[data-id]')].find(item=>item.dataset.id===blockId);
      if(!block)return false;
      block.scrollIntoView({behavior:'smooth',block:'center'});block.classList.add('note-linked-block');
      setTimeout(()=>block.classList.remove('note-linked-block'),2500);
      this.linkObserver?.disconnect();clearTimeout(this.linkTimer);return true;
    };
    if(reveal())return;
    this.linkObserver=new MutationObserver(reveal);this.linkObserver.observe(host,{childList:true,subtree:true});
    this.linkTimer=setTimeout(()=>{this.linkObserver?.disconnect();},5000);
  }
  async moveBlocks(blockIds){
    try{
      if(this.transferBusy){await this.flush();return;}
      const source=this.page;
      // Moving is an explicit edit. Persist the editor's parsed rich document
      // before using its block IDs when a private page still stores Markdown.
      if(source?.blocks===null&&!source.space_id&&this.handle?.editor.isEditable&&source.kind!=='database')this.changed();
      if(!source||!(await this.flush())||this.page?.id!==source.id||!(await this.persistDraft()))return;
      const sourceId=this.page.id,scope=this.page.space_id||null,sourceHandle=this.handle;
      const ids=[...new Set(blockIds)];
      const eligible=note=>note.id!==sourceId&&(note.space_id||null)===scope&&(!note.role||note.role==='editor')&&(!note.locked||note.unlock_until)&&note.kind!=='database'&&!note.isDraft;
      const targets=this.state.notes.filter(eligible);
      const el=this.ctx.modal(`<h2>${tHtml("notes:moveBlocks")}</h2><p class="hint">${tHtml("notes:blocksWillBeAddedToTheEndOf")}</p><input type="search" id="move-blocks-search" maxlength="200" placeholder="${tHtml("notes:searchingForPage")}" aria-label="${tHtml("notes:searchingForPageForBlocks")}"><div class="page-action-list move-blocks-targets">${targets.map(note=>`<button type="button" data-move-blocks-target="${esc(note.id)}">${pageIcon(note)} ${esc(note.title)}</button>`).join('')}</div><p class="hint" id="move-blocks-empty" ${targets.length?'hidden':''}>${tHtml("notes:noAvailablePagesToMove")}</p><p class="error" role="alert"></p>`);
      const search=$('#move-blocks-search',el);
      search.oninput=()=>{
        const query=search.value.trim().toLocaleLowerCase(locale());
        const buttons=[...el.querySelectorAll('[data-move-blocks-target]')];buttons.forEach(button=>button.hidden=!button.textContent.toLocaleLowerCase(locale()).includes(query));
        $('#move-blocks-empty',el).hidden=buttons.some(button=>!button.hidden);
      };
      search.focus();
      el.querySelectorAll('[data-move-blocks-target]').forEach(button=>button.onclick=async()=>{
        if(this.transferBusy)return;
        const buttons=[...el.querySelectorAll('[data-move-blocks-target]')];buttons.forEach(item=>item.disabled=true);
        const title=$('#page-title',this.host),markdown=$('#markdown-source',this.host);
        let frozen=false,sequence,version;
        const sameSource=()=>!this.disposed&&this.page?.id===sourceId&&this.handle===sourceHandle;
        try{
          if(!sameSource())throw new Error(tText("notes:theOriginalPageIsAlreadyClosed"));
          if(!(await this.flush()))throw new Error(tText("notes:firstSaveThePageChanges"));
          if(!sameSource()||!el.isConnected)return;
          sequence=this.sequence;version=this.page.version;
          this.transferBusy=true;this.transferNotice=false;frozen=true;
          sourceHandle.editor.isEditable=false;title.disabled=true;markdown.disabled=true;
          const targetId=button.dataset.moveBlocksTarget;
          const target=await this.ctx.api('/notes/'+targetId);
          if(!sameSource()||!el.isConnected)return;
          if(this.sequence!==sequence||this.page.version!==version||this.state.dirty)throw new Error(tText("notes:theOriginalPageHasBeenChangedPleaseRepeat"));
          if(!eligible(target))throw new Error(tText("notes:thisPageIsNoLongerAvailableForMoving"));
          const input={target_id:targetId,block_ids:ids,source_version:version,target_version:target.version};
          if(target.blocks===null){
            const parsed=sourceHandle.editor.tryParseMarkdownToBlocks(target.body||'');
            input.target_blocks=parsed.length?parsed:[{id:crypto.randomUUID(),type:'paragraph',content:[],children:[]}];
          }
          const result=await this.ctx.api('/notes/'+sourceId+'/blocks/move','POST',input);
          if(!sameSource())return;
          // The transaction response is canonical. Never save the old editor after
          // a successful move, even if the picker was closed while it was running.
          for(const page of [result.source,result.target]){
            const index=this.state.notes.findIndex(note=>note.id===page.id);
            if(index>=0)this.state.notes[index]=page;
          }
          this.page=result.source;this.state.dirty=false;
          clearTimeout(this.timer);clearTimeout(this.maxSaveTimer);this.maxSaveTimer=null;
          if(el.isConnected)$('.modal-close',el).click();
          await this.open(targetId,false);
          if(!this.disposed)this.ctx.toast(tText("notes:blocksHaveBeenMoved"));
        }catch(error){
          if(el.isConnected)$('.error',el).textContent=error.message;
          else if(!this.disposed)this.ctx.toast(error.message);
        }finally{
          if(frozen){
            this.transferBusy=false;this.transferNotice=false;
            if(sameSource()){
              const editable=(!this.page.role||this.page.role==='editor')&&(!this.page.locked||this.page.unlock_until>Date.now()/1000);
              sourceHandle.editor.isEditable=editable;title.disabled=!editable;markdown.disabled=!editable;
              if(this.state.dirty)this.timer=setTimeout(()=>this.flush(),350);
            }
          }
          if(el.isConnected)buttons.forEach(item=>item.disabled=false);
        }
      });
    }catch(error){this.ctx.toast(error.message);}
  }
  moveDialog() {
    const options = this.state.notes.filter((n) => n.id !== this.page.id && n.space_id===this.page.space_id);
    const el = this.ctx.modal(
      `<h2>${tHtml("notes:whereToMove")}</h2><div class="page-action-list">${this.vaultEnabled&&!this.page.space_id?`<button data-move-secret>${tHtml("notes:secret")}</button>`:''}<button data-parent="">${tHtml("notes:topLevelOfTheWorkspace")}</button>${options
          .map(
            (n) =>
              `<button data-parent="${esc(n.id)}">${pageIcon(n)} ${esc(n.title)}</button>`,
          )
          .join("")}</div>`,
    );
    el.querySelector('[data-move-secret]')?.addEventListener('click',async()=>{try{if(!await this.flush())return;const item=this.page;$('.modal-close',el).click();if(await moveIntoVault(this.ctx,'notes',item)){this.state.notes=await this.ctx.api('/notes');this.page=null;this.state.selected=null;this.tree();if(await this.openVault())await this.vault.openNote(item.id);}}catch(error){this.ctx.toast(error.message);}});
    el.querySelectorAll("[data-parent]").forEach(
      (b) =>
        (b.onclick = () => {
          $(".modal-close", el).click();
          this.move(this.page.id, b.dataset.parent || null);
        }),
    );
  }
  async openVault(){
    if(this.workspaceScope()!=='private')return false;
    if(this.vault&&!this.vault.disposed){if(this.vault.locked)return !!await this.vault.open();return !!await this.vault.ready;}
    if(!await unlockVaultNotes(this.ctx)||!await this.flush())return false;
    await this.releaseLease(true);this.discardDraft();this.historyView?.destroy();this.closePageMenu?.(false);this.closeContext?.();
    this.live?.destroy();this.live=null;this.handle?.destroy();this.handle=null;this.database?.destroy();this.database=null;this.discussions?.destroy();this.discussions=null;
    clearInterval(this.readTimer);clearTimeout(this.timer);clearTimeout(this.maxSaveTimer);clearTimeout(this.lockTimer);
    this.page=null;this.state.selected=null;this.state.dirty=false;history.replaceState({},'', '/');
    this.vault=mountVault($('#notion-page',this.host),{...this.ctx,vaultNoteClosed:id=>{if(id)this.open(id,false);else{this.empty();this.tree();}}},'notes');this.tree();
    const opened=await this.vault.ready;
    if(opened&&this.narrowSidebar.matches)this.sidebar(false);
    return !!opened;
  }
  async history(push=true) {
    if(this.historyView||!this.page||!(await this.flush()))return;
    const id=this.page.id;
    await this.releaseLease(true);this.live?.destroy();this.live=null;
    this.handle?.destroy();this.handle=null;this.discussions?.destroy();clearInterval(this.readTimer);
    $('.notes-shell',this.host).hidden=true;
    if(push)window.history.pushState({},'', '/page/'+id+'/history');
    this.historyView=mountHistory(this.host,this.ctx,id,async(replace=true)=>{
      this.historyView?.destroy();this.historyView=null;if(this.disposed)return;
      $('.notes-shell',this.host).hidden=false;
      if(replace)window.history.replaceState({},'', '/page/'+id);
      try{await this.refresh();if(!this.disposed)await this.open(id,false);}catch(error){this.ctx.toast(error.message);}
    });
    $('.history-sidebar h1',this.host).textContent=this.page.title;
  }
  async trash() {
    if (!(await this.flush())) return;
    this.closeTrash?.(false);
    try{
      const notes=await this.ctx.api('/notes?trash=true');if(this.disposed)return;
      this.closeTrash=trashPopover({anchor:$('#show-trash',this.host),notes,spaces:this.state.spaces||[],scope:this.workspaceScope(),ctx:this.ctx,
        onRestore:async n=>{await this.ctx.api('/notes/'+n.id+'/restore','POST');await this.refresh();await this.open(n.id,false);},
        onRemove:async n=>{await this.ctx.api('/notes/'+n.id+'/permanent','DELETE');},
        onOpen:n=>this.openTrash(n),onHistory:n=>this.trashHistory(n),onClose:()=>{this.closeTrash=null;}});
    }catch(error){this.ctx.toast(error.message);}
  }
  async openTrash(note){
    if(!(await this.flush()))return;
    const previous=this.page?.id;
    await this.releaseLease(true);this.live?.destroy();this.live=null;this.handle?.destroy();this.database?.destroy();this.database=null;this.discussions?.destroy();clearInterval(this.readTimer);clearTimeout(this.lockTimer);this.linkObserver?.disconnect();
    if(this.disposed)return;
    this.page=null;this.state.selected=null;this.state.dirty=false;
    const screen=$('#notion-page',this.host),remaining=Math.max(0,Math.ceil((note.deleted+30*86400-Date.now()/1000)/86400));
    const snapshot=JSON.parse(JSON.stringify(note).replace(/(\/api\/notes\/[A-Za-z0-9_-]+\/(?:files\/[A-Za-z0-9_-]{16}|assets\/[a-f0-9]{32}))(?![A-Za-z0-9_?-])/g,'$1?trash=true'));
    screen.classList.toggle('full-width',!!note.full_width);screen.classList.toggle('has-cover',!!note.cover);screen.classList.remove('is-database-page');
    screen.innerHTML=`<div class="page-top"><button class="text-link" data-trash-back>${tHtml("notes:back")}</button><button class="text-link" data-trash-history>${tHtml("notes:history")}</button></div><div class="note-trash-banner" role="status"><span>${tHtml("notes:pageDeletedBeforeFinalDeletionDays", {value0:(esc(date(note.deleted))), value1:(remaining)})}</span>${note.role==='editor'?`<button type="button" data-trash-restore>${tHtml("notes:restorePage")}</button><button type="button" data-trash-delete>${tHtml("notes:deletePermanently")}</button>`:''}</div>${note.cover?`<div class="page-cover-wrap"><div class="page-cover ${!snapshot.cover_url?'cover-'+esc(note.cover):''}">${pageCover(snapshot)}</div>${coverAttribution(snapshot)}</div>`:''}<div class="page-canvas"><div class="page-decoration"><span class="page-icon">${pageIcon(snapshot)}</span></div><h1 class="page-title-input">${esc(note.title)}</h1><div class="rich-editor" data-trash-document></div></div>`;
    this.handle=mountBlockEditor($('[data-trash-document]',screen),snapshot,{readOnly:true,getPage:id=>this.state.notes.find(n=>n.id===id),openPage:id=>this.open(id)});
    const back=async()=>{this.handle?.destroy();this.handle=null;await this.refresh();if(previous&&this.state.notes.some(n=>n.id===previous))await this.open(previous,false);else this.empty();};
    $('[data-trash-back]',screen).onclick=back;
    $('[data-trash-history]',screen).onclick=()=>this.trashHistory(note,{returnTrash:true});
    $('[data-trash-restore]',screen)?.addEventListener('click',async()=>{try{await this.ctx.api('/notes/'+note.id+'/restore','POST');await this.refresh();await this.open(note.id,false);}catch(error){this.ctx.toast(error.message);}});
    $('[data-trash-delete]',screen)?.addEventListener('click',async()=>{if(!confirm(tText("notes:deletePageAndSubpagesPermanently")))return;try{await this.ctx.api('/notes/'+note.id+'/permanent','DELETE');await back();}catch(error){this.ctx.toast(error.message);}});
    screen.scrollTop=0;if(this.narrowSidebar.matches)this.sidebar(false);this.tree();$('[data-trash-back]',screen).focus({preventScroll:true});
  }
  async trashHistory(note,{returnTrash=false}={}){
    if(this.historyView)return;
    const current=this.page?.id;
    await this.releaseLease(true);this.live?.destroy();this.live=null;this.handle?.destroy();this.handle=null;this.discussions?.destroy();clearInterval(this.readTimer);
    $('.notes-shell',this.host).hidden=true;
    this.historyView=mountHistory(this.host,this.ctx,note.id,async()=>{
      this.historyView?.destroy();this.historyView=null;if(this.disposed)return;
      $('.notes-shell',this.host).hidden=false;await this.refresh();
      if(returnTrash)await this.openTrash(note);else if(current&&this.state.notes.some(n=>n.id===current))await this.open(current,false);else this.empty();
    },{trashPage:note});
    $('.history-sidebar h1',this.host).textContent=note.title;
  }
  export() {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(
      new Blob(["# " + this.page.title + "\n\n" + this.page.body], {
        type: "text/markdown;charset=utf-8",
      }),
    );
    a.download =
      this.page.title.replace(/[^\p{L}\p{N} _-]/gu, "").slice(0, 100) + ".md";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  import() {
    const pageId=this.page.id;
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".md,.markdown,.zip,text/markdown,text/plain,application/zip";
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;
      try {
        if (/\.zip$/i.test(file.name)) {
          if(!(await this.flush())||this.disposed||this.page?.id!==pageId)return;
          return notionImportDialog(this.ctx,[file]);
        }
        if (file.size > 200000) return this.ctx.toast(tText("notes:fileIsLargerThan200Kb"));
        if (
          !confirm(
            tText("notes:replaceTheTextOfTheCurrentPageWith"),
          )
        )
          return;
        const text=await file.text();
        if(this.disposed||this.page?.id!==pageId)return;
        if(!(await this.flush())||this.disposed||this.page?.id!==pageId)return;
        this.handle.replaceMarkdown(text);
        this.sourceDirty=false;
        this.setMode("blocks");
        this.changed();
        if(await this.flush())this.ctx.toast(tText("notes:markdownImportedForAttachmentsAndFormattingInNotion"));
      } catch(error) {this.ctx.toast(error.message||tText("notes:failedToImportMarkdown"));}
    };
    input.click();
  }
  async createInlineSubpage(blockId) {
    try {
      if(!(await this.persistDraft())||!(await this.flush()))return;
      const parent=this.page.id,child=await this.createChild(parent,templates[0],subpagePlaceholder(this.handle.editor,blockId));if(!child)return;
      this.state.notes.push(child);this.expanded.add(parent);
      await this.open(child.id,false);
    }catch(error){this.ctx.toast(error.message);}
  }
}
