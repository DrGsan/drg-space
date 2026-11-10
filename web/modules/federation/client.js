import {tText} from "../../core/i18n.js";
// Device keys never leave IndexedDB/WebCrypto. Only public contact cards and
// short-lived signatures are sent to the home server.
const encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true});
const databaseName='drg-federation-devices',pendingDevices=new Map(),callSources=new WeakMap();
const idPattern=/^[a-f0-9]{32}$/,devicePattern=/^[a-f0-9]{64}$/;
let database;
export function base64(bytes){let out='';for(let i=0;i<bytes.length;i+=8192)out+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(out);}
export function assetBlobMime(value){return /^(?:image\/(?:png|jpeg|webp|gif)|video\/(?:mp4|webm)|audio\/(?:mpeg|ogg|wav))$/.test(value||'')?value:'application/octet-stream';}
export function unbase64(value){if(typeof value!=='string'||value.length>8*1024*1024)throw new Error(tText("federation:invalidProtectedData"));return Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));}
const url64=bytes=>base64(bytes).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');
const hex=bytes=>[...bytes].map(v=>v.toString(16).padStart(2,'0')).join('');
function unicodeOrder(a,b){const x=[...a],y=[...b];for(let i=0;i<Math.min(x.length,y.length);i++){const d=x[i].codePointAt(0)-y[i].codePointAt(0);if(d)return d;}return x.length-y.length;}
function number(value){
  if(!Number.isFinite(value))throw new Error(tText("federation:nonNumericValueInProtectedRequest"));
  const text=JSON.stringify(value);
  if(!/[.e]/i.test(text))return text;
  const normalized=Math.abs(value)>0&&Math.abs(value)<0.0001?value.toExponential():text;
  return normalized.replace(/e([+-]?)(\d+)$/i,(_all,sign,digits)=>'e'+(sign||'+')+digits.padStart(2,'0'));
}
// Matches the Python profile (ensure_ascii=False, sort_keys=True, compact JSON).
export function canonical(value){
  if(value===null)return 'null';
  if(typeof value==='string'||typeof value==='boolean')return JSON.stringify(value);
  if(typeof value==='number')return number(value);
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  if(value&&Object.getPrototypeOf(value)===Object.prototype)return '{'+Object.keys(value).sort(unicodeOrder).map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
  throw new Error(tText("federation:unsupportedProtectedRequestData"));
}
const digest=async bytes=>new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
function account(ctx){const uid=ctx.state.me?.id;if(!idPattern.test(uid||''))throw new Error(tText("federation:signInToYourAccount"));return {uid,scope:location.origin+'\n'+uid};}
function unchanged(ctx,uid){if(ctx.state.me?.id!==uid)throw new Error(tText("federation:accountHasBeenChangedPleaseRepeatTheAction"));}
function openDatabase(){
  if(!database)database=new Promise((resolve,reject)=>{
    const request=indexedDB.open(databaseName,3);
    request.onupgradeneeded=()=>{for(const name of ['devices','peers','drafts'])if(!request.result.objectStoreNames.contains(name))request.result.createObjectStore(name,{keyPath:'scope'});};
    request.onsuccess=()=>resolve(request.result);
    request.onerror=()=>reject(new Error(tText("federation:unableToOpenDeviceProtectedStorage")));
    request.onblocked=()=>reject(new Error(tText("federation:closeOldDrgTabsAndRepeatTheAction")));
  });
  return database;
}
const wrappingContext=device=>encoder.encode(canonical({purpose:'drg/federation/device-key/v1',scope:device.scope,server_id:device.serverId,card:device.card}));
const privateKey=(key,algorithm,usages)=>key?.type==='private'&&key.extractable===false&&key.algorithm.name===algorithm&&key.usages.slice().sort().join(',')===usages;
async function unwrapDevice(value,scope){
  if(value===undefined)return value;
  if(!value||value.scope!==scope)throw new Error(tText("federation:savedDeviceKeyIsUnavailableExistingDataHas"));
  try{
    if(!privateKey(value.signing,'Ed25519','sign'))throw new Error();
    if(!value.wrappedEncryption){if(!privateKey(value.encryption,'X25519','deriveBits'))throw new Error();return value;} // Preserve existing private keys and card fingerprints.
    const wrapped=value.wrappedEncryption,key=value.wrappingKey;
    if(wrapped.version!==1||!(wrapped.nonce instanceof Uint8Array)||wrapped.nonce.length!==12||!(wrapped.body instanceof ArrayBuffer)||wrapped.body.byteLength>1024||!key||key.extractable!==false||key.algorithm.name!=='AES-GCM'||key.usages.some(usage=>!['wrapKey','unwrapKey'].includes(usage)))throw new Error();
    const encryption=await crypto.subtle.unwrapKey('pkcs8',wrapped.body,key,{name:'AES-GCM',iv:wrapped.nonce,additionalData:wrappingContext(value)},'X25519',false,['deriveBits']);
    if(!privateKey(encryption,'X25519','deriveBits'))throw new Error();
    return {...value,encryption};
  }catch{throw new Error(tText("federation:unableToVerifyTheSavedDeviceKeyFor"));}
}
async function stored(scope){const db=await openDatabase(),value=await new Promise((resolve,reject)=>{const transaction=db.transaction('devices','readonly'),request=transaction.objectStore('devices').get(scope);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(new Error(tText("federation:unableToReadDeviceKey")));});return unwrapDevice(value,scope);}
async function save(value){const db=await openDatabase(),record={...value};if(record.wrappedEncryption)delete record.encryption;return new Promise((resolve,reject)=>{const transaction=db.transaction('devices','readwrite');transaction.objectStore('devices').put(record);transaction.oncomplete=()=>resolve();transaction.onerror=()=>reject(new Error(tText("federation:browserDoesNotAllowSavingTheDeviceKey")));transaction.onabort=()=>reject(new Error(tText("federation:savingDeviceKeyAborted")));});}
async function peerRecord(ctx,serverId,update){const db=await openDatabase(),scope=account(ctx).scope+'\n'+serverId;return new Promise((resolve,reject)=>{const transaction=db.transaction('peers',update?'readwrite':'readonly'),store=transaction.objectStore('peers'),request=store.get(scope);let value;request.onsuccess=()=>{value=request.result;if(update){value=update(value||{scope,actors:{}});store.put(value);}else resolve(value);};transaction.oncomplete=()=>{if(update)resolve(value);};transaction.onerror=()=>reject(new Error(tText("federation:unableToSaveTheConfirmedContactServerKey")));});}
async function peerPin(ctx,serverId,value){const row=await peerRecord(ctx,serverId,value?(old=>({...old,descriptor:value})):undefined);return row?.descriptor;}
export async function peerOrigin(ctx,serverId){const descriptor=await peerPin(ctx,serverId);if(!descriptor)throw new Error(tText("federation:firstConfirmTheFriendSServerCard"));return descriptor.origin;}
const sameServer=(a,b)=>['server_id','origin','key_id','public_key','result_key_id','result_public_key'].every(key=>a?.[key]===b?.[key]);
export async function hasDevice(ctx){try{return !!await stored(account(ctx).scope);}catch{return false;}}
async function generated(scope,serverId,actorId){
  let signing,encryption;
  // WebKit cannot serialize X25519 CryptoKeys to IndexedDB (WebKit 312279).
  // wrapKey keeps PKCS#8 inside native WebCrypto; no plaintext private key is
  // exported to JavaScript. Only the immediately unwrapped nonextractable key
  // leaves this function, and only its authenticated ciphertext is persisted.
  try{signing=await crypto.subtle.generateKey({name:'Ed25519'},false,['sign','verify']);encryption=await crypto.subtle.generateKey({name:'X25519'},true,['deriveBits']);}
  catch{throw new Error(tText("federation:thisBrowserDoesNotSupportSecureCommunicationBetween"));}
  const signRaw=new Uint8Array(await crypto.subtle.exportKey('raw',signing.publicKey)),boxRaw=new Uint8Array(await crypto.subtle.exportKey('raw',encryption.publicKey));
  const joined=new Uint8Array(64);joined.set(signRaw);joined.set(boxRaw,32);
  const value={scope,serverId,signing:signing.privateKey,card:{server_id:serverId,actor_id:actorId,device_id:hex(await digest(joined)),signing_key:url64(signRaw),encryption_key:url64(boxRaw)},registered:false},nonce=crypto.getRandomValues(new Uint8Array(12));
  value.wrappingKey=await crypto.subtle.generateKey({name:'AES-GCM',length:256},false,['wrapKey','unwrapKey']);
  const body=await crypto.subtle.wrapKey('pkcs8',encryption.privateKey,value.wrappingKey,{name:'AES-GCM',iv:nonce,additionalData:wrappingContext(value)});
  encryption=null;value.wrappedEncryption={version:1,nonce,body};
  return unwrapDevice(value,scope);
}
export async function signIntent(device,recipient,intent,now=Math.floor(Date.now()/1000)){
  if(!idPattern.test(recipient)||!devicePattern.test(device.card.device_id))throw new Error(tText("federation:unableToVerifyTheRequestRecipient"));
  const header={alg:'EdDSA',typ:'drg-device-intent+jwt',kid:device.card.device_id};
  const claims={iss:device.card.device_id,aud:recipient,iat:now,exp:now+120,digest:hex(await digest(encoder.encode(canonical(intent))))};
  const body=url64(encoder.encode(canonical(header)))+'.'+url64(encoder.encode(canonical(claims)));
  const signature=new Uint8Array(await crypto.subtle.sign({name:'Ed25519'},device.signing,encoder.encode(body)));
  return body+'.'+url64(signature);
}
export async function verifyResultProof(device,envelope,intent,owner,now=Math.floor(Date.now()/1000)){
  if(!owner||!owner.result_public_key||!owner.result_key_id||!idPattern.test(owner.server_id||''))throw new Error(tText("federation:theOwnerSServerKeyHasNotYet"));
  const token=envelope.result_proof;
  if(typeof token!=='string'||token.length>4096||!/^[-_A-Za-z0-9]+\.[-_A-Za-z0-9]+\.[-_A-Za-z0-9]+$/.test(token))throw new Error(tText("federation:ownerSResponseSignatureIsMissing"));
  const parts=token.split('.');let header,claims;
  try{header=JSON.parse(decoder.decode(unbase64(parts[0])));claims=JSON.parse(decoder.decode(unbase64(parts[1])));}catch{throw new Error(tText("federation:invalidOwnerSResponseSignature"));}
  if(canonical(header)!==canonical({alg:'EdDSA',typ:'drg-federation-result+jwt',kid:owner.result_key_id})||Object.keys(claims).sort().join(',')!=='aud,body_sha256,exp,iat,intent_digest,iss')throw new Error(tText("federation:incorrectResponseSignatureType"));
  const body={ephemeral:envelope.ephemeral,nonce:envelope.nonce,ciphertext:envelope.ciphertext};
  if(claims.iss!==owner.server_id||claims.aud!==device.card.device_id||claims.intent_digest!==hex(await digest(encoder.encode(canonical(intent))))||claims.body_sha256!==hex(await digest(encoder.encode(canonical(body))))||!Number.isSafeInteger(claims.iat)||!Number.isSafeInteger(claims.exp)||claims.exp<=now||claims.iat>now+30||claims.exp<=claims.iat||claims.exp-claims.iat>120)throw new Error(tText("federation:theResponseRefersToADifferentActionOr"));
  const key=await crypto.subtle.importKey('raw',unbase64(owner.result_public_key),{name:'Ed25519'},false,['verify']);
  if(!await crypto.subtle.verify({name:'Ed25519'},key,unbase64(parts[2]),encoder.encode(parts[0]+'.'+parts[1])))throw new Error(tText("federation:ownerSServerSignatureFailedVerification"));
}
export async function openResult(device,envelope,intent,owner){
  if(!envelope||Object.keys(envelope).sort().join(',')!=='ciphertext,ephemeral,nonce,result_proof')throw new Error(tText("federation:theServerReturnedAnUnverifiedResponse"));
  await verifyResultProof(device,envelope,intent,owner);
  try{
    const ephemeral=await crypto.subtle.importKey('raw',unbase64(envelope.ephemeral),{name:'X25519'},false,[]);
    const shared=await crypto.subtle.deriveBits({name:'X25519',public:ephemeral},device.encryption,256);
    const context=encoder.encode(canonical(intent)),keyMaterial=await crypto.subtle.importKey('raw',shared,'HKDF',false,['deriveKey']);
    const key=await crypto.subtle.deriveKey({name:'HKDF',hash:'SHA-256',salt:await digest(context),info:encoder.encode('drg/federation/prototype/result/v1')},keyMaterial,{name:'AES-GCM',length:256},false,['decrypt']);
    const plaintext=await crypto.subtle.decrypt({name:'AES-GCM',iv:unbase64(envelope.nonce),additionalData:context},key,unbase64(envelope.ciphertext));
    return JSON.parse(decoder.decode(plaintext));
  }catch{throw new Error(tText("federation:unableToVerifyAndDecryptTheResponseFor"));}
}
export async function ensureDevice(ctx){
  const {uid,scope}=account(ctx);
  if(pendingDevices.has(scope))return pendingDevices.get(scope);
  const work=(async()=>{
    const info=await ctx.api('/federation/info');unchanged(ctx,uid);
    if(!info.enabled){const error=new Error(tText("federation:communicationWithOtherServersHasBeenDisabledBy"));error.federationDisabled=true;throw error;}
    const serverId=info.descriptor?.server_id;if(!idPattern.test(serverId||''))throw new Error(tText("federation:invalidHomeServerIdentity"));
    let device=await stored(scope);unchanged(ctx,uid);
    if(device&&(device.serverId!==serverId||device.descriptor&&!sameServer(device.descriptor,info.descriptor)))throw new Error(tText("federation:homeServerIdentityHasChangedPleaseConfirmThe"));
    if(!device){device=await generated(scope,serverId,uid);await save(device);}
    device={...device,descriptor:info.descriptor};
    if(Array.isArray(info.devices)){
      const known=info.devices.find(item=>(item.card||item).device_id===device.card.device_id);
      if(known?.active===false)throw new Error(tText("federation:theDeviceKeyHasBeenRevokedContactThe"));
      if(known&&Object.entries(device.card).some(([key,value])=>(known.card||known)[key]!==value))throw new Error(tText("federation:devicePublicKeysDoNotMatchTheSaved"));
      device.registered=!!known;
    }
    if(!device.registered){
      const challenge=await ctx.api('/federation/devices/challenge','POST',{});unchanged(ctx,uid);
      const intent={operation:'enroll',card:device.card,nonce:challenge.nonce};
      const proof=await signIntent(device,serverId,intent);unchanged(ctx,uid);
      await ctx.api('/federation/devices','POST',{card:device.card,nonce:challenge.nonce,proof});unchanged(ctx,uid);
      device={...device,registered:true};await save(device);
    }
    if(!device.draftKey)device={...device,draftKey:await crypto.subtle.generateKey({name:'AES-GCM',length:256},false,['encrypt','decrypt'])};
    await save(device);return device;
  })();
  pendingDevices.set(scope,work);
  try{return await work;}finally{pendingDevices.delete(scope);}
}
export function assertIntent(intent,expected){
  if(!intent||!idPattern.test(intent.event_id||'')||!/^[a-f0-9]{64}$/.test(intent.nonce||''))throw new Error(tText("federation:theServerReturnedAnIncorrectActionConfirmation"));
  const fields=['actor_server_id','actor_id','device_id','target_actor_id','nonce','event_id','operation','payload'];
  if(Object.keys(intent).sort().join(',')!==fields.sort().join(',')||Object.entries(expected).some(([key,value])=>canonical(intent[key])!==canonical(value)))throw new Error(tText("federation:theServerChangedTheRecipientOrTheContent"));
}
export async function operate(ctx,peerId,targetActorId,operation,payload,{eventId}={}){
  const {uid}=account(ctx),device=await ensureDevice(ctx);unchanged(ctx,uid);
  const owner=await peerPin(ctx,peerId);if(!owner)throw new Error(tText("federation:firstConfirmTheContactServerCardOnThe"));unchanged(ctx,uid);
  const expected={actor_server_id:device.serverId,actor_id:uid,device_id:device.card.device_id,target_actor_id:targetActorId,operation,payload};
  const {intent}=await ctx.api('/federation/prepare','POST',{peer_id:peerId,device_id:device.card.device_id,target_actor_id:targetActorId,operation,payload});unchanged(ctx,uid);
  assertIntent(intent,expected);
  if(eventId){if(!idPattern.test(eventId))throw new Error(tText("federation:invalidActionIdentifier"));intent.event_id=eventId;}
  const proof=await signIntent(device,peerId,intent);unchanged(ctx,uid);
  const envelope=await ctx.api('/federation/send','POST',{peer_id:peerId,intent,proof});unchanged(ctx,uid);
  const result=await openResult(device,envelope,intent,owner);unchanged(ctx,uid);return result;
}
export async function operateLocal(ctx,operation,payload){
  const {uid}=account(ctx),device=await ensureDevice(ctx);unchanged(ctx,uid);
  const expected={actor_server_id:device.serverId,actor_id:uid,device_id:device.card.device_id,target_actor_id:uid,operation,payload};
  const {intent}=await ctx.api('/federation/local/prepare','POST',{device_id:device.card.device_id,operation,payload});unchanged(ctx,uid);
  assertIntent(intent,expected);
  const proof=await signIntent(device,device.serverId,intent);unchanged(ctx,uid);
  const envelope=await ctx.api('/federation/local/send','POST',{intent,proof});unchanged(ctx,uid);
  const result=await openResult(device,envelope,intent,device.descriptor);unchanged(ctx,uid);return result;
}
export async function contacts(ctx){const result=await ctx.api('/federation/contacts');return result.contacts||[];}
export async function ownContact(ctx){const device=await ensureDevice(ctx),value=await ctx.api('/federation/contact-card?device_id='+encodeURIComponent(device.card.device_id));if(canonical(value.actor)!==canonical(device.card)||!sameServer(value.server,device.descriptor))throw new Error(tText("federation:theServerHasReplacedThisDeviceSPublic"));return value;}
export async function knownContact(ctx,peerId,actorId){const actor=(await peerRecord(ctx,peerId))?.actors?.[actorId];return actor?.cards?.[actor.last_device_id]||actor;}
export async function contactFingerprint(value){return hex(await digest(encoder.encode(canonical({version:value.version,server:value.server,transport:{fingerprint:value.transport.fingerprint},actor:value.actor}))));}
export async function validateContact(value){
  if(typeof value==='string'){if(value.length>100000)throw new Error(tText("federation:contactCardIsTooLarge"));try{value=JSON.parse(value);}catch{throw new Error(tText("federation:pasteTheFullDrgContactCard"));}}
  if(value?.version!==1||!idPattern.test(value.server?.server_id||'')||!idPattern.test(value.actor?.actor_id||'')||value.actor.server_id!==value.server.server_id||!devicePattern.test(value.actor.device_id||''))throw new Error(tText("federation:invalidDrgContactCard"));
  let origin;try{origin=new URL(value.server.origin);}catch{throw new Error(tText("federation:invalidContactServerAddress"));}
  if(origin.protocol!=='https:'||origin.username||origin.password||origin.search||origin.hash||!['','/'].includes(origin.pathname))throw new Error(tText("federation:theContactMustUseTheFullHttpsAddress"));
  const sign=unbase64(value.actor.signing_key),box=unbase64(value.actor.encryption_key),server=unbase64(value.server.public_key),result=unbase64(value.server.result_public_key);
  if(sign.length!==32||box.length!==32||server.length!==32||result.length!==32)throw new Error(tText("federation:invalidContactOpenKeys"));
  const joined=new Uint8Array(64);joined.set(sign);joined.set(box,32);
  if(hex(await digest(joined))!==value.actor.device_id||'ed25519:'+url64(await digest(server))!==value.server.key_id||'ed25519:'+url64(await digest(result))!==value.server.result_key_id)throw new Error(tText("federation:theCardSFingerprintDoesNotMatchIts"));
  const certificate=value.transport?.certificate_pem?.match(/^\s*-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+)\s*-----END CERTIFICATE-----\s*$/);
  if(!certificate||!devicePattern.test(value.transport?.fingerprint||'')||hex(await digest(unbase64(certificate[1].replace(/\s/g,''))))!==value.transport.fingerprint)throw new Error(tText("federation:theCommunicationCertificateSFingerprintDoesNotMatch"));
  return value;
}
export async function addContact(ctx,value,{expectedOldDeviceId}={}){const verified=await validateContact(value);await ensureDevice(ctx);const old=await peerPin(ctx,verified.server.server_id);if(old&&!sameServer(old,verified.server))throw new Error(tText("federation:theKeyOrAddressOfAPreviouslyVerified"));const result=await ctx.api('/federation/contacts/import','POST',{package:verified,confirmed:true,...expectedOldDeviceId?{expected_old_device_id:expectedOldDeviceId}:{}});await peerRecord(ctx,verified.server.server_id,previous=>{const actor=previous.actors?.[verified.actor.actor_id],cards=actor?.cards||(actor?.device_id?{[actor.device_id]:actor}:{});return {...previous,descriptor:verified.server,actors:{...previous.actors,[verified.actor.actor_id]:{last_device_id:verified.actor.device_id,cards:{...cards,[verified.actor.device_id]:verified.actor}}}};});return result;}
export async function verifyCallActions(ctx,call,known=new Set()){
  if(!call?.federation)return call;
  const meta=call.federation,actor=(await peerRecord(ctx,meta.peer_id))?.actors?.[meta.actor_id],cards=actor?.cards||(actor?.device_id?{[actor.device_id]:actor}:{});
  if(!call.outgoing&&meta.home_actor_id!==ctx.state.me?.id||call.outgoing&&(meta.home_actor_id!==meta.actor_id||meta.home_server_id!==meta.peer_id))throw new Error(tText("federation:theCallRefersToADifferentRecipient"));
  return verifyCallEvidence(call,cards,known);
}
export async function verifyCallEvidence(call,cards,known=new Set(),now=Math.floor(Date.now()/1000)){
  const meta=call.federation;
  if(!Object.keys(cards).length)throw new Error(tText("federation:theContactSDeviceCardHasNotBeen"));
  const actions=call.peer_actions||[],sdps=new Set(),candidates=new Set(),media=[];let invitation=false;
  const candidate=value=>{if(!value||Object.keys(value).some(key=>!['candidate','sdpMid','sdpMLineIndex','usernameFragment'].includes(key)))throw new Error(tText("federation:invalidCallNetworkAddress"));return canonical({candidate:value.candidate,sdpMid:value.sdpMid??null,sdpMLineIndex:value.sdpMLineIndex??null,usernameFragment:value.usernameFragment??null});};
  const mediaState=value=>canonical({muted:!!value.muted,camera:!!value.camera,screen:!!value.screen});
  if(!Array.isArray(actions)||actions.length>1000)throw new Error(tText("federation:invalidCallEvidence"));
  for(const action of actions){
    const {intent,proof}=action||{};
    const card=cards[intent?.device_id];if(!card)throw new Error(tText("federation:theContactSDeviceHasChangedVerifyIts"));
    if(!intent||typeof proof!=='string'||proof.length>4096||!['call_invite','call_accept','call_signal'].includes(intent.operation)||intent.actor_server_id!==card.server_id||intent.actor_id!==card.actor_id||intent.device_id!==card.device_id||intent.target_actor_id!==meta.home_actor_id||intent.payload?.id!==call.id||typeof intent.payload.device!=='string'||!intent.payload.device||intent.payload.device.length>128)throw new Error(tText("federation:theActionAppliesToAnotherParticipantOrCall"));
    assertIntent(intent,{actor_server_id:card.server_id,actor_id:card.actor_id,device_id:card.device_id,target_actor_id:meta.home_actor_id});
    const parts=proof.split('.');if(parts.length!==3)throw new Error(tText("federation:callParametersSignatureIsMissing"));
    let header,claims;try{header=JSON.parse(decoder.decode(unbase64(parts[0])));claims=JSON.parse(decoder.decode(unbase64(parts[1])));}catch{throw new Error(tText("federation:incorrectCallParametersSignature"));}
    const already=known.has(proof);
    if(canonical(header)!==canonical({alg:'EdDSA',typ:'drg-device-intent+jwt',kid:card.device_id})||Object.keys(claims).sort().join(',')!=='aud,digest,exp,iat,iss'||claims.iss!==card.device_id||claims.aud!==meta.home_server_id||claims.digest!==hex(await digest(encoder.encode(canonical(intent))))||!Number.isSafeInteger(claims.iat)||!Number.isSafeInteger(claims.exp)||claims.exp<=claims.iat||claims.exp-claims.iat>120||!already&&(claims.exp<=now||claims.iat>now+30))throw new Error(tText("federation:callParametersHaveNotBeenVerifiedByThe"));
    if(!already){const publicKey=await crypto.subtle.importKey('raw',unbase64(card.signing_key),{name:'Ed25519'},false,['verify']);if(!await crypto.subtle.verify('Ed25519',publicKey,unbase64(parts[2]),encoder.encode(parts[0]+'.'+parts[1])))throw new Error(tText("federation:theContactSDeviceSignatureFailedVerification"));known.add(proof);}
    const source=canonical({id:call.id,home:meta.home_server_id,actor:meta.home_actor_id,server:card.server_id,user:card.actor_id,device:card.device_id,runtime:intent.payload.device}),previous=callSources.get(known);if(previous&&previous!==source)throw new Error(tText("federation:theContactSDeviceChangedDuringTheCall"));callSources.set(known,source);
    if(intent.payload.sdp)sdps.add(intent.payload.sdp);
    if(intent.operation==='call_invite'&&intent.payload.sdp)invitation=true;
    for(const item of intent.payload.candidates||[])candidates.add(candidate(item));
    if(intent.payload.media)media.push(mediaState(intent.payload.media));
  }
  if(call.status==='ringing'&&!call.outgoing&&!call.owned&&!invitation||call.sdp&&!sdps.has(call.sdp)||(call.candidates||[]).some(item=>!candidates.has(candidate(item)))||call.media&&(media.length||Object.values(call.media).some(Boolean))&&!media.includes(mediaState(call.media)))throw new Error(tText("federation:theServerHasChangedTheProtectedCallParameters"));
  return call;
}
export async function saveDraft(ctx,key,value){
  const {scope}=account(ctx),device=await stored(scope),db=await openDatabase(),draftScope=scope+'\n'+key;
  if(value&&!device?.draftKey)throw new Error(tText("federation:unableToProtectUnsavedChangesOnThisDevice"));
  let encrypted;
  if(value){const nonce=crypto.getRandomValues(new Uint8Array(12));encrypted={scope:draftScope,nonce,body:await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce,additionalData:encoder.encode(draftScope)},device.draftKey,encoder.encode(canonical(value)))};}
  return new Promise((resolve,reject)=>{const transaction=db.transaction('drafts','readwrite'),store=transaction.objectStore('drafts');if(value)store.put(encrypted);else store.delete(draftScope);transaction.oncomplete=()=>resolve();transaction.onerror=()=>reject(new Error(tText("federation:unableToSaveTheProtectedDraftOnThe")));});
}
export async function loadDraft(ctx,key){
  const {scope}=account(ctx),device=await stored(scope),db=await openDatabase(),draftScope=scope+'\n'+key;
  const value=await new Promise((resolve,reject)=>{const request=db.transaction('drafts').objectStore('drafts').get(draftScope);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(new Error(tText("federation:unableToOpenTheLocalDraft")));});
  if(!value)return null;
  if(!device?.draftKey)throw new Error(tText("federation:localDraftKeyIsUnavailable"));
  try{return JSON.parse(decoder.decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:value.nonce,additionalData:encoder.encode(draftScope)},device.draftKey,value.body)));}catch{throw new Error(tText("federation:theLocalDraftFailedVerification"));}
}
