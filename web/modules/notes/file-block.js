import {tText} from '../../core/i18n.js';
import {downloadFile} from '../files/download.js';
import {chatIcon} from '../chat/icons.js';

// Keep the standard file schema, parser and export. Reading an attachment is
// independent of permission to edit the document or open its formatting toolbar.
export function downloadableFile(spec) {
  return {...spec,implementation:{...spec.implementation,render(block,editor){
    const view=spec.implementation.render.call(this,block,editor);
    if(!block.props.url)return view;
    const host=view.dom.matches('.bn-file-block-content-wrapper')?view.dom:view.dom.querySelector('.bn-file-block-content-wrapper');
    if(!host)return view;
    const controls=document.createElement('div'),button=document.createElement('button'),error=document.createElement('p');
    controls.className='note-file-actions';controls.contentEditable='false';
    button.type='button';button.className='note-file-download';button.dataset.noteFileDownload=block.id;
    button.setAttribute('aria-label',tText('notes:downloadNamedFile',{name:block.props.name||tText('notes:fileAttachment')}));
    const icon=document.createElement('span'),label=document.createElement('span');
    icon.innerHTML=chatIcon('download');label.textContent=tText('notes:downloadFile');button.append(icon,label);
    error.className='note-file-error';error.setAttribute('role','alert');error.hidden=true;
    controls.append(button,error);host.append(controls);let active=true;
    // Let the focused native button handle Enter/Space, without the document
    // editor treating them as commands for the selected file block.
    const buttonKey=event=>{if(event.key==='Enter'||event.key===' ')event.stopPropagation();};
    button.onkeydown=buttonKey;button.onkeyup=buttonKey;
    const showError=message=>{if(active){error.textContent=message;error.hidden=false;}};
    button.onclick=async event=>{
      event.preventDefault();event.stopPropagation();error.hidden=true;button.disabled=true;
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
    return {...view,
      stopEvent(event){return controls.contains(event.target)||view.stopEvent?.(event)||false;},
      destroy(){active=false;button.onclick=null;button.onkeydown=null;button.onkeyup=null;view.destroy?.();}
    };
  }}};
}
