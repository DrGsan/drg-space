import test from 'node:test';
import assert from 'node:assert/strict';
import {albumRows,albumMarkup,telegramAlbums,fitAlbums} from '../web/modules/chat/media.js';
const file=id=>({id:String(id),kind:'photo',url:'/private/'+id,state:'ready'});
test('mosaics keep file order and split oversized visual collections into rows',()=>{for(let count=1;count<=23;count++){const files=Array.from({length:count},(_,i)=>file(i));assert.deepEqual(albumRows(files).flat(),files);assert(albumRows(files).every(row=>row.length<=3));}assert.deepEqual(albumRows(Array.from({length:5},(_,i)=>file(i))).map(row=>row.length),[2,3]);});
test('Telegram albums preserve source identities, captions and explicit boundaries',()=>{const rows=[1,2,3].map(id=>({id,group_id:'g',sender_id:'u',album_id:'a',media:[file(id)],text:id===1?'Caption':'',created:100}));const grouped=telegramAlbums(rows);assert.equal(grouped.length,1);assert.deepEqual(grouped[0].sources.map(m=>m.id),[1,2,3]);assert.equal(grouped[0].text,'Caption');assert.equal(rows[0].media.length,1);assert.equal(telegramAlbums(rows.map(m=>({...m,album_id:null}))).length,3);assert.equal(telegramAlbums([rows[0],{...rows[1],sender_id:'v'}]).length,2);assert.equal(telegramAlbums(Array.from({length:21},(_,i)=>({...rows[0],id:i}))).length,3);});
test('visual previews have centered play controls and never show file labels or sizes',()=>{const html=albumMarkup([{...file(1),name:'PRIVATE-FILENAME.jpg',size:123},{...file(2),kind:'video',thumbnail_url:'/cover'}],'data-chat-media-id');assert(!html.includes('PRIVATE-FILENAME'));assert(!html.includes('<small'));assert(html.includes('chat-media-play'));assert(html.includes('data-chat-media-id="2"'));assert(!html.includes('controls'));assert(!albumMarkup([{...file(3),state:'pending'}],'data-fp-chat-file').includes('src='));});
test('portrait tiles fill their row and single images retain unrestricted source proportions',()=>{
  for(const [single,dimensions] of [[true,[[80,800]]],[true,[[900,80]]],[false,[[80,800],[80,800]]]]){
    const message={style:{}},album={style:{removeProperty(){}},classList:{contains:()=>single,toggle(){}},querySelectorAll:()=>children,closest:()=>message};
    const children=dimensions.map(([naturalWidth,naturalHeight])=>({style:{},querySelector:()=>({naturalWidth,naturalHeight})}));
    const row={isConnected:true,children,style:{},closest:()=>album,querySelectorAll:()=>[]};
    fitAlbums({querySelectorAll:selector=>selector==='.chat-media-row'?[row]:[]});
    assert(children.reduce((sum,tile)=>sum+Number(tile.style.flex),0)>=1);
    if(single){assert.equal(children[0].style.flex,'1');assert.equal(Number(row.style.aspectRatio),dimensions[0][0]/dimensions[0][1]);}
  }
});

test('an old album keeps reactions and UTF-16 custom emoji from every source',()=>{
 const rows=[{id:1,group_id:'g',sender_id:'u',album_id:'album',media:[file(1)],text:'😎'},{id:2,group_id:'g',sender_id:'u',album_id:'album',media:[file(2)],text:'кот 🐈',custom_emojis:[{offset:4,length:2,document_id:'9'}],reactions:[{emoji:'😁',count:2}],site_reactions:[{emoji:'❤️',count:1}]}];
 const album=telegramAlbums(rows)[0];assert.equal(album.reactions[0].count,2);assert.equal(album.site_reactions[0].emoji,'❤️');assert.equal(album.custom_emojis[0].offset,7);assert.equal(rows[1].custom_emojis[0].offset,4);assert.equal(rows[0].reactions,undefined);
});

 test('a portal batch stays together even when a Telegram participant replies between files',()=>{const own={group_id:'g',sender_id:'site:u',site_send_id:'job',album_id:'site:job',created:100};const grouped=telegramAlbums([{...own,id:1,text:'Подпись',media:[file(1)]},{id:2,sender_id:'v',text:'Ответ',media:[]},{...own,id:3,text:'',media:[file(3)]}]);assert.equal(grouped.length,2);assert.deepEqual(grouped[0].sources.map(m=>m.id),[1,3]);assert.equal(grouped[1].id,2);});

test('GIF originals and Telegram MP4 animations have a GIF badge without video controls',()=>{
 for(const mime of ['image/gif','video/mp4']){
  const html=albumMarkup([{...file(1),kind:'animation',mime,thumbnail_url:'/still.jpg'}],'data-fp-chat-file');
  assert(html.includes('>GIF</span>'));assert(!html.includes('data-video-remaining'));assert(!html.includes('chat-video-progress'));assert(!html.includes('chat-media-play'));
  if(mime==='image/gif'){assert(html.includes('src="/private/1"'));assert(!html.includes('/still.jpg'));}else{assert(html.includes('data-animation-preview'));assert(!html.includes('data-video-preview'));assert(!html.includes('controls'));}
 }
});
