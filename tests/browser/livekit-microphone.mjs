import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const bundle=await build({
  entryPoints:['web/modules/chat/livekit-room.js'],bundle:true,write:false,
  format:'iife',globalName:'GuestAdapter',
});
for(const engine of [chromium,webkit]){
  const browser=await engine.launch();
  try{
    const page=await browser.newPage();
    await page.route('http://localhost/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html>'}));
    await page.goto('http://localhost/');
    await page.addScriptTag({content:bundle.outputFiles[0].text});
    const results=await page.evaluate(async()=>{
      function deferred(){
        let resolve;
        const promise=new Promise(done=>{resolve=done;});
        return {promise,resolve};
      }
      const audio=new AudioContext();
      const stream=audio.createMediaStreamDestination().stream;
      const source=stream.getAudioTracks()[0];
      const access={url:'wss://example.invalid',token:'fixture'};
      let clone,displayUpdates;
      function delayedRoom(){
        displayUpdates=0;
        const adapter=new GuestAdapter.LiveKitGuest({changed:()=>displayUpdates++});
        const publishing=deferred(),release=deferred();
        adapter.room.connect=async()=>{};
        adapter.room.disconnect=async()=>{};
        adapter.room.localParticipant.publishTrack=async microphone=>{
          // Use the SDK's real audio track and the browser's real clone.
          clone=microphone.mediaStreamTrack??microphone;
          publishing.resolve();
          await release.promise;
          const publishedClone=clone;
          return {
            mute:async()=>{publishedClone.enabled=false;},
            unmute:async()=>{publishedClone.enabled=true;},
          };
        };
        return {adapter,publishing,release};
      }

      const first=delayedRoom();
      const connecting=first.adapter.connect(access,stream,false);
      await first.publishing.promise;
      const initiallySilent=!clone.enabled;
      await first.adapter.mute(true);
      await first.adapter.mute(false);
      await first.adapter.mute(true);
      const silentDuringPublication=!clone.enabled;
      first.release.resolve();
      await connecting;
      const stayedMuted=!clone.enabled;
      const changes=[];
      const clicks=[false,true,false,true].map(value=>{
        const done=first.adapter.mute(value);
        changes.push(clone.enabled);
        return done;
      });
      await Promise.all(clicks);
      const finalMuted=!clone.enabled;
      first.adapter.close();
      const closedTrack=clone.readyState;

      const second=delayedRoom();
      const cancelled=second.adapter.connect(access,stream,false);
      await second.publishing.promise;
      await second.adapter.mute(false);
      second.adapter.close();
      const stoppedImmediately=clone.readyState;
      second.release.resolve();
      await cancelled;
      await second.adapter.mute(false);
      const afterClose={state:clone.readyState,enabled:clone.enabled,displayUpdates};

      const third=delayedRoom();
      const restored=third.adapter.connect(access,stream,true);
      await third.publishing.promise;
      await third.adapter.mute(false);
      const silentUntilReady=!clone.enabled;
      third.release.resolve();
      await restored;
      const restoredUnmuted=clone.enabled;
      third.adapter.close();
      const originalTrack=source.readyState;
      source.stop();
      await audio.close();
      return {initiallySilent,silentDuringPublication,stayedMuted,changes,finalMuted,closedTrack,
        stoppedImmediately,afterClose,silentUntilReady,restoredUnmuted,originalTrack};
    });
    assert.deepEqual(results,{
      initiallySilent:true,silentDuringPublication:true,stayedMuted:true,changes:[true,false,true,false],
      finalMuted:true,closedTrack:'ended',stoppedImmediately:'ended',
      afterClose:{state:'ended',enabled:false,displayUpdates:0},silentUntilReady:true,
      restoredUnmuted:true,originalTrack:'live',
    });
    console.log(engine.name()+': microphone passed delayed publish, repeated mute, cancel and reconnect');
  }finally{await browser.close();}
}
