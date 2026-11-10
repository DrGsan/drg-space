import {appSection} from './notes-ui.js';
import {execFileSync} from 'node:child_process';
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

async function scenario(page){
 const errors=[];page.on('pageerror',e=>errors.push(e.message));const password='Outline-test-password-123';
 async function api(path,method='GET',data,csrf=''){const response=await page.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(response.ok(),path+' '+await response.text()).toBe(true);return response.json();}
 let csrf=(await api('/setup','POST',{login:'outlineowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password},csrf);await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
 const existing=await api('/notes','POST',{title:'Existing note',body:'Preserve original'},csrf);
 const parts=JSON.parse(execFileSync(process.env.DRG_PYTHON||'.venv/bin/python',['-c',String.raw`import io,json,base64
from zipfile import ZipFile
out=[]
for i in range(3):
 b=io.BytesIO()
 with ZipFile(b,'w') as z:
  z.writestr('Page'+str(i)+'.md','# Outline '+str(i)+'\n\n[File](attachments/file'+str(i)+'.txt)')
  z.writestr('attachments/file'+str(i)+'.txt','OUTLINE_ATTACHMENT_'+str(i))
 out.append(base64.b64encode(b.getvalue()).decode())
print(json.dumps(out))`],{encoding:'utf8'}));
 await page.goto('/');await appSection(page,'Настройки');await page.locator('#open-personal-data').click();await page.locator('[data-outline-import]').click();
 const form=page.locator('[data-import-source=outline]');await form.locator('[name=file]').setInputFiles(parts.map((value,i)=>({name:'outline-'+i+'.zip',mimeType:'application/zip',buffer:Buffer.from(value,'base64')})));
 await form.getByRole('button',{name:'Проверить архив',exact:true}).click();await expect(form.locator('[data-notion-preview]')).toContainText('Архив проверен');
 await expect(form.locator('[data-notion-preview]')).toContainText('Вложения');expect((await api('/notes')).length).toBe(1);
 await form.locator('[data-notion-import]').click();await expect(page.locator('[data-notion-preview]')).toContainText('Импорт завершён',{timeout:15000});
 const notes=await api('/notes');expect(notes).toHaveLength(4);expect((await api('/notes/'+existing.id)).body).toBe('Preserve original');expect(notes.every(n=>!n.space_id)).toBe(true);
 await page.screenshot({path:'artifacts/outline-import.png'});expect(errors).toEqual([]);
}
const origin='http://127.0.0.1:8039',directory=await mkdtemp(tmpdir()+'/drg-outline-import-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8039','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Outline import passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
