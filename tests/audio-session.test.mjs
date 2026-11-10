import test from 'node:test';
import assert from 'node:assert/strict';
import {acquireCallAudioSession} from '../web/modules/chat/audio-session.js';

test('a call requests duplex audio and restores the previous profile once',()=>{
  const session={type:'auto'},release=acquireCallAudioSession(session);
  assert.equal(session.type,'play-and-record');release();assert.equal(session.type,'auto');
  session.type='ambient';release();assert.equal(session.type,'ambient');
});
test('late teardown cannot release the audio profile of another call',()=>{
  const session={type:'auto'},first=acquireCallAudioSession(session),second=acquireCallAudioSession(session);
  first();assert.equal(session.type,'play-and-record');second();assert.equal(session.type,'auto');
});
test('call teardown respects a subsequent media profile change',()=>{
  const session={type:'auto'},release=acquireCallAudioSession(session);
  session.type='playback';release();assert.equal(session.type,'playback');
});
test('unavailable or rejected AudioSession APIs leave calls usable',()=>{
  acquireCallAudioSession(undefined)();
  const session={get type(){return 'auto';},set type(value){throw new Error('Unsupported');}};
  acquireCallAudioSession(session)();assert.equal(session.type,'auto');
});
