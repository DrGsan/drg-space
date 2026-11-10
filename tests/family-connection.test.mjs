import test from 'node:test';
import assert from 'node:assert/strict';
import {connectionLabel,connectionDescription,connectionMarkup,botLabel,checkedMarkup} from '../web/modules/family-photos/connection-status.js';
import {syncMarkup} from '../web/modules/family-photos/sync-status.js';

test('migrated archives cannot claim automatic live synchronization even with an idle worker or stopped files',()=>{
 const connection={state:'migrated',checked_at:1792600000,last_success:1792590000,migrated_group_id:'-1009'};
 const html=syncMarkup({state:'idle',connection,history_complete:true,pending:0,batch:{},stopped:1,stopped_files:[]},{title:'Архив',kind:'basic'});
 assert(html.includes('Группа преобразована'));assert(html.includes('новые сообщения здесь не появляются'));
 assert(!html.includes('Новые сообщения синхронизируются автоматически'));assert(html.includes('data-fp-target-group="-1009"'));assert(!html.includes('Последняя успешная проверка'));assert(checkedMarkup(connection).includes('Последняя успешная проверка'));
});
test('source health and sending permissions are independent and copies never claim their sync was stopped',()=>{
 assert.equal(connectionLabel({state:'working'}),'Работает');assert.equal(botLabel({can_send:false,bot_reason:'not_member'}),'Бот недоступен в группе');
 const html=connectionMarkup({kind:'export',copy_created:1,connection:{state:'copy',hidden:true}});
 assert(html.includes('Копия скрыта'));assert(!html.includes('Синхронизация остановлена'));assert(!html.includes('fp-connection-bot'));assert.equal(botLabel({kind:'export',copy_created:1}),'Отправка: только чтение');
 assert(connectionDescription({state:'paused',wait_until:1792600000}).includes('Следующая попытка'));
});
test('source and bot diagnostics escape presentation and target IDs',()=>{
 const html=connectionMarkup({can_send:true,connection:{state:'migrated',migrated_group_id:'"<x>'}},{chat:true});
 assert(!html.includes('"<x>'));assert(html.includes('&lt;x&gt;'));assert(!html.includes('Отправка: старый архив'));assert.equal(botLabel({connection:{state:'migrated'}}),'Отправка: старый архив');
 assert(!connectionMarkup({can_send:true,connection:{state:'working'}}).includes('Отправка доступна'));assert.equal(botLabel({can_send:true}),'Отправка доступна');
});
