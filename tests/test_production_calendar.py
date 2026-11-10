"""Updated all-day subscriptions never create personal events or stampede a provider."""
import asyncio
from datetime import date
import pytest
from app.production_calendar import ProductionCalendar,parse_calendar,CHECK_SECONDS,RETRY_SECONDS,MAX_BYTES
from app.data_crypto import DataCipher
from app.safe_fetch import PublicResource


def calendar(*events):
 return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//DrG test//EN\r\n'+''.join('BEGIN:VEVENT\r\n'+event+'\r\nEND:VEVENT\r\n' for event in events)+'END:VCALENDAR\r\n').encode()

EVENT='UID:holiday\r\nDTSTART;VALUE=DATE:20261104\r\nDTEND;VALUE=DATE:20261105\r\nSUMMARY:День народного единства'
BODY=calendar(EVENT,'UID:short\r\nDTSTART;VALUE=DATE:20261103\r\nSUMMARY:* Сокращённый день','UID:weekend\r\nDTSTART;VALUE=DATE:20261107\r\nDTEND;VALUE=DATE:20261109\r\nSUMMARY:Выходные')


def resource(body=BODY,status=200):return PublicResource(body,'https://ovodov.me/trud.ics','text/calendar',status,'"fixture-v1"','Sun, 08 Nov 2026 19:01:30 GMT')


def test_dates_are_civil_and_end_is_exclusive():
 rows=parse_calendar(BODY)
 assert rows[0]['start']=='2026-11-03' and rows[0]['end']=='2026-11-04' and rows[0]['kind']=='short_day'
 holiday=rows[1];assert holiday['start']=='2026-11-04' and holiday['end']=='2026-11-05' and holiday['kind']=='holiday' and holiday['name']=='unityDay'
 assert rows[2]['kind']=='day_off' and (date.fromisoformat(rows[2]['end'])-date.fromisoformat(rows[2]['start'])).days==2


def test_folded_text_revisions_and_cancellations():
 rows=parse_calendar(calendar(EVENT,EVENT.replace('SUMMARY:','SEQUENCE:1\r\nSUMMARY:'),'UID:x\r\nDTSTART;VALUE=DATE:20260101\r\nSUMMARY:New\\,\r\n  holiday\\; name\\nline','UID:gone\r\nDTSTART;VALUE=DATE:20260101\r\nSUMMARY:old','UID:gone\r\nSEQUENCE:2\r\nSTATUS:CANCELLED'))
 assert len(rows)==2 and rows[0]['title']=='New, holiday; name\nline'
 assert rows[0]['kind']=='observance'  # unknown source labels are not assumed to be days off


@pytest.mark.parametrize('body',[b'<html>not a calendar</html>',calendar(),calendar(EVENT.replace('20261105','20261104')),calendar(EVENT.replace('20261105','20291105')),calendar(EVENT.replace('DTSTART;VALUE=DATE:20261104','DTSTART:20261104T000000Z')),calendar(EVENT+'\r\nRRULE:FREQ=DAILY'),b'x'*(MAX_BYTES+1)])
def test_invalid_or_changed_source_contract_cannot_silently_replace_good_dates(body):
 with pytest.raises((ValueError,KeyError,TypeError)):parse_calendar(body)


def test_shared_cache_cold_start_parallel_workers_and_restart(tmp_path):
 async def run():
  stamp=[100];cipher=DataCipher.ephemeral();requests=[]
  async def fetch(*args,**kwargs):requests.append(kwargs);await asyncio.sleep(.02);return resource()
  a=ProductionCalendar(tmp_path,lambda:stamp[0],cipher,fetch);b=ProductionCalendar(tmp_path,lambda:stamp[0],cipher,fetch)
  results=await asyncio.gather(*(service.get(2026) for service in [a,b]*100))
  assert len(requests)==1 and all(r['status']=='ready' and len(r['events'])==3 for r in results)
  assert requests[0]['accept']=='text/calendar' and requests[0]['etag']==''
  assert 'День'.encode() not in a.path.read_bytes()
  assert not list(a.path.parent.glob('.production-calendar-*'))
  restarted=ProductionCalendar(tmp_path,lambda:stamp[0],cipher,fetch)
  assert (await restarted.get(2027))['has_data'] is False and len(requests)==1
  await a.close();await b.close();await restarted.close()
 asyncio.run(run())


def test_conditional_refresh_failure_and_retry_keep_last_good_snapshot(tmp_path):
 async def run():
  stamp=[100];calls=[];responses=[resource(),resource(b'not parsed',304),OSError('offline'),resource(BODY.replace(b'20261104',b'20271104').replace(b'20261105',b'20271105'))]
  async def fetch(*args,**kwargs):
   calls.append(kwargs);value=responses.pop(0)
   if isinstance(value,Exception):raise value
   return value
  service=ProductionCalendar(tmp_path,lambda:stamp[0],DataCipher.ephemeral(),fetch)
  first=await service.get(2026);stamp[0]+=CHECK_SECONDS
  assert (await service.get(2026))['events']==first['events'];await service._task
  assert calls[1]['etag']=='"fixture-v1"' and calls[1]['last_modified']
  assert (await service.get(2026))['status']=='ready'
  stamp[0]+=CHECK_SECONDS;await service.get(2026);await service._task
  failed=await service.get(2026);assert failed['status']=='stale' and failed['events']==first['events'] and len(calls)==3
  stamp[0]+=RETRY_SECONDS;await service.get(2027);await service._task
  assert (await service.get(2027))['has_data'] and len(calls)==4
  await service.close()
 asyncio.run(run())


def test_failed_cold_source_has_one_shared_backoff_including_restart(tmp_path):
 async def run():
  calls=[];cipher=DataCipher.ephemeral()
  async def fetch(*args,**kwargs):calls.append(1);return resource(b'broken')
  service=ProductionCalendar(tmp_path,lambda:100,cipher,fetch)
  results=await asyncio.gather(*(service.get(2026) for _ in range(100)))
  assert len(calls)==1 and all(r['status']=='unavailable' and not r['has_data'] for r in results)
  other=ProductionCalendar(tmp_path,lambda:101,cipher,fetch)
  assert (await other.get(2026))['events']==[] and len(calls)==1
  await service.close();await other.close()
 asyncio.run(run())
