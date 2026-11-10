from tests.test_security import m,owner,client,reset
from tests.test_widgets import enable


def test_calendar_subscription_requires_sign_in_and_does_not_write_events(monkeypatch):
 calls=[]
 async def get(year):calls.append(year);return {'year':year,'events':[],'status':'unavailable','has_data':False}
 monkeypatch.setattr(m.production_calendar,'get',get)
 assert client().get('/api/calendar/holidays?year=2026').status_code==401 and calls==[]
 c,_,_=owner();enable(c,'calendar','tasks')
 before=c.get('/api/tasks').json()
 r=c.get('/api/calendar/holidays?year=2026');assert r.status_code==200 and r.json()['year']==2026 and calls==[2026]
 assert c.get('/api/tasks').json()==before
 assert c.put('/api/modules',json={'enabled':{'calendar':False}}).status_code==200
 # Hiding a widget keeps its read-only module available, like other widget APIs.
 assert c.get('/api/calendar/holidays?year=2026').status_code==200 and calls==[2026,2026]


def test_calendar_year_validation_is_localized_and_never_fetches_invalid_year(monkeypatch):
 calls=[]
 async def get(year):calls.append(year);return {}
 monkeypatch.setattr(m.production_calendar,'get',get)
 c,_,_=owner();enable(c,'calendar');c.headers['X-DrG-Language']='en'
 r=c.get('/api/calendar/holidays?year=9999');assert r.status_code==422 and r.json()['code']=='calendarYearInvalid' and 'between' in r.json()['detail']
 assert c.get('/api/calendar/holidays?year=invalid').status_code==422
 assert calls==[]


def test_calendar_widget_legacy_defaults_and_independent_settings():
 c,_,_=owner()
 layout=c.get('/api/dashboard/layout').json()
 layout['instances']=[{'id':'calendar_legacy','module_id':'calendar','size':'1x1','config':{}}]
 r=c.put('/api/dashboard/layout',json=layout)
 assert r.status_code==200,r.text
 assert r.json()['instances'][0]['config']=={'production':True,'thematic':True}
 layout=r.json();layout['instances'][0]['size']='1x2';layout['instances'][0]['config']={'production':False,'thematic':True}
 assert c.put('/api/dashboard/layout',json=layout).status_code==200
 saved=c.get('/api/dashboard/layout').json()
 assert saved['instances'][0]['config']=={'production':False,'thematic':True}
 saved['instances'][0]['config']['production']='false'
 assert c.put('/api/dashboard/layout',json=saved).status_code==422
