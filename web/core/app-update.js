import {tText} from './i18n.js';

const checks=new WeakMap();

export async function showAppUpdate(host,me,api){
 if(!host||!['owner','admin'].includes(me.role))return;
 try{
  let cached=checks.get(me);
  if(!cached||cached.until<Date.now()){cached={until:Date.now()+60000,result:api('/app-update')};checks.set(me,cached);}
  const result=await cached.result;
  if(!host.isConnected||!result.available||typeof result.version!=='string'||!/^\d+\.\d+\.\d+$/.test(result.version)||
   !['https://github.com/DrGsan/drg-space/releases/tag/v'+result.version,'https://github.com/DrGsan/drg-space/releases/tag/'+result.version].includes(result.url))return;
  const link=document.createElement('a');link.href=result.url;link.target='_blank';link.rel='noopener noreferrer';
  link.textContent=tText('app:updateAvailable',{version:result.version});host.replaceChildren(link);host.hidden=false;
 }catch{/* Unavailable metadata leaves the installed version readable. */}
}
