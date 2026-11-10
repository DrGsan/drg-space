import {tText} from "../../core/i18n.js";
import {personName} from '../../core/person-names.js';
import * as Y from 'yjs';
import { Awareness } from 'y-protocols/awareness';
import { connectRoom } from '../../core/realtime.js';
import { seedCollaborativeDoc } from './editor-runtime.js';
import {documentUpdates} from './document-sync.js';
const encode=bytes=>{let result='';for(let i=0;i<bytes.length;i+=8192)result+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(result);};
const decode=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
export function collaborativePage(page,ctx,options={}){
  const doc=new Y.Doc(),awareness=new Awareness(doc),remoteUsers=new Map();
  let synced=false,disposed=false,epoch=null,error=null,resolveReady,rejectReady,readyTimeout,presenceTimer;
  const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
  // Attach a handler immediately: navigation may destroy a pending connection.
  ready.catch(()=>{});
  const user={id:ctx.state.me.id,name:ctx.state.me.login,color:'#'+ctx.state.me.id.slice(0,6)};
  const updates=documentUpdates(doc,(update,seq)=>room.send({type:'update',epoch,update:encode(update),seq}),{onDirty:()=>{options.onDirty?.();status();}});
  function push(){updates.flush();}
  function status(){options.onStatus?.(error?'error':!room.online||!synced?'offline':updates.dirty?'saving':'saved');}
  function sendPresence(){clearTimeout(presenceTimer);presenceTimer=null;if(synced&&room.online)room.send({type:'presence',state:awareness.getLocalState()});}
  const room=connectRoom('notes/'+page.id,ctx,{
    clientId:doc.clientID,
    validate:async()=>{const current=await ctx.api('/notes/'+page.id);if(current.role&&current.role!=='editor'){const error=new Error(tText("notes:permissionsHaveChanged"));error.status=403;throw error;}return current;},
    onStatus:state=>{
      if(state==='offline'){
        updates.offline();
        const removed=[...awareness.states.keys()].filter(id=>id!==doc.clientID);
        for(const id of removed)awareness.states.delete(id);
        if(removed.length)awareness.emit('change',[{added:[],updated:[],removed},'remote']);
        options.onPresence?.([user.name]);
      }
      status();
    },
    onReset:()=>{options.onReset?.();},
    onError:e=>{
      updates.reject();
      error=e;options.onError?.(e);status();
      if(!synced){rejectReady(new Error(e.message));}
      if([401,403,404,409].includes(e.status))room.destroy();
      if(e.status===409)options.onReset?.();
    },
    onMessage:data=>{
      if(disposed)return;
      if(data.type==='init'){
        const seeded=seedCollaborativeDoc(data.page);
        room.send({type:'init',update:encode(Y.encodeStateAsUpdate(seeded)),version:data.page.version});seeded.destroy();
      }
      if(data.type==='sync'){
        if(epoch&&epoch!==data.epoch){options.onReset?.();return;}
        epoch=data.epoch;const state=decode(data.state);Y.applyUpdate(doc,state,'remote');updates.sync(state);
        const first=!synced;synced=true;error=null;clearTimeout(readyTimeout);
        if(first)resolveReady();
        options.onVersion?.(data.version);push();status();
        if(first)sendPresence();
      }
      if(data.type==='update'){
        if(data.epoch!==epoch){options.onReset?.();return;}
        Y.applyUpdate(doc,decode(data.update),'remote');options.onVersion?.(data.version);status();
      }
      if(data.type==='ack'){
        error=null;
        updates.ack(data.seq);
        options.onVersion?.(data.version);status();
      }
      if(data.type==='presence'&&data.client_id!==doc.clientID){
        const id=data.client_id,exists=awareness.states.has(id);
        if(data.state){
          const cursor=data.state.cursor;
          if(cursor){ // Only valid Yjs relative positions reach the cursor plugin.
            try{for(const key of ['anchor','head'])Y.createRelativePositionFromJSON(cursor[key]);}catch{return;}
          }
          const rawUser=data.state.user||{};remoteUsers.set(id,rawUser);const profile=(page.participants||[]).find(p=>p.id===rawUser.id)||{id:rawUser.id,login:rawUser.name};
          awareness.states.set(id,{...data.state,user:{...rawUser,name:personName(profile)}});
        }else awareness.states.delete(id);
        awareness.emit('change',[{added:data.state&&!exists?[id]:[],updated:data.state&&exists?[id]:[],removed:!data.state&&exists?[id]:[]},'remote']);
        options.onPresence?.([...awareness.states.values()].map(s=>s.user?.name).filter(Boolean));
      }
    }
  });
  readyTimeout=setTimeout(()=>rejectReady(new Error(tText("notes:failedToConnectCollaborativeEditorCheckTheConnection"))),15000);
  awareness.on('update',(_change,origin)=>{if(origin!=='remote'&&synced){if(!presenceTimer)presenceTimer=setTimeout(sendPresence,120);options.onPresence?.([...awareness.states.values()].map(s=>s.user?.name).filter(Boolean));}});
  const namesChanged=()=>{const updated=[];for(const [id,state] of awareness.states){if(id===doc.clientID)continue;const raw=remoteUsers.get(id)||state.user||{};const profile=(page.participants||[]).find(p=>p.id===raw.id)||{id:raw.id,login:raw.name};awareness.states.set(id,{...state,user:{...raw,name:personName(profile)}});updated.push(id);}if(updated.length)awareness.emit('change',[{added:[],updated,removed:[]},'remote']);options.onPresence?.([...awareness.states.values()].map(s=>s.user?.name).filter(Boolean));};
  document.addEventListener('drg-people-names-change',namesChanged);
  const meta=doc.getMap('meta');
  meta.observe(()=>options.onTitle?.(meta.get('title')||tText("notes:untitled")));
  return {
    ready,doc,collaboration:{fragment:doc.getXmlFragment('prosemirror'),user,provider:{awareness},showCursorLabels:'always'},
    get isSaved(){return synced&&!error&&!updates.dirty;},
    get hasUnsavedChanges(){return updates.dirty;},
    title(value){meta.set('title',value||tText("notes:untitled"));},
    async flush(){
      if(!synced||error||!room.online)return false;
      push();const target=updates.version,deadline=Date.now()+10000;
      while(!disposed&&!error&&updates.acknowledged<target&&Date.now()<deadline)await new Promise(r=>setTimeout(r,40));
      return !disposed&&!error&&updates.acknowledged>=target;
    },
    destroy(){disposed=true;document.removeEventListener('drg-people-names-change',namesChanged);updates.destroy();clearTimeout(presenceTimer);clearTimeout(readyTimeout);rejectReady(new Error(tText("notes:connectionClosed")));room.destroy();awareness.destroy();doc.destroy();}
  };
}
