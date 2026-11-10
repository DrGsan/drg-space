import json
from pathlib import Path
from PIL import Image
from tests.test_security import m,owner,member,client,reset
from tests.test_next_features import ok
from tests.test_widgets import enable
from tests.test_personal_data import export,headers,unseal

PEOPLE=[{'id':'me','name':'Owner'},{'id':'friend','name':'Friend'}]
def create(c,currency=None):
    body={'title':'Bill','participants':PEOPLE}
    if currency is not None:body['currency']=currency
    return ok(c.post('/api/expenses',json=body))

def test_currency_remembers_only_successful_creation_and_is_account_scoped():
    c,_,_=owner();g,_=member(c);enable(g,'expenses')
    assert ok(c.get('/api/expenses/options'))['default_currency']=='RUB'
    usd=create(c,'USD');assert usd['currency']=='USD'
    assert create(c)['currency']=='USD'
    assert c.post('/api/expenses',json={'title':'','participants':PEOPLE,'currency':'EUR'}).status_code==422
    assert ok(c.get('/api/expenses/options'))['default_currency']=='USD'
    assert ok(g.get('/api/expenses/options'))['default_currency']=='RUB'
    assert create(c,'RUB')['currency']=='RUB'
    assert create(c)['currency']=='RUB'
    assert create(c,'EUR')['currency']=='EUR'
    assert c.post('/api/expenses',json={'title':'Bill','participants':PEOPLE,'currency':'GBP'}).status_code==422

def test_existing_currency_survives_legacy_update_and_cannot_relabel_money():
    c,_,_=owner();bill=create(c,'USD')
    bill=ok(c.post('/api/expenses/'+bill['id']+'/entries',json={'amount':10001,'payer':'me','allocations':{'me':5001,'friend':5000},'category':'restaurant','event_version':bill['version']}))
    body={'title':'Renamed','participants':PEOPLE,'retention_days':30,'version':bill['version']}
    bill=ok(c.put('/api/expenses/'+bill['id'],json=body));assert bill['currency']=='USD'
    body.update(currency='RUB',version=bill['version'])
    assert c.put('/api/expenses/'+bill['id'],json=body).status_code==409
    assert ok(c.get('/api/expenses/'+bill['id']))['currency']=='USD'
    with m.db() as db:
        row=db.execute('SELECT payload FROM expense_events WHERE id=?',(bill['id'],)).fetchone();payload=json.loads(row[0]);payload.pop('currency');db.execute('UPDATE expense_events SET payload=? WHERE id=?',(json.dumps(payload),bill['id']))
    assert ok(c.get('/api/expenses/'+bill['id']))['currency']=='RUB'

def test_repayment_offsets_debt_without_becoming_a_purchase_and_share_keeps_currency():
    c,_,_=owner();bill=create(c,'EUR')
    for data in [
        {'amount':60000,'payer':'me','allocations':{'me':30000,'friend':30000},'kind':'expense','category':'groceries'},
        {'amount':20000,'payer':'friend','allocations':{'me':20000},'kind':'debt','category':'mixed'},
        {'amount':10000,'payer':'friend','allocations':{'me':10000},'kind':'repayment','category':'mixed'}]:
        bill=ok(c.post('/api/expenses/'+bill['id']+'/entries',json={**data,'event_version':bill['version']}))
    assert bill['balance']=={'me':0,'friend':0} and bill['debts']==[]
    assert bill['cost']=={'me':30000,'friend':30000} and bill['paid']['me']==60000
    assert bill['categories']['groceries']==30000
    share=ok(c.post('/api/expenses/'+bill['id']+'/shares',json={'duration':2,'unit':'days'}))
    public=ok(client().get('/api/shared-expenses/'+share['token']))
    assert public['currency']=='EUR' and public['cost']==bill['cost']

def test_archive_roundtrip_preserves_currency_and_legacy_categories_without_replacing_preference():
    c,_,_=owner();g,_=member(c);enable(g,'expenses')
    bill=create(c,'USD');bill=ok(c.post('/api/expenses/'+bill['id']+'/entries',json={'amount':500,'payer':'me','allocations':{'friend':500},'category':'beer','event_version':bill['version']}))
    create(g,'EUR');archive=export(c);manifest,_=unseal(archive)
    assert manifest['data']['expenses'][0]['currency']=='USD'
    ok(g.post('/api/account/data/import',content=archive,headers=headers(password='Friend-password-123')))
    copied=next(e for e in ok(g.get('/api/expenses')) if e['currency']=='USD')
    assert copied['entries'][0]['category']=='beer' and copied['cost']['friend']==500
    assert ok(g.get('/api/expenses/options'))['default_currency']=='EUR'

def test_neutral_setup_and_complete_shared_artwork_catalog():
    c,_,_=owner();bill=ok(c.post('/api/expenses',json={'title':'Solo','participants':[{'id':'me','name':'Owner'}]}))
    assert bill['participants']==[{'id':'me','name':'Owner'}]
    options=ok(c.get('/api/expenses/options'));assert len(options['categories'])==30
    for item in options['categories']:
        assert item['ru'] and item['en']
        with Image.open(Path('web/public/assets/expense-categories')/(item['id']+'.webp')) as image:assert image.size==(256,256)
    from app.modules.widget_models import ExpenseConfig
    assert ExpenseConfig().participants==[]
