import {expect} from '@playwright/test';
import {canonical} from '../../web/modules/federation/client.js';

// The authenticated browser deliberately signs adversarial requests with its
// own nonextractable key. This tests server authorization beyond UI controls.
async function attempt(page,operation,payload){
  if(!await page.evaluate(()=>typeof window.__securityCanonical==='function'))await page.exposeFunction('__securityCanonical',canonical);
  return page.evaluate(async({operation,payload})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');
    async function request(path,method='GET',body){const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(body?{body:JSON.stringify(body)}:{})});return {status:response.status,body:await response.json()};}
    const me=(await request('/me')).body,peer=(await request('/federation/contacts')).body.contacts.find(contact=>contact.status==='accepted');
    const own=await new Promise((resolve,reject)=>{const open=indexedDB.open('drg-federation-devices');open.onsuccess=()=>{const get=open.result.transaction('devices').objectStore('devices').get(location.origin+'\n'+me.id);get.onsuccess=()=>resolve(get.result);get.onerror=reject;};open.onerror=reject;});
    if(!own||own.signing.extractable)throw Error('Expected the browser-owned nonextractable key.');
    const prepared=await request('/federation/prepare','POST',{peer_id:peer.peer_id,device_id:own.card.device_id,target_actor_id:peer.actor_id,operation,payload});if(prepared.status!==200)return {status:prepared.status,error:prepared.body.error};const intent=prepared.body.intent;
    if(intent.actor_server_id!==own.card.server_id||intent.actor_id!==me.id||intent.device_id!==own.card.device_id||intent.target_actor_id!==peer.actor_id||intent.operation!==operation||await window.__securityCanonical(intent.payload)!==await window.__securityCanonical(payload))throw Error('The relay substituted the test intent.');
    const encode=value=>new TextEncoder().encode(value),url64=bytes=>btoa(String.fromCharCode(...bytes)).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_'),hash=bytes=>[...bytes].map(value=>value.toString(16).padStart(2,'0')).join(''),now=Math.floor(Date.now()/1000);
    const header={alg:'EdDSA',typ:'drg-device-intent+jwt',kid:own.card.device_id},claims={iss:own.card.device_id,aud:peer.peer_id,iat:now,exp:now+120,digest:hash(new Uint8Array(await crypto.subtle.digest('SHA-256',encode(await window.__securityCanonical(intent)))))};
    const input=url64(encode(await window.__securityCanonical(header)))+'.'+url64(encode(await window.__securityCanonical(claims))),signature=await crypto.subtle.sign('Ed25519',own.signing,encode(input)),sent=await request('/federation/send','POST',{peer_id:peer.peer_id,intent,proof:input+'.'+url64(new Uint8Array(signature))});return {status:sent.status,error:sent.body.error};
  },{operation,payload});
}

export async function federationDatabaseSecurityScenario({alice,bob,api,share,fixture}){
  const id=fixture.id,attack='<img src=x onerror="window.__federationStoredXss=1">';
  for(const page of [alice,bob]){await page.addInitScript(()=>window.__federationStoredXss=0);await page.evaluate(()=>window.__federationStoredXss=0);}
  let db=await api(alice,'/databases/'+id);
  const properties=[...db.properties,{id:'malicious_text',name:attack,type:'text'},{id:'malicious_formula',name:'Формула с разметкой',type:'formula',expression:'concat("<svg/onload=alert(1)>")'}];
  await api(alice,'/databases/'+id,'PUT',{version:db.version,properties,views:db.views.map((view,index)=>index===0?{...view,name:attack}:view)});
  const record=await api(alice,'/databases/'+id+'/records','POST',{title:attack,properties:{malicious_text:'"><svg/onload=alert(1)>'}});
  await bob.locator('[data-refresh-remote]').click();await bob.locator(`[data-remote-note="${id}"]`).click();await expect(bob.locator('.note-database')).toContainText(attack);await expect(bob.locator('.note-database img[onerror],.note-database svg[onload],.note-database script')).toHaveCount(0);expect(await bob.evaluate(()=>window.__federationStoredXss)).toBe(0);
  await alice.goto('/page/'+id);await expect(alice.locator('.note-database')).toContainText(attack);await expect(alice.locator('.note-database img[onerror],.note-database svg[onload],.note-database script')).toHaveCount(0);expect(await alice.evaluate(()=>window.__federationStoredXss)).toBe(0);
  const privateDatabase=await api(alice,'/databases','POST',{title:'Unshared IDOR fixture'}),privateRecord=await api(alice,'/databases/'+privateDatabase.id+'/records','POST',{title:'Unshared record fixture',properties:{}});
  const denied=async(op,payload)=>expect([403,404]).toContain((await attempt(bob,op,payload)).status);
  await denied('database_read',{note_id:privateDatabase.id,revision:1});await denied('read',{note_id:privateRecord.id,revision:1});
  db=await api(alice,'/databases/'+id);const revision=await bob.evaluate(id=>window.__databaseReplies.findLast(value=>value.note_id===id)?.revision,id);expect(revision).toBeGreaterThan(0);
  await denied('database_configure',{note_id:id,revision,version:db.version,properties:[...db.properties,{id:'forbidden_relation',name:'Forbidden',type:'relation',database_id:privateDatabase.id}],views:db.views});expect((await api(alice,'/databases/'+id)).version).toBe(db.version);
  await share(alice,'viewer');await bob.locator('[data-refresh-remote]').click();await bob.locator(`[data-remote-note="${id}"]`).click();await expect(bob.locator('#db-add')).toHaveCount(0);const viewerRevision=await bob.evaluate(id=>window.__databaseReplies.findLast(value=>value.note_id===id)?.revision,id),fresh=await api(alice,'/databases/'+id),row=fresh.records.find(row=>row.note_id===record.id);
  await denied('database_record_update',{note_id:record.id,revision:viewerRevision,version:row.version,properties:{malicious_text:'unauthorized overwrite'}});expect((await api(alice,'/databases/'+id)).records.find(row=>row.note_id===record.id).properties.malicious_text).toBe('"><svg/onload=alert(1)>');
  console.log('PASS: stored database/view/property/record/formula markup remains inert locally and remotely; real device-signed IDOR, unshared-relation configuration and viewer writes rejected without mutations.');
}
