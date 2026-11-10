import test from 'node:test';
import assert from 'node:assert/strict';
import {filterRooms,unreadRoomCounts,messagePreview,messageTextMarkup,canEditMessage,roomDate,sameMessageGroup,chatDayLabel} from '../web/modules/chat/format.js';

test('history separators show Today, the current year date, or a date with its older year',()=>{
  const now=new Date(2026,9,21,20,14).getTime()/1000;
  assert.equal(chatDayLabel(now,now),'Сегодня');
  assert.equal(chatDayLabel(new Date(2026,9,20,23,59).getTime()/1000,now),'20 октября');
  assert.equal(chatDayLabel(new Date(2025,8,15).getTime()/1000,now),'15 сентября 2025');
  assert.equal(chatDayLabel(new Date(2025,9,1).getTime()/1000,now),'01 октября 2025');
});

test('room dates use local calendar age and include the year only across years',()=>{
  const stamp=(year,month,day,hour=12,minute=0)=>new Date(year,month-1,day,hour,minute).getTime()/1000;
  const now=stamp(2026,10,21,20,14);
  assert.equal(roomDate(stamp(2026,10,21,17,34),now),'17:34');
  assert.equal(roomDate(stamp(2026,10,20),now),'вт');
  assert.equal(roomDate(stamp(2026,10,15),now),'чт');
  assert.equal(roomDate(stamp(2026,10,14),now),'14.10');
  assert.equal(roomDate(stamp(2026,10,6),now),'06.10');
  assert.equal(roomDate(stamp(2025,8,15),now),'15.08.25');
  assert.equal(roomDate(stamp(2026,12,31,23,59),stamp(2027,1,1,0,1)),'чт');
  assert.equal(roomDate(NaN,now),'');
});

test('weekday cutoff survives daylight saving rather than counting elapsed 24-hour intervals',()=>{
  const previous=process.env.TZ;process.env.TZ='America/New_York';
  try{
    const stamp=(month,day,hour=12)=>new Date(2026,month-1,day,hour).getTime()/1000;
    assert.equal(roomDate(stamp(10,31,0),stamp(11,6,23)),'сб');
    assert.equal(roomDate(stamp(3,7,23),stamp(3,14,0)),'07.03');
  }finally{if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
});

test('message sequences break on sender changes, pauses, backwards time and midnight',()=>{
  const created=new Date(2026,9,21,16,48).getTime()/1000;
  const first={sender_id:'anna',sender:'Анна',created};
  assert(sameMessageGroup(first,{...first,created:created+299}));
  assert(sameMessageGroup(first,{...first,sender:'Новое имя',created:created+1}));
  assert(!sameMessageGroup(first,{...first,sender_id:'bob',created:created+1}));
  assert(!sameMessageGroup(first,{...first,created:created+300}));
  assert(!sameMessageGroup(first,{...first,created:created-1}));
  const midnight=new Date(2026,9,22).getTime()/1000;
  assert(!sameMessageGroup({...first,created:midnight-60},{...first,created:midnight+60}));
  assert(!sameMessageGroup(null,first));
});

test('message text escapes markup and only links ordinary HTTP addresses',()=>{
  const markup=messageTextMarkup('<img src=x onerror="alert(1)"> https://example.com/a?x=1&y=2.\njavascript:alert(1)');
  assert.ok(markup.startsWith('&lt;img src=x onerror=&quot;alert(1)&quot;&gt; '));
  assert.equal((markup.match(/<a /g)||[]).length,1);
  assert.ok(markup.includes('href="https://example.com/a?x=1&amp;y=2"'));
  assert.ok(markup.includes('target="_blank" rel="noopener noreferrer"'));
  assert.ok(markup.includes('https://example.com/a?x=1&amp;y=2</a>.\njavascript:alert(1)'));
  assert.ok(!markup.includes('<img'));
});

test('credentialed and malformed addresses stay passive text',()=>{
  for(const body of ['https://user:password@example.com/private','http://user@example.com','https://[broken','javascript:alert(document.cookie)','data:text/html,<script>alert(1)</script>']){
    const markup=messageTextMarkup(body);
    assert.ok(!markup.includes('<a '),body);
    assert.ok(!markup.includes('<script>'),body);
  }
  const mixed=messageTextMarkup('https://example.com/" onclick="alert(1)');
  assert.ok(mixed.endsWith('&quot; onclick=&quot;alert(1)'));
  assert.ok(!mixed.includes('" onclick="'));
});

test('conversation filters accept the real friends/family API kinds and member search',()=>{
  const rooms=[
    {id:'personal',kind:'friends',name:'Александр',members:[{login:'demoowner'},{login:'Sasha'}],unread:2,favorite:true,pinned:false},
    {id:'family',kind:'family',name:'Дом',members:[{login:'demoowner'}],unread:0,favorite:false,pinned:false},
  ];
  const ids=(filter,query)=>filterRooms(rooms,filter,query).map(room=>room.id);
  assert.deepEqual(ids('direct'),['personal']);
  assert.deepEqual(ids('groups'),['family']);
  assert.deepEqual(ids('unread'),['personal']);
  assert.deepEqual(ids('favorites'),['personal']);
  assert.deepEqual(ids('all',' SASHA '),['personal']);
  assert.deepEqual(ids('all','ДОМ'),['family']);
  assert.deepEqual(ids('all','нет такого имени'),[]);
});

test('pinned conversations precede recent ones without changing the source list',()=>{
  const rooms=[
    {id:'old',kind:'family',name:'Old',pinned:false,last_message:{created:1}},
    {id:'recent',kind:'friends',name:'Recent',pinned:false,last_message:{created:50}},
    {id:'pinned',kind:'family',name:'Pinned',pinned:true,last_message:{created:2}},
  ];
  assert.deepEqual(filterRooms(rooms,'all').map(room=>room.id),['pinned','recent','old']);
  assert.deepEqual(rooms.map(room=>room.id),['old','recent','pinned']);
  const sameSecond=[
    {id:'earlier',kind:'family',name:'А',pinned:false,last_message:{created:50,id:1}},
    {id:'later',kind:'family',name:'Я',pinned:false,last_message:{created:50,id:2}},
  ];
  assert.deepEqual(filterRooms(sameSecond,'all').map(room=>room.id),['later','earlier']);
});

test('folder badges count unread conversations separately from message totals and match each filter',()=>{
  const rooms=[
    {id:'dm',kind:'friends',name:'Личный чат',unread:12,favorite:true},
    {id:'group',kind:'family',name:'Семья',unread:3,favorite:true},
    {id:'telegram:group',kind:'family',name:'Telegram',unread:1},
    {id:'read',kind:'friends',name:'Прочитан',unread:0},
  ];
  assert.deepEqual(unreadRoomCounts(rooms),{all:3,unread:3,favorites:2,direct:1,groups:2});
  for(const [filter,count] of Object.entries(unreadRoomCounts(rooms)))
    assert.equal(count,filterRooms(rooms,filter).filter(room=>room.unread>0).length);
  rooms[0].unread=0;
  assert.deepEqual(unreadRoomCounts(rooms),{all:2,unread:2,favorites:1,direct:0,groups:2});
  assert.deepEqual(unreadRoomCounts([]),{all:0,unread:0,favorites:0,direct:0,groups:0});
});

test('conversation previews describe media and empty conversations',()=>{
  assert.equal(messagePreview(null),'Пока нет сообщений');
  assert.equal(messagePreview({body:'  Привет  ',attachments:[{kind:'image'}]}),'Привет');
  assert.equal(messagePreview({body:'',attachments:[{kind:'image'},{kind:'video'},{kind:'file',name:'plan.txt'}]}),'Фото, Видео, plan.txt');
});

// UI follows the original deadline; the API independently enforces this too.
test('editing is available only to the author before the exact deadline',()=>{
  const message={sender_id:'alice',created:100,editable_until:86500,edited:86499};
  assert.equal(canEditMessage(message,'alice',86499.999),true);
  assert.equal(canEditMessage(message,'alice',86500),false);
  assert.equal(canEditMessage(message,'alice',86501),false);
  assert.equal(canEditMessage(message,'bob',101),false);
});
