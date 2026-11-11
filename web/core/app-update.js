import {tText} from './i18n.js';

export async function showAppUpdate(host,me,api){
 if(!host||!['owner','admin'].includes(me.role))return;
 try{
  const result=await api('/app-update');
  if(!host.isConnected||!result.available||typeof result.version!=='string'||!/^\d+\.\d+\.\d+$/.test(result.version)||
   !['https://github.com/DrGsan/drg-space/releases/tag/v'+result.version,'https://github.com/DrGsan/drg-space/releases/tag/'+result.version].includes(result.url))return;
  const link=document.createElement('a');link.href=result.url;link.target='_blank';link.rel='noopener noreferrer';
  link.textContent=tText('app:updateAvailable',{version:result.version});host.replaceChildren(link);host.hidden=false;
 }catch{/* Unavailable metadata leaves the installed version readable. */}
}
