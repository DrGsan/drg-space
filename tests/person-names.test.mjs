import test from 'node:test';
import assert from 'node:assert/strict';
import {setPeopleNames,rememberPeople,personName,personSearch,personNickname} from '../web/core/person-names.js';
import {messageTextMarkup,messagePreview,filterRooms} from '../web/modules/chat/format.js';
import {mentionQuery,mentionCandidates} from '../web/modules/chat/mentions.js';
const person={id:'a'.repeat(32),login:'alena.test',display_name:'Алёна'};

test('private names resolve immutable identities, preserve official names, and clear on account switch',()=>{
  setPeopleNames('viewer',{[person.id]:'Мама'});rememberPeople([person]);
  assert.equal(personName(person),'Мама');assert.equal(personName({login:person.login}),'Мама');
  assert.equal(person.display_name,'Алёна');assert.ok(personSearch(person).includes('алёна'));assert.ok(personSearch(person).includes('мама'));
  assert.equal(personName({...person,login:'changed-login'}),'Мама');
  setPeopleNames('another',{});assert.equal(personName(person),'Алёна');assert.equal(personNickname(person),'');
});
test('mention labels and previews personalize without changing stored tokens, links or email addresses',()=>{
  setPeopleNames('viewer',{[person.id]:'Мама'});rememberPeople([person]);
  const text='@alena.test https://example.org/@alena.test name@alena.test @alena.test.extra';
  const html=messageTextMarkup(text,[person]);assert.match(html,/>@Мама<\/button>/);assert.ok(html.includes('https://example.org/@alena.test'));assert.ok(html.includes('name@alena.test'));
  assert.equal(messagePreview({body:text,mentions:[person]}),'@Мама https://example.org/@alena.test name@alena.test @alena.test.extra');
  assert.equal(text.split(' ')[0],'@alena.test');
  assert.equal(mentionQuery('Привет @Ма',10)?.query,'ма');
  assert.deepEqual(mentionCandidates([person],'мам'),[person]);
  assert.equal(filterRooms([{name:'Алёна',members:[person],kind:'friends'}],'all','Мама').length,1);
  setPeopleNames(null,{});
});
test('private labels escape HTML and never borrow an alias from a reused login',()=>{
  setPeopleNames('viewer',{[person.id]:'<img src=x onerror=alert(1)>'});rememberPeople([person]);
  assert.ok(!messageTextMarkup('@alena.test',[person]).includes('<img'));
  assert.equal(personName({...person,id:'b'.repeat(32)}),'Алёна');
  setPeopleNames(null,{});
});
