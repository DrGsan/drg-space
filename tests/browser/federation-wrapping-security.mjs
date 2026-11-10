// Independent adversarial storage checks. All keys are disposable local fixtures.
import assert from 'node:assert/strict';
import {chromium,webkit} from '@playwright/test';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../../web/modules/federation/client.js',import.meta.url));
const server=createServer((request,response)=>{response.setHeader('Content-Type',request.url==='/client.js'?'text/javascript':'text/html');response.end(request.url==='/client.js'?source:'<!doctype html><title>Disposable wrapping security fixture</title>');});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin='http://127.0.0.1:'+server.address().port,engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const browser=await ({chromium,webkit}[engine]).launch({headless:true});
try{
  const page=await browser.newPage({locale:'ru-RU'});await page.goto(origin);
  const result=await page.evaluate(async engine=>{
    const client=await import('/client.js'),uid='c'.repeat(32),other='d'.repeat(32),serverId='a'.repeat(32);
    const descriptor={server_id:serverId,origin:'https://fixture.example',public_key:'transport',key_id:'transport-id',result_public_key:'result',result_key_id:'result-id'};
    let enrollments=0;const known=[],generated=[],unwrapFlags=[];
    const subtle=crypto.subtle,generate=subtle.generateKey.bind(subtle),unwrap=subtle.unwrapKey.bind(subtle),exportKey=subtle.exportKey.bind(subtle);let privateExports=0;
    subtle.generateKey=async(...args)=>{generated.push({name:typeof args[0]==='string'?args[0]:args[0].name,extractable:args[1]});return generate(...args);};
    subtle.unwrapKey=async(...args)=>{unwrapFlags.push(args[5]);return unwrap(...args);};
    subtle.exportKey=async(...args)=>{if(args[1].type==='private')privateExports++;return exportKey(...args);};
    const ctx={state:{me:{id:uid}},api:async(path,method,payload)=>{
      if(path==='/federation/info')return {enabled:true,descriptor,devices:known};
      if(path==='/federation/devices/challenge')return {nonce:'e'.repeat(64)};
      if(path==='/federation/devices'){
        if(Object.keys(payload).sort().join(',')!=='card,nonce,proof')throw Error('Unexpected private enrollment field');
        enrollments++;known.push({...payload.card,active:true});return {};
      }
      throw Error('Unexpected fixture request');
    }};
    const device=await client.ensureDevice(ctx);
    subtle.generateKey=generate;subtle.unwrapKey=unwrap;subtle.exportKey=exportKey;
    const db=await new Promise((resolve,reject)=>{const request=indexedDB.open('drg-federation-devices');request.onsuccess=()=>resolve(request.result);request.onerror=reject;});
    const read=scope=>new Promise((resolve,reject)=>{const request=db.transaction('devices').objectStore('devices').get(scope);request.onsuccess=()=>resolve(request.result);request.onerror=reject;});
    const put=value=>new Promise((resolve,reject)=>{const tx=db.transaction('devices','readwrite');tx.objectStore('devices').put(value);tx.oncomplete=resolve;tx.onerror=reject;tx.onabort=reject;});
    const scope=location.origin+'\n'+uid,original=await read(scope),failures=[];
    function check(condition,label){if(!condition)failures.push(label);}
    const containsX=value=>value instanceof CryptoKey?value.algorithm.name==='X25519':value&&typeof value==='object'?Object.values(value).some(containsX):false;
    check(!containsX(original)&&!('encryption' in original),'persisted X25519 handle');
    check(original.wrappingKey.extractable===false&&original.wrappingKey.algorithm.length===256,'AES key protection');
    check(original.wrappingKey.usages.slice().sort().join(',')==='unwrapKey,wrapKey','AES exact usages');
    check(!device.encryption.extractable&&!device.signing.extractable,'returned key extractability');
    check(generated.some(x=>x.name==='X25519'&&x.extractable===true)&&generated.filter(x=>x.name!=='X25519').every(x=>x.extractable===false),'temporary extraction policy');
    check(privateExports===0&&unwrapFlags.length>0&&unwrapFlags.every(value=>value===false),'application private export/unwrap policy');
    let rejected=0;
    const cases=[
      ['ciphertext',r=>{r.wrappedEncryption.body=r.wrappedEncryption.body.slice(0);new Uint8Array(r.wrappedEncryption.body)[0]^=1;}],
      ['nonce',r=>{r.wrappedEncryption.nonce=new Uint8Array(r.wrappedEncryption.nonce);r.wrappedEncryption.nonce[0]^=1;}],
      ['nonce length',r=>{r.wrappedEncryption.nonce=new Uint8Array(11);}],
      ['version',r=>{r.wrappedEncryption.version=2;}],
      ['card actor',r=>{r.card.actor_id=other;}],
      ['card server',r=>{r.card.server_id='b'.repeat(32);} ],
      ['server identity',r=>{r.serverId='b'.repeat(32);} ],
      ['card fingerprint',r=>{r.card.device_id='f'.repeat(64);} ],
      ['card signing key',r=>{r.card.signing_key='A'.repeat(43);} ],
      ['card encryption key',r=>{r.card.encryption_key='A'.repeat(43);} ],
      ['format downgrade',r=>{delete r.wrappedEncryption;}],
      ['missing signing key',r=>{delete r.signing;}],
      ['oversize ciphertext',r=>{r.wrappedEncryption.body=new ArrayBuffer(1025);}]
    ];
    const initialEnrollments=enrollments;
    for(const [name,mutate] of cases){
      const row={...original,card:{...original.card},wrappedEncryption:{...original.wrappedEncryption}};mutate(row);await put(row);
      let denied=false;try{await client.ensureDevice(ctx);}catch{denied=true;}
      check(denied,name+' accepted');check(enrollments===initialEnrollments,name+' enrolled/replaced key');
      const retained=await read(scope);check(retained.card.device_id===row.card.device_id&&!!retained.wrappedEncryption===!!row.wrappedEncryption,name+' silently rewrote storage');
      if(denied)rejected++;await put(original);
    }
    // Changing the real account scope while copying the entire AES/cipher record
    // still fails authenticated unwrapping and cannot create a substitute key.
    await put({...original,scope:location.origin+'\n'+other,card:{...original.card,actor_id:other}});ctx.state.me={id:other};
    let accountDenied=false;try{await client.ensureDevice(ctx);}catch{accountDenied=true;}check(accountDenied&&enrollments===initialEnrollments,'cross-account clone');ctx.state.me={id:uid};
    // The wrapping key is unchanged; every authenticated binding must reject.
    const aad=value=>new TextEncoder().encode(client.canonical({purpose:'drg/federation/device-key/v1',scope:value.scope,server_id:value.serverId,card:value.card}));
    for(const altered of [{...original,scope:'https://different-origin.example\n'+uid},{...original,scope:location.origin+'\n'+other},{...original,serverId:'b'.repeat(32)},{...original,card:{...original.card,device_id:'f'.repeat(64)}}]){
      let denied=false;try{await unwrap('pkcs8',original.wrappedEncryption.body,original.wrappingKey,{name:'AES-GCM',iv:original.wrappedEncryption.nonce,additionalData:aad(altered)},'X25519',false,['deriveBits']);}catch{denied=true;}check(denied,'AAD binding accepted');
    }
    let legacyPreserved=null,extractableLegacyDenied=null;
    if(engine==='chromium'){
      const legacy={...original,encryption:device.encryption};delete legacy.wrappedEncryption;delete legacy.wrappingKey;await put(legacy);
      const restored=await client.ensureDevice(ctx);legacyPreserved=restored.card.device_id===device.card.device_id&&!restored.encryption.extractable&&!(await read(scope)).wrappedEncryption;
      const exportable=await unwrap('pkcs8',original.wrappedEncryption.body,original.wrappingKey,{name:'AES-GCM',iv:original.wrappedEncryption.nonce,additionalData:aad(original)},'X25519',true,['deriveBits']);
      await put({...legacy,encryption:exportable});extractableLegacyDenied=false;try{await client.ensureDevice(ctx);}catch{extractableLegacyDenied=true;}check(legacyPreserved&&extractableLegacyDenied,'legacy validation');await put(original);
    }
    // Document the EXCLUDED same-origin-XSS threat honestly. An attacker that can
    // execute script and access IDB can choose true when unwrapping. This test
    // exports only this disposable fixture key and returns no plaintext bytes.
    const malicious=await unwrap('pkcs8',original.wrappedEncryption.body,original.wrappingKey,{name:'AES-GCM',iv:original.wrappedEncryption.nonce,additionalData:aad(original)},'X25519',true,['deriveBits']);
    const fixtureBytes=await exportKey('pkcs8',malicious),excludedXssCanExport=fixtureBytes.byteLength>0;new Uint8Array(fixtureBytes).fill(0);
    check(excludedXssCanExport,'XSS tradeoff unexpectedly untested');db.close();
    return {failures,rejectedTamperCases:rejected,accountDenied,legacyPreserved,extractableLegacyDenied,privateExports,allApplicationUnwrapsNonextractable:unwrapFlags.every(value=>value===false),excludedXssCanExport};
  },engine);
  assert.deepEqual(result.failures,[],JSON.stringify(result));
  console.log(JSON.stringify({engine,...result}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
