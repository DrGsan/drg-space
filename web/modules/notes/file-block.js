import {tText} from '../../core/i18n.js';
import {downloadFile} from '../files/download.js';
import {pageMenuIcon,showPageMenu} from './page-menu.js';
import './page-menu.css';

let closeActiveFileMenu;

// Keep the standard file schema, parser and export. Reading an attachment is
// independent of permission to edit the document or open its formatting toolbar.
export function downloadableFile(spec) {
  return {...spec,implementation:{...spec.implementation,render(block,editor){
    const view=spec.implementation.render.call(this,block,editor);
    if(!block.props.url)return view;
    const host=view.dom.matches('.bn-file-block-content-wrapper')?view.dom:view.dom.querySelector('.bn-file-block-content-wrapper');
    if(!host)return view;
    const row=host.querySelector('.bn-file-name-with-icon');
    if(!row)return view;
    row.classList.add('note-file-row');
    const button=document.createElement('button'),error=document.createElement('p');
    button.type='button';button.className='note-file-menu-trigger';button.dataset.noteFileMenu=block.id;
    const label=tText('notes:fileActions',{name:block.props.name||tText('notes:fileAttachment')});
    button.setAttribute('aria-label',label);button.title=label;
    button.setAttribute('aria-haspopup','menu');button.setAttribute('aria-expanded','false');
    button.contentEditable='false';button.innerHTML=pageMenuIcon('dots');row.append(button);
    error.className='note-file-error';error.setAttribute('role','alert');error.contentEditable='false';error.hidden=true;host.append(error);
    let active=true,closeMenu;
    const buttonKey=event=>{
      if(['Enter',' '].includes(event.key))event.stopPropagation();
      if(event.type==='keydown'&&event.key==='ArrowDown'){event.preventDefault();event.stopPropagation();if(!closeMenu)button.click();}
    };
    button.onkeydown=buttonKey;button.onkeyup=buttonKey;
    const showError=message=>{if(active){error.textContent=message;error.hidden=false;}};
    const startDownload=async()=>{
      error.hidden=true;button.disabled=true;
      try{
        const resolved=editor.resolveFileUrl?await editor.resolveFileUrl(block.props.url):block.props.url;
        if(typeof resolved!=='string'||!resolved)throw new Error(tText('notes:failedToLoadFile'));
        const target=new URL(resolved,location.href);
        if(target.username||target.password||!['http:','https:','blob:'].includes(target.protocol)||target.protocol==='blob:'&&target.origin!==location.origin)throw new Error(tText('notes:failedToLoadFile'));
        if(!active)return;
        if(target.origin===location.origin&&target.protocol!=='blob:'){
          await downloadFile(target.pathname+target.search,{toast:showError,canDownload:()=>active});
        }else{
          const link=document.createElement('a');link.href=target.href;link.download=block.props.name||'download';link.rel='noopener noreferrer';
          if(target.protocol!=='blob:')link.target='_blank';
          document.body.append(link);link.click();link.remove();
        }
      }catch{showError(tText('notes:failedToLoadFile'));}
      finally{button.disabled=false;}
    };
    button.onclick=event=>{
      event.preventDefault();event.stopPropagation();
      if(closeMenu){closeMenu();return;}
      closeActiveFileMenu?.(false);
      let close;
      close=showPageMenu({anchor:button,compact:true,label,
        actions:[{name:'download',label:tText('notes:downloadFile'),icon:'export',group:'file'}],
        onAction:()=>{void startDownload();},
        onClose:()=>{closeMenu=null;if(closeActiveFileMenu===close)closeActiveFileMenu=null;}
      });
      closeMenu=close;closeActiveFileMenu=close;
    };
    return {...view,
      stopEvent(event){return button.contains(event.target)||error.contains(event.target)||view.stopEvent?.(event)||false;},
      destroy(){active=false;closeMenu?.(false);button.onclick=null;button.onkeydown=null;button.onkeyup=null;view.destroy?.();}
    };
  }}};
}
