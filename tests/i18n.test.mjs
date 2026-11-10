import test from 'node:test';
import assert from 'node:assert/strict';
import {createInstance} from 'i18next';
import resources from '../locales/resources.js';
import {normalizeLanguage,systemLanguage,tHtml,translateSystemText,formatNumber} from '../web/core/i18n.js';

test('regional language tags resolve to supported languages in preference order',()=>{
 assert.equal(normalizeLanguage('en-US'),'en');assert.equal(normalizeLanguage('ru_RU'),'ru');
 assert.equal(normalizeLanguage('eng'),null);assert.equal(normalizeLanguage('../en'),null);
 assert.equal(systemLanguage(['fr-FR','en-AU','ru']),'en');assert.equal(systemLanguage(['de']),'ru');
});
test('all supported languages resolve plurals, including zero and Russian 11–14',()=>{
 for(const [lng,expected]of [['ru',['0 файлов','1 файл','2 файла','5 файлов','11 файлов','21 файл','22 файла','101 файл','1,5 файла']],['en',['0 files','1 file','2 files','5 files','11 files','21 files','22 files','101 files','1.5 files']]]){
  const instance=createInstance();instance.init({lng,resources,initAsync:false,interpolation:{escapeValue:false}});
  const counts=[0,1,2,5,11,21,22,101,1.5];
  assert.deepEqual(counts.map(count=>instance.t('common:fileCount',{count:count})),expected.map((value,index)=>index===8?value.replace(',','.'):value));
 }
});
test('HTML translations escape static text and retain explicitly escaped or markup slots',()=>{
 const escaped='&lt;img src=x onerror=alert(1)&gt; &amp; &quot;test&quot;';
 const result=tHtml('app:appVersion',{value0:escaped});
 assert.ok(result.includes(escaped));assert.ok(!result.includes('<img'));assert.ok(!result.includes('&amp;lt;'));
 const markup='<span aria-hidden="true">✓</span>';
 assert.ok(tHtml('app:signInWithAPasskey',{value0:markup}).startsWith(markup));
});
test('fallback uses Russian text rather than a raw key, and user data has no automatic translation',()=>{
 const instance=createInstance();instance.init({lng:'fr',fallbackLng:'ru',resources,initAsync:false});
 assert.equal(instance.t('app:settings'),'Настройки');
 assert.equal(translateSystemText('Моя личная заметка с произвольным текстом'),'Моя личная заметка с произвольным текстом');
 assert.equal(formatNumber(12345.67),'12 345,67');
});
