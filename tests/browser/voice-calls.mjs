import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {installCallOutputFixture} from './call-output-fixture.js';
import {voiceCallsFlow} from './voice-calls-flow.js';
import {silenceTestAudio} from './silent-audio.js';
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
const reviewOut=process.env.DRG_CALL_REVIEW_OUT||'artifacts';
const origin='http://127.0.0.1:8001',directory=await mkdtemp(tmpdir()+'/drg-voice-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8001','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-voice-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser,page;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
 let ready=false;for(let i=0;i<100;i++){try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)throw Error(output);await new Promise(resolve=>setTimeout(resolve,100));}if(!ready)throw Error(output||'Server did not start');
 const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;browser=await engine.launch({headless:true,args:engine===webkit?[]:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true,...(engine===webkit?{}:{permissions:['microphone']})});
 await silenceTestAudio(context);await context.addInitScript(installCallOutputFixture);page=await context.newPage();await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
 async function post(path,data,csrf=''){const r=await context.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();}
 const password='Owner-password-123';let csrf=(await post('/setup',{login:'voiceowner',password,token:'browser-test-setup-key'})).csrf;const enrollment=await post('/2fa/enroll',{password},csrf);await post('/2fa/confirm',{code:totp(enrollment.secret)},csrf);csrf=(await post('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;await post('/admin/access/unlock',{password},csrf);
 await mkdir(reviewOut,{recursive:true});await page.goto('/');await voiceCallsFlow(page,browser,totp,reviewOut);console.log('Voice calls passed: real audio/video RTP, output/mute, desktop, phone fullscreen in every phase, narrow/landscape bounds, minimize/restore and hangup.');
}catch(error){await page?.screenshot({path:reviewOut+'/voice-call-failure.png'}).catch(()=>{});throw error;}
finally{await browser?.close();server.kill('SIGTERM');await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));await rm(directory,{recursive:true,force:true});}
