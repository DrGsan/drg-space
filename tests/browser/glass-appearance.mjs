// Independent encrypted server, so this scenario never changes the normal
// workspace suite's one-time setup or a developer's running application.
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {glassAppearanceFlow} from './glass-appearance-flow.js';
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const origin='http://127.0.0.1:8052',directory=await mkdtemp(tmpdir()+'/drg-glass-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8052','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-glass-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
const exited=new Promise(resolve=>server.once('exit',resolve));
try{
  let ready=false;
  for(let attempt=0;attempt<150;attempt++){
    try{ready=(await fetch(origin+'/api/health')).ok;}catch{}
    if(ready)break;if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,200));
  }
  if(!ready)throw Error('Isolated glass server did not start: '+output);
  browser=await ({chromium,webkit}[engine]).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1000},colorScheme:'light',timezoneId:'Europe/Moscow'});
  const page=await context.newPage();
  let csrf='';
  const send=async(path,body,method='POST')=>{
    const r=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});
    if(!r.ok())throw Error(path+' '+r.status()+' '+await r.text());return r.json();
  };
  csrf=(await send('/setup',{login:'demoowner',password:'Glass-test-password-123',token:'browser-test-setup-key'})).csrf;
  const {secret}=await send('/2fa/enroll',{password:'Glass-test-password-123'});
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join('');
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
  await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});
  csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;
  for(const title of ['План на неделю','Идеи для дома','Список покупок','Путешествия'])await send('/notes',{title,body:'# '+title+'\n\nЗдесь всё важное под рукой.'});
  for(const title of ['Купить продукты','Оплатить интернет','План на неделю'])await send('/tasks',{title,day:new Date().toLocaleDateString('en-CA',{timeZone:'Europe/Moscow'})});
  const layout=await send('/dashboard/layout',undefined,'GET');
  const specs=[['tasks','2x1',{}],['notes','2x1',{}],['calendar','1x1',{}],['weather','1x1',{city:'Москва',latitude:55.75,longitude:37.62}],['bookmarks','1x1',{links:[{title:'GitHub',url:'https://github.com'},{title:'Почта',url:'https://mail.google.com'}]}],['expenses','1x1',{}]];
  await send('/dashboard/layout',{version:layout.version,instances:specs.map(([module_id,size,config])=>({id:'preview_'+module_id,module_id,size,config}))},'PUT');
  await glassAppearanceFlow(page,{engine,origin});

}finally{
  await browser?.close();
  server.kill('SIGTERM');await exited;
  await rm(directory,{recursive:true,force:true});
}
