"""One bounded, persistent subscription shared by all accounts and workers."""
import asyncio
import json
import logging
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

from icalendar import Calendar
from app.data_crypto import DataCipher, StorageError
from app.process_locks import async_file_lock
from app.safe_fetch import fetch_public_resource

SOURCE='https://ovodov.me/trud.ics'
CHECK_SECONDS=86400
RETRY_SECONDS=3600
MAX_BYTES=512*1024
MAX_EVENTS=4096
CONTEXT='production-calendar:v1'
NAMES={
    'Новогодние каникулы':('newYearHolidays','holiday'),
    'Рождество Христово':('christmas','holiday'),
    'День защитника Отечества':('defenderDay','holiday'),
    'Международный женский день':('womenDay','holiday'),
    'Праздник Весны и Труда':('labourDay','holiday'),
    'День Победы':('victoryDay','holiday'),
    'День России':('russiaDay','holiday'),
    'День народного единства':('unityDay','holiday'),
    'Выходные':('dayOff','day_off'),
    'Выходной':('dayOff','day_off'),
    '* Сокращённый день':('shortDay','short_day'),
    'Сокращённый день':('shortDay','short_day'),
}

def parse_calendar(body):
    """Preserve all-day dates and exclusive DTEND; never turn an event into UTC."""
    if len(body)>MAX_BYTES:raise ValueError('Calendar exceeds the size limit')
    calendar=Calendar.from_ical(body)
    if calendar.name!='VCALENDAR' or calendar.errors:raise ValueError('Invalid calendar')
    components=calendar.walk('VEVENT')
    if not components or len(components)>MAX_EVENTS:raise ValueError('Invalid event count')
    chosen={}
    for item in components:
        uid=str(item.get('UID','')).strip()
        if item.errors or not uid or len(uid)>512:raise ValueError('Invalid calendar event')
        if any(item.get(name) is not None for name in ('RRULE','RDATE','EXDATE','RECURRENCE-ID')):
            raise ValueError('The production source must supply explicit dates')
        sequence=int(item.get('SEQUENCE',0))
        if not 0<=sequence<2**31:raise ValueError('Invalid event sequence')
        previous=chosen.get(uid)
        if previous is None or sequence>=previous[0]:chosen[uid]=(sequence,item)
    events=[]
    for _,item in chosen.values():
        if str(item.get('STATUS','')).upper()=='CANCELLED':continue
        start=item.decoded('DTSTART');end=item.decoded('DTEND') if 'DTEND' in item else start+timedelta(days=1)
        if type(start) is not date or type(end) is not date or not 0<(end-start).days<=366:
            raise ValueError('Expected a bounded all-day date interval')
        title=str(item.get('SUMMARY','')).strip()
        if not title or len(title)>512:raise ValueError('Invalid event title')
        name,kind=NAMES.get(title,('', 'observance'))
        events.append({'start':start.isoformat(),'end':end.isoformat(),'title':title,'name':name,'kind':kind})
    if not events:raise ValueError('Empty production calendar')
    return sorted(events,key=lambda row:(row['start'],row['end'],row['title']))

class ProductionCalendar:
    def __init__(self,root,now,cipher=None,fetch=None):
        self.root=Path(root);self.path=self.root/'provider-cache'/'production-calendar-v1.cache'
        self.now=now;self._cipher=cipher;self.fetch=fetch or fetch_public_resource
        self._signature=None;self._state=None;self._task=None;self._retry_after=0

    @property
    def cipher(self):
        if self._cipher is None:self._cipher=DataCipher.from_env()
        return self._cipher

    def _read(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():raise StorageError('Unsafe calendar cache path')
        try:
            stat=self.path.stat();signature=(stat.st_ino,stat.st_mtime_ns,stat.st_size)
            if signature==self._signature and self._state is not None:return self._state
            if stat.st_size>MAX_BYTES*4:raise ValueError('Oversized cache')
            state=json.loads(self.cipher.open(self.path.read_bytes(),CONTEXT))
            if state.get('version')!=1 or not isinstance(state.get('events'),list) or len(state['events'])>MAX_EVENTS:raise ValueError('Invalid cache')
            for key in ('checked','updated','next_check'):
                if type(state.get(key)) is not int or state[key]<0:raise ValueError('Invalid cache time')
            for key in ('etag','last_modified'):
                if not isinstance(state.get(key),str) or len(state[key])>512:raise ValueError('Invalid validator')
            for row in state['events']:
                if set(row)!={'start','end','title','name','kind'} or not all(isinstance(v,str) for v in row.values()):raise ValueError('Invalid cached event')
                if row['kind'] not in ('holiday','day_off','short_day','observance') or not 0<(date.fromisoformat(row['end'])-date.fromisoformat(row['start'])).days<=366:raise ValueError('Invalid cached dates')
            self._signature=signature;self._state=state;return state
        except FileNotFoundError:return {'version':1,'events':[],'checked':0,'updated':0,'next_check':0,'etag':'','last_modified':''}
        except (ValueError,TypeError,KeyError,StorageError):
            # Invalid derived data is replaced only by a newly validated subscription.
            self._signature=None
            return {'version':1,'events':[],'checked':0,'updated':0,'next_check':0,'etag':'','last_modified':''}

    def _write(self,state):
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.path.parent.is_symlink() or self.path.is_symlink():raise StorageError('Unsafe calendar cache path')
        body=self.cipher.seal(json.dumps(state,ensure_ascii=False,allow_nan=False).encode(),CONTEXT)
        fd,name=tempfile.mkstemp(prefix='.production-calendar-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'wb') as output:output.write(body);output.flush();os.fsync(output.fileno())
            os.replace(name,self.path)
        finally:Path(name).unlink(missing_ok=True)
        self._signature=None

    async def refresh(self):
        async with async_file_lock(self.root,'production-calendar.lock'):
            state=self._read();stamp=self.now()
            if state['next_check']>stamp:return state
            try:
                response=await self.fetch(SOURCE,max_bytes=MAX_BYTES,accept='text/calendar',etag=state['etag'],last_modified=state['last_modified'])
                if response.status==304:
                    if not state['events']:raise ValueError('304 without a calendar')
                    events=state['events']
                elif response.status==200:events=parse_calendar(response.body)
                else:raise ValueError('Unexpected calendar response')
                validator=lambda value:value if len(value)<=512 and not any(ord(c)<32 for c in value) else ''
                etag=validator(response.etag);modified=validator(response.last_modified)
                state={**state,'events':events,'checked':stamp,'updated':stamp,'next_check':stamp+CHECK_SECONDS,'etag':(etag or state['etag']) if response.status==304 else etag,'last_modified':(modified or state['last_modified']) if response.status==304 else modified}
            except Exception as error:
                # Keep the last successful calendar; an outage is not an empty calendar.
                logging.getLogger('drg.calendar').warning('Production calendar refresh failed (%s); keeping previous data.',type(error).__name__)
                state={**state,'checked':stamp,'next_check':stamp+RETRY_SECONDS}
            self._write(state);return state

    async def get(self,year):
        state=self._read()
        if max(state['next_check'],self._retry_after)<=self.now():
            if self._task is None or self._task.done():
                self._task=asyncio.create_task(self.refresh());self._task.add_done_callback(self._finished)
            if not state['events']:state=await asyncio.shield(self._task)
        events=[row for row in state['events'] if row['start']<f'{year+1:04d}-01-01' and row['end']>f'{year:04d}-01-01']
        years=sorted({year for row in state['events'] for year in range(date.fromisoformat(row['start']).year,(date.fromisoformat(row['end'])-timedelta(days=1)).year+1)})
        stale=state['updated']<state['checked'] or self.now()>=state['updated']+CHECK_SECONDS
        return {'year':year,'events':events,'years':years,'source':SOURCE,'checked_at':state['checked'] or None,'updated_at':state['updated'] or None,'next_check_at':max(state['next_check'],self._retry_after),'refreshing':bool(self._task and not self._task.done()),'status':'unavailable' if not state['events'] else 'stale' if stale else 'ready','has_data':year in years}

    def _finished(self,task):
        if not task.cancelled() and task.exception() is not None:
            self._retry_after=self.now()+RETRY_SECONDS
            logging.getLogger('drg.calendar').warning('Production calendar cache unavailable; retrying later.')

    async def close(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:await self._task
            except asyncio.CancelledError:pass
