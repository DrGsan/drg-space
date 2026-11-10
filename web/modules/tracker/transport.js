import {tText} from '../../core/i18n.js';
import {operate,contacts,base64,unbase64,assetBlobMime} from '../federation/client.js';

export const projectAddress=project=>project.remote?project.remote.peer_id+':'+project.remote.actor_id+':'+project.id:project.id;
const part=encodeURIComponent;

export class TrackerTransport{
 constructor(ctx){this.ctx=ctx;this.remoteContacts=[];}
 async catalog(){
  const result=await this.ctx.api('/tracker');
  this.remoteContacts=[];
  if(this.ctx.state.me.federation_enabled){
   try{this.remoteContacts=(await contacts(this.ctx)).filter(contact=>contact.status==='accepted');}catch{}
  }
  return {...result,remoteContacts:this.remoteContacts};
 }
 async remoteCatalog(contact){
  const result=await operate(this.ctx,contact.peer_id,contact.actor_id,'tracker_catalog',{});
  return result.projects.map(project=>({...project,remote:{peer_id:contact.peer_id,actor_id:contact.actor_id,revision:project.grant_revision}}));
 }
 async call(project,operation,payload={}){
  const remote=project.remote;
  return operate(this.ctx,remote.peer_id,remote.actor_id,operation,{...payload,project_id:project.id,revision:remote.revision});
 }
 async project(project){
  if(!project.remote)return this.ctx.api('/tracker/projects/'+part(project.id));
  const result=await this.call(project,'tracker_project');return {...result,remote:{...project.remote,revision:result.grant_revision}};
 }
 async issues(project,after=0,group=null){
  if(!project.remote)return this.ctx.api('/tracker/projects/'+part(project.id)+'/issues?after='+after+'&limit=500'+(group!==null?'&group_id='+part(group):''));
  const result=await this.call(project,'tracker_page',{after,limit:200,...(group!==null?{group_id:group}:{})});
  result.project={...result.project,remote:project.remote};return result;
 }
 async allIssues(project,group=null,current=()=>true){
  // A complete compact projection prevents filtering/sorting just the oldest
  // page. Keep the previous screen until one consistent project version loads.
  for(let attempt=0;attempt<3;attempt++){
   let after=0,version,items=[],page,changed=false;
   do{
    page=await this.issues(project,after,group);if(!current())return null;
    if(version!==undefined&&page.project.version!==version){changed=true;break;}
    version=page.project.version;items.push(...page.items);
    if(page.more&&page.cursor<=after)throw Error(tText('tracker:networkError'));
    after=page.cursor;
   }while(page.more);
   if(!changed&&items.length===Object.values(page.view_counts).reduce((n,v)=>n+v,0))return {...page,items,more:false};
  }
  throw Error(tText('tracker:updatedRemotely'));
 }
 async group(project,id){return project.remote?this.call(project,'tracker_group_read',{group_id:id}):this.ctx.api('/tracker/groups/'+part(id));}
 async avatar(project){const result=await this.call(project,'tracker_avatar_read');if(result.mime!=='image/webp'||typeof result.body!=='string'||result.body.length>350000)throw Error(tText('tracker:invalidAvatar'));const bytes=unbase64(result.body);return URL.createObjectURL(new Blob([bytes],{type:'image/webp'}));}
 async issue(project,id){return project.remote?this.call(project,'tracker_issue_read',{issue_id:id}):this.ctx.api('/tracker/issues/'+part(id));}
 async create(project,data){return project.remote?this.call(project,'tracker_issue_create',{data}):this.ctx.api('/tracker/projects/'+part(project.id)+'/issues','POST',data);}
 async update(project,id,data){return project.remote?this.call(project,'tracker_issue_update',{issue_id:id,data}):this.ctx.api('/tracker/issues/'+part(id),'PATCH',data);}
 async comment(project,id,body){return project.remote?this.call(project,'tracker_comment',{issue_id:id,body}):this.ctx.api('/tracker/issues/'+part(id)+'/comments','POST',{body});}
 async accept(project){
  if(!project.remote)return this.ctx.api('/tracker/projects/'+part(project.id)+'/accept','POST');
  const result=await this.call(project,'tracker_accept');return {...result,remote:{...project.remote,revision:result.grant_revision}};
 }
 async deleteProject(project,confirmation){return this.ctx.api('/tracker/projects/'+part(project.id),'DELETE',{version:project.version,confirmation});}
 async restoreProject(project){return this.ctx.api('/tracker/projects/'+part(project.id)+'/restore','POST',{version:project.version});}
 async upload(project,id,file){
  if(file.size>50*1024*1024)throw new Error(tText('tracker:attachmentHelp'));
  if(!project.remote)return this.ctx.api('/tracker/issues/'+part(id)+'/attachments?name='+part(file.name),'POST',file);
  const started=await this.call(project,'tracker_upload_start',{issue_id:id,name:file.name,size:file.size});
  for(let offset=0;offset<file.size;offset+=192*1024){
   const body=base64(new Uint8Array(await file.slice(offset,offset+192*1024).arrayBuffer()));
   await this.call(project,'tracker_upload_chunk',{issue_id:id,upload_id:started.id,offset,body});
  }
  return this.call(project,'tracker_upload_finish',{issue_id:id,upload_id:started.id});
 }
 async attachment(project,id,file){
  if(!project.remote)return '/api/tracker/issues/'+part(id)+'/attachments/'+part(file.id);
  const chunks=[];let offset=0;
  do{
   const result=await this.call(project,'tracker_attachment_read',{issue_id:id,attachment_id:file.id,offset,length:192*1024});
   const bytes=unbase64(result.body);
   if(result.offset!==offset||result.size!==file.size||bytes.length>192*1024||offset+bytes.length>file.size||(!bytes.length&&offset<file.size))throw new Error(tText('federation:invalidProtectedData'));
   chunks.push(bytes);offset+=bytes.length;
  }while(offset<file.size);
  return URL.createObjectURL(new Blob(chunks,{type:assetBlobMime(file.mime)}));
 }
}
