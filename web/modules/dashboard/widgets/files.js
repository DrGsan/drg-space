import {tText,tHtml} from "../../../core/i18n.js";
import {esc} from '../../../core/ui.js';
import {bytes} from '../../files/index.js';
import {widgetSize} from '../sizes.js';
export async function mountFilesWidget(host,ctx){
  const data=await ctx.api('/files');if(!host.isConnected)return;
  const [columns,rows]=widgetSize(ctx.instance.size).split('x').map(Number),limit=Math.min(48,6*columns*rows);
  host.innerHTML=`<p class=hint>${bytes(data.quota.used_bytes)} · ${data.quota.limit_bytes===null?tText("dashboard:unlimited"):bytes(data.quota.limit_bytes)}</p><div class="widget-file-list">${data.items.slice(0,limit).map(f=>'<button class=recent-note data-file-home="'+f.id+'"><span>'+(f.kind==='folder'?'📁':'📄')+'</span><strong>'+esc(f.name)+'</strong></button>').join('')}</div><button class=text-link data-files-all>${tHtml("dashboard:allFiles")}</button>`;
  host.querySelectorAll('[data-file-home]').forEach(b=>b.onclick=()=>{const f=data.items.find(x=>x.id===b.dataset.fileHome);ctx.state.fileTrash=false;ctx.state.fileView='active';ctx.state.fileFolder=f.kind==='folder'?f.id:null;ctx.navigate('files');});
  host.querySelector('[data-files-all]').onclick=()=>{ctx.state.fileTrash=false;ctx.state.fileView='active';ctx.navigate('files');};
}
