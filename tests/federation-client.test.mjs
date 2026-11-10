import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {canonical,signIntent,openResult,assertIntent,verifyCallEvidence,contactFingerprint,assetBlobMime} from '../web/modules/federation/client.js';

const root=fileURLToPath(new URL('../',import.meta.url));
function python(code,value){const result=spawnSync(root+'.venv/bin/python',['-c',code],{cwd:root,input:JSON.stringify(value),encoding:'utf8'});assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);}
const raw64=value=>Buffer.from(value).toString('base64url');
async function device(){
  const signing=await crypto.subtle.generateKey('Ed25519',false,['sign','verify']),encryption=await crypto.subtle.generateKey('X25519',false,['deriveBits']);
  const sign=await crypto.subtle.exportKey('raw',signing.publicKey),box=await crypto.subtle.exportKey('raw',encryption.publicKey);
  const joined=Buffer.concat([Buffer.from(sign),Buffer.from(box)]),fingerprint=Buffer.from(await crypto.subtle.digest('SHA-256',joined)).toString('hex');
  return {serverId:'a'.repeat(32),signing:signing.privateKey,encryption:encryption.privateKey,card:{server_id:'a'.repeat(32),actor_id:'c'.repeat(32),device_id:fingerprint,signing_key:raw64(sign),encryption_key:raw64(box)}};
}
const responseCode=`
import json,sys,time,hashlib,jwt
from app.federation.actors import DeviceCard,canonical,seal_result,sign_result
from app.federation.identity import SigningIdentity
value=json.load(sys.stdin)
owner=SigningIdentity.generate('https://owner.example')
card=DeviceCard.model_validate(value['card'])
intent=value['intent']
body=seal_result(card,{'body':'PRIVATE_FEDERATED_DOCUMENT','count':7},intent)
envelope=sign_result(owner,card,intent,body,int(time.time()))
if value.get('forge_transport'):
    claims=jwt.decode(envelope['result_proof'],options={'verify_signature':False})
    envelope['result_proof']=jwt.encode(claims,owner.private_key,algorithm='EdDSA',headers={'typ':'drg-federation-result+jwt','kid':owner.descriptor.result_key_id})
print(json.dumps({'owner':owner.descriptor.model_dump(mode='json'),'envelope':envelope}))
`;

test('sealed attachments preserve the ordinary file endpoint passive-content MIME policy',()=>{
  for(const mime of ['text/html','image/svg+xml','application/javascript','text/javascript','application/pdf',''])assert.equal(assetBlobMime(mime),'application/octet-stream');
  for(const mime of ['image/png','image/webp','video/mp4','audio/ogg'])assert.equal(assetBlobMime(mime),mime);
});

test('canonical protected JSON matches Python, including unicode ordering and exponent formatting',()=>{
  const value={'😀':true,'\ue000':false,message:'Русский текст 🎵',small:0.000012,smaller:1e-7,large:1.2e25,integer:100000000000000000000,nested:[null,{z:3,a:'x'}]};
  const expected=python("import json,sys; print(json.dumps({'canonical':json.dumps(json.load(sys.stdin),ensure_ascii=False,sort_keys=True,separators=(',',':'))}))",value).canonical;
  assert.equal(canonical(value),expected);
});

test('one independently compared contact fingerprint covers the result key, TLS pin, device and origin',async()=>{
  const value={version:1,server:{server_id:'a'.repeat(32),origin:'https://friend.example',key_id:'transport-key',public_key:'transport',result_key_id:'result-key',result_public_key:'result'},transport:{certificate_pem:'public certificate',fingerprint:'b'.repeat(64)},actor:{device_id:'c'.repeat(64),server_id:'a'.repeat(32),actor_id:'d'.repeat(32),signing_key:'sign',encryption_key:'encrypt'},profile:{display_name:'Friend'}};
  const fingerprint=await contactFingerprint(value);assert.match(fingerprint,/^[a-f0-9]{64}$/);
  for(const changed of [{...value,server:{...value.server,result_public_key:'attacker'}},{...value,server:{...value.server,origin:'https://attacker.example'}},{...value,transport:{...value.transport,fingerprint:'e'.repeat(64)}},{...value,actor:{...value.actor,encryption_key:'attacker'}}])assert.notEqual(await contactFingerprint(changed),fingerprint);
  assert.equal(await contactFingerprint({...value,profile:{display_name:'New display name'}}),fingerprint);
});

test('nonextractable browser device signs an intent accepted by Python',async()=>{
  const own=await device(),intent={operation:'read',payload:{note_id:'opaque-note',revision:1},nonce:'f'.repeat(64)},now=Math.floor(Date.now()/1000);
  assert.equal(own.signing.extractable,false);assert.equal(own.encryption.extractable,false);
  await assert.rejects(()=>crypto.subtle.exportKey('pkcs8',own.signing));
  const proof=await signIntent(own,'b'.repeat(32),intent,now);
  const value=python("import json,sys; from app.federation.actors import DeviceCard,verify_device; v=json.load(sys.stdin); verify_device(DeviceCard.model_validate(v['card']),v['recipient'],v['intent'],v['proof'],v['now']); print('{\"verified\":true}')",{card:own.card,recipient:'b'.repeat(32),intent,proof,now});
  assert.equal(value.verified,true);
});

test('browser verifies independent owner result key and decrypts Python sealed response',async()=>{
  const own=await device(),intent={operation:'read',nonce:'f'.repeat(64),payload:{note_id:'n'.repeat(16),revision:1}};
  const {owner,envelope}=python(responseCode,{card:own.card,intent});
  assert.equal(JSON.stringify(envelope).includes('PRIVATE_FEDERATED_DOCUMENT'),false);
  assert.deepEqual(await openResult(own,envelope,intent,owner),{body:'PRIVATE_FEDERATED_DOCUMENT',count:7});
  await assert.rejects(()=>openResult(own,{...envelope,ciphertext:envelope.ciphertext.slice(0,-4)+'AAAA'},intent,owner));
  await assert.rejects(()=>openResult(own,envelope,{...intent,operation:'update'},owner));
  const other=await device();await assert.rejects(()=>openResult(other,envelope,intent,owner));
});

test('stolen server transport signing key cannot forge a sealed result signature',async()=>{
  const own=await device(),intent={operation:'read',nonce:'f'.repeat(64),payload:{note_id:'n'.repeat(16),revision:1}};
  const {owner,envelope}=python(responseCode,{card:own.card,intent,forge_transport:true});
  await assert.rejects(()=>openResult(own,envelope,intent,owner),/Подпись/);
});

test('home server cannot substitute actor, operation, or resource before device signing',()=>{
  const expected={actor_server_id:'a'.repeat(32),actor_id:'b'.repeat(32),device_id:'c'.repeat(64),target_actor_id:'d'.repeat(32),operation:'read',payload:{note_id:'n'.repeat(16),revision:1}};
  const intent={...expected,nonce:'e'.repeat(64),event_id:'f'.repeat(32)};
  assert.doesNotThrow(()=>assertIntent(intent,expected));
  for(const changed of [{operation:'update'},{actor_id:'e'.repeat(32)},{target_actor_id:'e'.repeat(32)},{payload:{note_id:'different',revision:1}},{unexpected:true}])assert.throws(()=>assertIntent({...intent,...changed},expected));
});

test('call SDP, ICE, incoming ring and media require the pinned counterpart device signature',async()=>{
  const peer=await device(),home='b'.repeat(32),callee='d'.repeat(32),id='91c45f6c-10ad-44c8-82fb-59c6648aed13',now=Math.floor(Date.now()/1000);
  const payload={id,device:'runtime-device',sdp:'v=0\r\na=fingerprint:sha-256 PINNED_MEDIA_CERT\r\n',candidates:[{candidate:'candidate:1 1 udp 123 192.0.2.1 45123 typ relay',sdpMid:'0',sdpMLineIndex:0}],media:{muted:true}};
  const intent={actor_server_id:peer.card.server_id,actor_id:peer.card.actor_id,device_id:peer.card.device_id,target_actor_id:callee,nonce:'e'.repeat(64),event_id:'f'.repeat(32),operation:'call_invite',payload};
  const proof=await signIntent(peer,home,intent,now),cards={[peer.card.device_id]:peer.card};
  const call={id,status:'active',outgoing:false,owned:true,sdp:payload.sdp,candidates:payload.candidates,media:{muted:true,camera:false,screen:false},federation:{home_server_id:home,home_actor_id:callee,peer_id:peer.card.server_id,actor_id:peer.card.actor_id},peer_actions:[{intent,proof}]};
  const cache=new Set();await verifyCallEvidence(call,cards,cache,now);
  // Only proofs actually verified while fresh may be retained for this call.
  await verifyCallEvidence(call,cards,cache,now+121);
  await assert.rejects(()=>verifyCallEvidence(call,cards,new Set(),now+121));
  await assert.rejects(()=>verifyCallEvidence({...call,sdp:payload.sdp.replace('PINNED','ATTACKER')},cards));
  await assert.rejects(()=>verifyCallEvidence({...call,candidates:[...payload.candidates,{candidate:'candidate:2 1 udp 123 203.0.113.5 4444 typ host'}]},cards));
  await assert.rejects(()=>verifyCallEvidence({...call,media:{muted:false}},cards));
  await assert.rejects(()=>verifyCallEvidence({...call,id:'different-call'},cards));
  const ring={...call,status:'ringing',owned:false,sdp:undefined,candidates:undefined,media:undefined};await verifyCallEvidence(ring,cards);
  await assert.rejects(()=>verifyCallEvidence({...ring,peer_actions:[]},cards));
  const stranger=await device();await assert.rejects(()=>verifyCallEvidence(call,{[stranger.card.device_id]:stranger.card}));
  const transport=await crypto.subtle.generateKey('Ed25519',false,['sign','verify']),fake=await signIntent({...peer,signing:transport.privateKey},home,intent,now);
  await assert.rejects(()=>verifyCallEvidence({...call,peer_actions:[{intent,proof:fake}]},cards));
});

test('callee acceptance precedes the answer and pins one signed runtime and device for the call',async()=>{
  const peer=await device(),second=await device(),home=peer.card.server_id,actor=peer.card.actor_id,id='accepted-call',now=Math.floor(Date.now()/1000),cards={[peer.card.device_id]:peer.card,[second.card.device_id]:second.card};
  async function action(device,operation,payload){const intent={actor_server_id:device.card.server_id,actor_id:device.card.actor_id,device_id:device.card.device_id,target_actor_id:actor,nonce:'e'.repeat(64),event_id:'f'.repeat(32),operation,payload:{id,device:'callee-runtime',...payload}};return {intent,proof:await signIntent(device,home,intent,now)};}
  const accepted=await action(peer,'call_accept',{}),answer=await action(peer,'call_signal',{sdp:'v=0\r\na=fingerprint:sha-256 ACCEPTED_CALLEE\r\n'}),cache=new Set();
  const call={id,status:'connecting',outgoing:true,owned:true,federation:{home_server_id:home,home_actor_id:actor,peer_id:home,actor_id:actor},peer_actions:[accepted]};
  await verifyCallEvidence(call,cards,cache);await verifyCallEvidence({...call,sdp:answer.intent.payload.sdp,peer_actions:[accepted,answer]},cards,cache);
  const otherRuntime=await action(peer,'call_signal',{device:'other-runtime',sdp:answer.intent.payload.sdp});await assert.rejects(()=>verifyCallEvidence({...call,sdp:answer.intent.payload.sdp,peer_actions:[otherRuntime]},cards,cache),/изменилось/);
  const otherDevice=await action(second,'call_signal',{sdp:answer.intent.payload.sdp});await assert.rejects(()=>verifyCallEvidence({...call,sdp:answer.intent.payload.sdp,peer_actions:[otherDevice]},cards,cache),/изменилось/);
});
