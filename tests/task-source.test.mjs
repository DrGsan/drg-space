import {test} from 'node:test';
import assert from 'node:assert/strict';
import {taskSource,sourceTasks,matchesSource,taskDepth,taskDescendants} from '../web/modules/dashboard/widgets/task-source.js';
import {matchesView,smartViews} from '../web/modules/tasks/format.js';
const uid='my-account',lists=[{id:'default_'+uid,name:'Напоминания',is_default:true,can_manage:true},{id:'work-list',name:'Работа',shared:true},{id:'default_friend',name:'Список друга',is_default:true,can_manage:false}];
const tasks=[{id:'undated',list_id:'default_'+uid,day:'',done:0},{id:'today',list_id:'default_'+uid,day:'2026-10-14',done:0,flagged:true},{id:'tomorrow',list_id:'work-list',day:'2026-10-15',done:0},{id:'shared-today',list_id:'work-list',day:'2026-10-14',done:1},{id:'foreign-undated',list_id:'default_friend',day:'',done:0,flagged:true}];
test('a default list contains undated tasks; a Today view spans accessible lists for the actual date',()=>{
  assert.deepEqual(sourceTasks(tasks,taskSource({},lists,uid),'2026-10-14').map(x=>x.id),['today','undated']);
  const source=taskSource({view:'today',list_id:null},lists,uid);
  assert.deepEqual(sourceTasks(tasks,source,'2026-10-14').map(x=>x.id),['today','shared-today']);
  assert.deepEqual(sourceTasks(tasks,source,'2026-10-15').map(x=>x.id),['tomorrow']);
});
test('explicit sources never fall back after deletion or revoked membership',()=>{
  const revoked=taskSource({view:'list',list_id:'removed'},lists,uid);
  assert.equal(revoked.unavailable,true);assert.equal(revoked.label,'Список недоступен');
  assert.deepEqual(sourceTasks(tasks,revoked,'2026-10-14'),[]);
  assert.equal(taskSource({},lists.filter(x=>x.id==='default_friend'),uid).unavailable,true);
});
test('all and flagged views preserve undated and shared content without altering another widget source',()=>{
  const selected=taskSource({view:'list',list_id:'work-list'},lists,uid),flags=taskSource({view:'flagged'},lists,uid);
  assert.deepEqual(new Set(sourceTasks(tasks,selected,'2026-10-14').map(x=>x.id)),new Set(['tomorrow','shared-today']));
  assert.deepEqual(new Set(sourceTasks(tasks,flags,'2026-10-14').map(x=>x.id)),new Set(['today','foreign-undated']));
  assert.equal(sourceTasks(tasks,taskSource({view:'all'},lists,uid),'2026-10-14').length,5);
  assert.equal(selected.list.id,'work-list');
});
test('subtask indentation follows visible ancestry and terminates for malformed cycles',()=>{
  const rows=[{id:'root'},{id:'child',parent_id:'root'},{id:'grandchild',parent_id:'child'}];
  assert.equal(taskDepth(rows[2],rows),2);assert.equal(taskDepth(rows[2],[rows[2]]),0);
  assert.equal(taskDepth({id:'a',parent_id:'b'},[{id:'a',parent_id:'b'},{id:'b',parent_id:'a'}]),2);
});
test('a high-priority subtask remains under its visible parent instead of preceding it',()=>{
  const rows=[{id:'child',list_id:'work-list',parent_id:'root',priority:3,day:''},{id:'root',list_id:'work-list',day:''}];
  assert.deepEqual(sourceTasks(rows,taskSource({view:'list',list_id:'work-list'},lists,uid),'2026-10-14').map(x=>x.id),['root','child']);
});
test('deletion checks all descendants, including children hidden by the widget filter',()=>{
  const rows=[{id:'root',flagged:true},{id:'child',parent_id:'root',flagged:false},{id:'grandchild',parent_id:'child',flagged:false},{id:'other'}];
  assert.deepEqual(sourceTasks(rows,taskSource({view:'flagged'},lists,uid),'2026-10-14').map(x=>x.id),['root']);
  assert.deepEqual(taskDescendants('root',rows),['child','grandchild']);
  assert.deepEqual(taskDescendants('other',rows),[]);
  assert.deepEqual(taskDescendants('a',[{id:'a',parent_id:'b'},{id:'b',parent_id:'a'}]),['b']);
});
test('source matching supports resolved lists and transient list ids without widening revoked sources',()=>{
  const owned=tasks[0],shared=tasks[2],done=tasks[3];
  assert.equal(matchesSource(owned,{view:'list',list_id:'default_'+uid},'2026-10-14'),true);
  assert.equal(matchesSource(shared,{view:'list',list:{id:'work-list'}},'2026-10-14'),true);
  assert.equal(matchesSource(owned,{view:'list',list_id:'work-list'},'2026-10-14'),false);
  assert.equal(matchesSource(owned,{view:'list',list_id:'missing'},'2026-10-14'),false);
  assert.equal(matchesSource(owned,{view:'list'},'2026-10-14'),false);
  assert.equal(matchesSource(done,{view:'today'},'2026-10-14'),true);
  assert.equal(matchesSource(shared,{view:'today'},'2026-10-14'),false);
  assert.equal(matchesSource(owned,{view:'flagged'},'2026-10-14'),false);
  assert.equal(matchesSource(tasks[1],{view:'flagged'},'2026-10-14'),true);
  assert.equal(matchesSource(owned,{view:'all'},'2026-10-14'),true);
});
test('compact summary counts use the selected list and the same smart-filter semantics as its destination',()=>{
  const date=new Date(),key=value=>`${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;
  const day=key(date),past=new Date(date),future=new Date(date);past.setDate(past.getDate()-1);future.setDate(future.getDate()+1);
  const rows=[
    {id:'undated',list_id:'work-list',day:'',done:0,priority:0},
    {id:'overdue',list_id:'work-list',day:key(past),done:0,flagged:true,priority:3},
    {id:'current',list_id:'work-list',day,done:0,priority:2},
    {id:'future',list_id:'work-list',day:key(future),done:0,priority:1},
    {id:'done',list_id:'work-list',day,done:1,flagged:true,priority:3},
    {id:'foreign',list_id:'default_'+uid,day,done:0,flagged:true,priority:3},
  ];
  const scope={view:'list',list_id:'work-list'},source=taskSource(scope,lists,uid),pool=sourceTasks(rows,source,day);
  const result=Object.fromEntries(smartViews.map(view=>[view.id,pool.filter(task=>matchesView(task,{kind:'smart',id:view.id})).length]));
  assert.deepEqual(result,{today:2,scheduled:3,all:4,flagged:1,important:1,completed:1});
  for(const view of smartViews){
    const expected=pool.filter(task=>matchesView(task,{kind:'smart',id:view.id})).map(task=>task.id).sort();
    const actual=rows.filter(task=>matchesView(task,{kind:'smart',id:view.id,scope})).map(task=>task.id).sort();
    assert.deepEqual(actual,expected,view.id);
  }
});
test('compact Today and Flagged sources keep their intersection when opening other summary filters',()=>{
  const date=new Date(),day=`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  const rows=[
    {id:'current',list_id:'work-list',day,done:0,flagged:true,priority:3},
    {id:'done-current',list_id:'work-list',day,done:1,flagged:true,priority:3},
    {id:'undated',list_id:'work-list',day:'',done:0,flagged:true,priority:0},
    {id:'unflagged',list_id:'work-list',day,done:0,flagged:false,priority:3},
  ];
  const ids=view=>rows.filter(task=>matchesView(task,view)).map(task=>task.id).sort();
  assert.deepEqual(ids({kind:'smart',id:'completed',scope:{view:'today'}}),['done-current']);
  assert.deepEqual(ids({kind:'smart',id:'all',scope:{view:'today'}}),['current','unflagged']);
  assert.deepEqual(ids({kind:'smart',id:'all',scope:{view:'flagged'}}),['current','undated']);
  assert.deepEqual(ids({kind:'smart',id:'important',scope:{view:'flagged'}}),['current']);
  assert.deepEqual(ids({kind:'smart',id:'completed',scope:{view:'list',list_id:'missing'}}),[]);
});
