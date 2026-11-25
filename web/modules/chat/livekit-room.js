// Loaded only by a LiveKit invitation. No SDK in the initial application bundle.
import {Room,RoomEvent,Track,LocalAudioTrack,ConnectionState,DisconnectReason} from 'livekit-client';
import {tText} from '../../core/i18n.js';

// The call owns the input device. Only gate its clone and notify LiveKit, in
// the same event as the mute button, without stopping or reacquiring the mic.
class GuestMicrophone extends LocalAudioTrack {
  setMuted(muted){this.setTrackMuted(muted);}
}

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
    this.room.on(RoomEvent.Disconnected,reason=>{if(!this.closed&&this.established){if([DisconnectReason.CLIENT_INITIATED,DisconnectReason.DUPLICATE_IDENTITY,DisconnectReason.PARTICIPANT_REMOVED].includes(reason))this.ended();else this.failed(tText('chat:livekitDisconnected'));}});
  }
  async connect(access,stream,muted){
    // Access is issued by DrG for this participant and room, never from .env.
    if(!access?.token||!access.url?.startsWith('wss://'))throw Error(tText('chat:livekitDisconnected'));
    this.muted??=Boolean(muted);
    await this.room.connect(access.url,access.token,{peerConnectionTimeout:15000,websocketTimeout:10000,maxRetries:0});
    if(this.closed)return;
    this.established=true;
    // The SDK owns this track and can stop it on a terminal transport disconnect.
    this.track=new GuestMicrophone(stream.getAudioTracks()[0].clone());
    // Publication may outlive multiple clicks or even a hangup. Publish silent
    // and open the track only after LiveKit can signal its actual mute state.
    this.track.setMuted(true);
    this.publication=await this.room.localParticipant.publishTrack(this.track,{
      source:Track.Source.Microphone,audioPreset:{maxBitrate:32000},dtx:true,red:true,forceStereo:false,
    });
    if(this.closed){await this.room.disconnect();return;}
    this.track.setMuted(this.muted);this.changed();
  }
  get connected(){return this.room.state===ConnectionState.Connected;}
  has(identity){return this.room.remoteParticipants.has(identity);}
  async play(audio){
    const active=()=>[...this.players.values()].some(player=>player.audio===audio);
    try{await this.outputs.prepare(audio);if(this.closed)return false;if(!active())return true;await audio.play();return true;}
    catch{if(!this.closed&&active())this.playback();return !active();}
  }
  async resume(){return (await Promise.all([...this.players.values()].map(p=>this.play(p.audio)))).every(Boolean);}
  async mute(value){
    if(this.closed)return;
    this.muted=Boolean(value);
    if(this.publication)this.track.setMuted(this.muted);
  }
  remove(sid){const player=this.players.get(sid);if(!player)return;this.outputs.remove(player.audio);player.audio.pause();player.audio.srcObject=null;player.audio.remove();this.players.delete(sid);}
  diagnostics(){return {transport:'livekit',connection:this.room.state,participants:this.room.remoteParticipants.size,players:this.players.size};}
  close(){if(this.closed)return;this.closed=true;this.track?.stop();for(const sid of this.players.keys())this.remove(sid);this.room.removeAllListeners();this.room.disconnect().catch(()=>{});}
}
