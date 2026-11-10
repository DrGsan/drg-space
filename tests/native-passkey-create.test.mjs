import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs/promises';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const {outputFiles}=await build({entryPoints:[fileURLToPath(new URL('../web/native-passkey-create.js',import.meta.url))],bundle:true,write:false,format:'iife',platform:'browser',define:{'process.env.NODE_ENV':'"production"'}});
const script=outputFiles[0].text;
const encode=data=>Buffer.from(data).toString('base64url');
const ceremony={state:'S'.repeat(43),options:{rp:{id:'native.test'},user:{id:encode('user'),name:'user'},challenge:encode('challenge'),excludeCredentials:[{id:encode('old-key'),type:'public-key'}],authenticatorSelection:{userVerification:'required'}}};
function page(value=ceremony,{fail=false}={}){
 const elements={'#continue':{disabled:false},'#status':{textContent:''},'#server':{textContent:''}},calls=[];let redirected,cleared;
 const context={document:{querySelector:key=>elements[key],querySelectorAll:()=>[]},location:{host:'native.test',hostname:'native.test',pathname:'/native/passkey-create',hash:'#'+encode(JSON.stringify(value)),replace:url=>{redirected=url;}},history:{replaceState:(state,title,url)=>{cleared=url;}},window:{isSecureContext:true},navigator:{credentials:{create:async request=>{calls.push(request);if(fail)throw Object.assign(new Error('cancelled'),{name:'NotAllowedError'});return {id:encode('new-key'),rawId:new TextEncoder().encode('new-key').buffer,type:'public-key',response:{clientDataJSON:new TextEncoder().encode('client').buffer,attestationObject:new TextEncoder().encode('attestation').buffer,getTransports:()=>['internal']},getClientExtensionResults:()=>({})};}}},URL,TextDecoder,TextEncoder,atob:input=>Buffer.from(input,'base64').toString('binary'),btoa:input=>Buffer.from(input,'binary').toString('base64')};
 vm.runInNewContext(script,context);return {elements,calls,get redirected(){return redirected;},get cleared(){return cleared;}};
}
test('native enrollment clears fragment and hands back only public credential with bound state',async()=>{
 const p=page();assert.equal(p.cleared,'/native/passkey-create');await p.elements['#continue'].onclick();assert.equal(p.calls.length,1);assert.deepEqual(Array.from(p.calls[0].publicKey.challenge),Array.from(new TextEncoder().encode('challenge')));assert.deepEqual(Array.from(p.calls[0].publicKey.user.id),Array.from(new TextEncoder().encode('user')));assert.deepEqual(Array.from(p.calls[0].publicKey.excludeCredentials[0].id),Array.from(new TextEncoder().encode('old-key')));
 const callback=new URL(p.redirected);assert.equal(callback.protocol,'drg-family:');assert.equal(callback.hostname,'passkey-created');assert.equal(callback.searchParams.get('state'),ceremony.state);assert.deepEqual([...callback.searchParams.keys()].sort(),['credential','state']);const proof=JSON.parse(Buffer.from(callback.searchParams.get('credential'),'base64url'));assert.equal(proof.id,encode('new-key'));assert.equal(proof.id,proof.rawId);assert.equal(proof.response.attestationObject,encode('attestation'));assert.ok(!p.redirected.includes('password'));assert.ok(!p.redirected.includes('session'));
});
test('foreign RP, invalid state and malformed options cannot trigger creation',async()=>{
 for(const changed of [{...ceremony,state:'bad'},{...ceremony,options:{...ceremony.options,rp:{id:'foreign.test'}}},{...ceremony,options:{}}]){const p=page(changed);assert.equal(p.elements['#continue'].disabled,true);await p.elements['#continue'].onclick();assert.equal(p.calls.length,0);assert.equal(p.redirected,undefined);}
});
test('cancelled system ceremony returns no proof and allows an explicit retry',async()=>{const p=page(ceremony,{fail:true});await p.elements['#continue'].onclick();assert.equal(p.redirected,undefined);assert.equal(p.elements['#continue'].disabled,false);assert.match(p.elements['#status'].textContent,/отменено/);});
