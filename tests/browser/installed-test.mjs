import {test as base,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';

// Every default spec performs initial setup, so sharing one installation makes
// the second spec fail with 409. Keep real transport and give each test its own
// supervised, disposable installation instead of adding a reset API.
export const test=base.extend({
  installation:[async({},use,info)=>{
    const origin='http://127.0.0.1:8001';
    try {
      await fetch(origin+'/api/health',{signal:AbortSignal.timeout(500)});
      throw new Error('Port 8001 is already in use; run this suite with one worker.');
    } catch(error) {if(error.message.startsWith('Port 8001'))throw error;}
    const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','tests.browser_server'],{
      env:{...process.env,DRG_BROWSER_PORT:'8001'},detached:true,stdio:['ignore','pipe','pipe'],
    });
    let logs='',spawnError;
    const running=()=>server.exitCode===null&&server.signalCode===null;
    server.stdout.on('data',data=>logs+=data);server.stderr.on('data',data=>logs+=data);
    const exited=new Promise(resolve=>{server.once('exit',resolve);server.once('error',error=>{spawnError=error;resolve();});});
    try {
      let ready=false;
      for(let attempt=0;attempt<300;attempt++){
        if(spawnError)throw spawnError;
        if(!running())throw new Error(logs||'Browser fixture stopped before startup.');
        try {ready=(await fetch(origin+'/api/health',{signal:AbortSignal.timeout(500)})).ok;}catch{}
        if(ready)break;
        await new Promise(resolve=>setTimeout(resolve,100));
      }
      if(!ready)throw new Error('Browser fixture did not become ready.\n'+logs);
      await use();
    } finally {
      if(server.pid&&running()){
        try{process.kill(-server.pid,'SIGTERM');}catch(error){if(error.code!=='ESRCH')throw error;}
        let timeout;
        await Promise.race([exited,new Promise(resolve=>timeout=setTimeout(resolve,70000))]);clearTimeout(timeout);
        if(running()){try{process.kill(-server.pid,'SIGKILL');}catch(error){if(error.code!=='ESRCH')throw error;}await exited;}
      }
      const path=info.outputPath('server.log');await mkdir(dirname(path),{recursive:true});await writeFile(path,logs);
    }
  },{auto:true,timeout:90000}],
});
export {expect};
