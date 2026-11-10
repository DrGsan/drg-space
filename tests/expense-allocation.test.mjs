import test from 'node:test';
import assert from 'node:assert/strict';
import {allocate,allocateWeighted,kopecks} from '../web/modules/expenses/allocation.js';
test('manual shares fix amounts; empty people divide remaining cost',()=>{
  assert.deepEqual(allocate(130000,['me','vova','pasha'],{me:'300'}),{me:30000,vova:50000,pasha:50000});
  assert.deepEqual(allocate(130000,['me','vova','pasha'],{me:'300',vova:'200'}),{me:30000,vova:20000,pasha:80000});
});
test('rounding keeps every kopeck, and explicit zero is a fixed share',()=>{
  assert.deepEqual(allocate(100,['a','b','c']),{a:34,b:33,c:33});
  assert.deepEqual(allocate(101,['a','b','c'],{a:'0'}),{a:0,b:51,c:50});
  assert.equal(kopecks('10,05'),1005);
});
test('invalid or inconsistent amounts cannot be submitted',()=>{
  for(const input of ['-1','1.001','1e2','Infinity','NaN'])assert.throws(()=>kopecks(input));
  assert.throws(()=>allocate(100,['a','b'],{a:'2'}));
  assert.throws(()=>allocate(100,['a','b'],{a:'0.2',b:'0.3'}));
  assert.throws(()=>allocate(100,[]));
});
test('weighted splits preserve the total, deterministic remainder and explicit bounds',()=>{
  assert.deepEqual(allocateWeighted(180000,['a','b','c'],{a:2}),{a:90000,b:45000,c:45000});
  assert.deepEqual(allocateWeighted(10001,['a','b','c']),{a:3334,b:3334,c:3333});
  assert.throws(()=>allocateWeighted(100,['a'],{a:0}));
  assert.throws(()=>allocateWeighted(100,['a'],{a:1.5}));
  assert.throws(()=>allocateWeighted(100,['a','a']));
});
