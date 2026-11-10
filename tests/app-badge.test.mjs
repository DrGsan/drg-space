import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createAppBadge} from '../web/core/app-badge.js';

test('icon badge follows unread totals, deduplicates updates and clears on zero',async()=>{
  const calls=[],badge=createAppBadge({navigator:{async setAppBadge(n){calls.push(n);},async clearAppBadge(){calls.push(0);}}});
  await badge.set(3);await badge.set(3);await badge.set(2);await badge.set(0);
  assert.deepEqual(calls,[3,2,0]);
  await badge.set(0,{force:true});assert.deepEqual(calls,[3,2,0,0]);
});

test('logout clear follows outstanding updates and the next account can set its own total',async()=>{
  const calls=[];let release;
  const badge=createAppBadge({navigator:{async setAppBadge(n){calls.push(n);if(n===5)await new Promise(resolve=>release=resolve);},async clearAppBadge(){calls.push(0);}}});
  badge.set(5);await Promise.resolve();badge.set(0,{force:true});const next=badge.set(1);
  assert.deepEqual(calls,[5]);release();await next;assert.deepEqual(calls,[5,0,1]);
});

test('unsupported devices, invalid counts and rejected permission do not break the app',async()=>{
  await createAppBadge({}).set(2);
  let attempts=0;
  const badge=createAppBadge({navigator:{async setAppBadge(){attempts++;throw new Error('denied');},async clearAppBadge(){}}});
  for(const n of [-1,NaN,Infinity,1.5,'2'])await badge.set(n);
  assert.equal(attempts,0);await badge.set(2);await badge.set(2);assert.equal(attempts,2);
});
