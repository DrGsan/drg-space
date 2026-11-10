import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mentionQuery,mentionCandidates} from '../web/modules/chat/mentions.js';
import {messageTextMarkup} from '../web/modules/chat/format.js';
test('mention completion uses caret boundaries without matching email addresses',()=>{
  assert.deepEqual(mentionQuery('Привет @bat потом',11),{start:7,end:11,query:'bat'});
  assert.equal(mentionQuery('mail@bat',8),null);assert.equal(mentionQuery('@@bat',5),null);
  assert.equal(mentionQuery('@',1).query,'');
  assert.deepEqual(mentionCandidates([{login:'Batman'},{login:'cat'}],'bat'),[{login:'Batman'}]);
});
test('only canonical members become escaped mentions and links stay intact',()=>{
  const members=[{id:'1',login:'batman',display_name:'<Мама>'}];
  const html=messageTextMarkup('@batman! <script> @outsider mail@batman https://host/@batman',members);
  assert.equal((html.match(/data-chat-mention/g)||[]).length,1);assert.ok(html.includes('&lt;Мама&gt;'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('href="https://host/@batman"'));assert.ok(html.includes('@outsider'));
});

test('usernames with dots or leading hyphens are matched exactly',()=>{
  const members=[{id:'1',login:'bat.man'},{id:'2',login:'-cat'}];
  const html=messageTextMarkup('@bat.man. @batXman @-cat!',members);
  assert.equal((html.match(/data-chat-mention/g)||[]).length,2);
  assert.ok(html.includes('>@bat.man</button>.'));
});
