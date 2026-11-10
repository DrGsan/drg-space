// Loaded only by a LiveKit invitation. No SDK in the initial application bundle.
import {Room,RoomEvent,Track,ConnectionState,DisconnectReason} from 'livekit-client';
import {tText} from '../../core/i18n.js';

export class LiveKitGuest {
  constructor({outputs,changed,failed,ended,playback}){
    this.outputs=outputs;this.changed=changed;this.failed=failed;this.ended=ended;this.playback=playback;
    this.players=new Map();this.closed=false;
    this.room=new Room({adaptiveStream:false,dynacast:true});
    this.room.on(RoomEvent.TrackSubscribed,(track,publication,participant)=>{
      if(this.closed||track.kind!==Track.Kind.Audio)return;
      this.remove(publication.trackSid);
      // DrG owns playback and output routing. SDK attach() starts playback before
      // setSinkId has completed, which can briefly use the wrong phone speaker.
      const audio=document.createElement('audio');audio.autoplay=false;audio.setAttribute('playsinline','');audio.hidden=true;
      audio.srcObject=new MediaStream([track.mediaStreamTrack]);
      this.players.set(publication.trackSid,{audio,track,identity:String(participant.identity)});
      this.outputs.add(audio);document.body.append(audio);this.play(audio);this.changed();
    });
    this.room.on(RoomEvent.TrackUnsubscribed,(track,publication)=>{if(this.players.get(publication.trackSid)?.track===track)this.remove(publication.trackSid);});
    for(const event of [RoomEvent.ConnectionStateChanged,RoomEvent.ParticipantConnected,RoomEvent.ParticipantDisconnected,RoomEvent.TrackMuted,RoomEvent.TrackUnmuted,RoomEvent.RoomMetadataChanged])this.room.on(event,()=>{if(!this.closed)this.changed();});
    this.room.on(RoomEvent.Disconnected,reason=>{if(!this.closed&&this.established){if([DisconnectReason.ROOM_DELETED,DisconnectReason.PARTICIPANT_REMOVED].includes(reason))this.ended();else this.failed(tText('chat:livekitDisconnected'));}});
  }
  async connect(access,stream,muted){
    // Access is issued by DrG for this participant and room, never from .env.
    if(!access?.token||!access.url?.startsWith('wss://'))throw Error(tText('chat:livekitDisconnected'));
    await this.room.connect(access.url,access.token,{peerConnectionTimeout:15000,websocketTimeout:10000,maxRetries:0});
    if(this.closed)return;
    this.established=true;
    this.publication=await this.room.localParticipant.publishTrack(stream.getAudioTracks()[0],{
      source:Track.Source.Microphone,audioPreset:{maxBitrate:32000},dtx:true,red:true,forceStereo:false,
    });
    if(this.closed){await this.room.disconnect();return;}
    await this.mute(muted);this.changed();
  }
  get connected(){return this.room.state===ConnectionState.Connected;}
  has(identity){return this.room.remoteParticipants.has(identity);}
  async play(audio){
    const active=()=>[...this.players.values()].some(player=>player.audio===audio);
    try{await this.outputs.prepare(audio);if(this.closed)return false;if(!active())return true;await audio.play();return true;}
    catch{if(!this.closed&&active())this.playback();return !active();}
  }
  async resume(){return (await Promise.all([...this.players.values()].map(p=>this.play(p.audio)))).every(Boolean);}
  async mute(value){if(!this.publication||this.closed)return;await (value?this.publication.mute():this.publication.unmute());}
  remove(sid){const player=this.players.get(sid);if(!player)return;this.outputs.remove(player.audio);player.audio.pause();player.audio.srcObject=null;player.audio.remove();this.players.delete(sid);}
  diagnostics(){return {transport:'livekit',connection:this.room.state,participants:this.room.remoteParticipants.size,players:this.players.size};}
  close(){if(this.closed)return;this.closed=true;for(const sid of this.players.keys())this.remove(sid);this.room.removeAllListeners();this.room.disconnect().catch(()=>{});}
}
