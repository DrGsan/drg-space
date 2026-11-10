import json
import pytest
from tests.test_security import owner,member,reset
from app import main as m
from app.formulas import evaluate,FormulaError


def database(c,title='Database',space=None):
    r=c.post('/api/databases',json={'title':title,'space_id':space});assert r.status_code==200,r.text
    return r.json()['id']

def configure(c,nid,properties,views=None):
    current=c.get('/api/databases/'+nid).json()
    payload={'properties':properties,'views':views or [{'id':'table','name':'Таблица','type':'table'}],'version':current['version']}
    return c.put('/api/databases/'+nid,json=payload)

def record(c,nid,title,properties):
    r=c.post('/api/databases/'+nid+'/records',json={'title':title,'properties':properties});assert r.status_code==200,r.text
    return r.json()['id']

@pytest.mark.parametrize('expression,expected',[
 ('prop("Цена") * 2',6),('if(prop("Цена") > 2, "Да", "Нет")','Да'),
 ('[1,2,3].sum()',6),('true && !false',True),('2 ^ 3',8),
 ('dateAdd("2026-10-12", 2, "months")','2026-12-12'),
 ('if(false, 1/0, 4)',4),('dateBetween("2026-10-12", "2026-10-10", "days")',2),
 ('concat("А", "Б")','АБ'),('prop("Цена") >= 2 && prop("Цена") < 4',True)])
def test_bounded_formula_language(expression,expected):assert evaluate(expression,lambda key:3)==expected

@pytest.mark.parametrize('expression',['__import__("os")','open("/etc/passwd")','().__class__','[x for x in [1,2]]','lambda:1','2 ** 1000000','prop()','prop(1)','1 / 0'])
def test_formulas_cannot_execute_code_or_unbounded_math(expression):
    with pytest.raises(FormulaError):evaluate(expression,lambda key:3)


def test_formulas_compute_without_persisting_derived_values_and_reject_cycles():
    c,_,_=owner();nid=database(c)
    props=[{'id':'price','name':'Цена','type':'number'},{'id':'count','name':'Количество','type':'number'},{'id':'sum','name':'Сумма','type':'formula','expression':'prop("Цена") * prop("Количество")'}]
    r=configure(c,nid,props);assert r.status_code==200,r.text
    rid=record(c,nid,'First',{'price':4,'count':3})
    data=c.get('/api/databases/'+nid).json();assert data['records'][0]['properties']['sum']==12
    with m.db() as d:assert 'sum' not in json.loads(d.execute('SELECT properties FROM database_records WHERE note_id=?',(rid,)).fetchone()[0])
    assert c.patch('/api/database-records/'+rid,json={'properties':{'sum':999},'version':1}).status_code==422
    props[-1]['expression']='prop("Сумма") + 1';assert configure(c,nid,props).status_code==422
    preview=c.post('/api/databases/'+nid+'/formula-preview',json={'expression':'round(prop("Цена") * 2.5)'});assert preview.json()['value']==10


def test_relations_rollups_and_two_way_updates():
    c,_,_=owner();products=database(c,'Товары');orders=database(c,'Заказы')
    assert configure(c,products,[{'id':'price','name':'Цена','type':'number'}]).status_code==200
    apple=record(c,products,'Яблоко',{'price':10});pear=record(c,products,'Груша',{'price':20})
    props=[{'id':'items','name':'Товары','type':'relation','database_id':products,'two_way':True,'multiple':True},{'id':'total','name':'Итого','type':'rollup','relation_id':'items','property':'price','aggregate':'sum'}]
    r=configure(c,orders,props);assert r.status_code==200,r.text
    inverse=r.json()['properties'][0]['inverse_id']
    order=record(c,orders,'Корзина',{'items':[apple,pear]})
    data=c.get('/api/databases/'+orders).json();assert data['records'][0]['properties']['total']==30
    product_rows=c.get('/api/databases/'+products).json()['records'];assert all(row['properties'][inverse]==[order] for row in product_rows)
    assert c.patch('/api/database-records/'+order,json={'properties':{'items':[pear]},'version':1}).status_code==200
    data=c.get('/api/databases/'+orders).json();assert data['records'][0]['properties']['total']==20
    product_rows=c.get('/api/databases/'+products).json()['records'];assert next(row for row in product_rows if row['note_id']==apple)['properties'][inverse]==[]


def test_relations_cannot_read_foreign_private_records_or_cross_spaces():
    c,_,_=owner();g,_=member(c);mine=database(c);foreign=database(g)
    assert configure(c,mine,[{'id':'link','name':'Private leak','type':'relation','database_id':foreign}]).status_code==404
    family=c.post('/api/social/families',json={'name':'Family'}).json()['id'];shared=database(c,'Shared',family)
    assert configure(c,shared,[{'id':'link','name':'Link','type':'relation','database_id':mine}]).status_code==422


def test_dependencies_detect_cycles_and_shift_successor_dates():
    c,_,_=owner();nid=database(c)
    props=[{'id':'dates','name':'Срок','type':'date'},{'id':'before','name':'Зависит от','type':'dependency'}]
    views=[{'id':'timeline','name':'Шкала','type':'timeline','date_by':'dates','dependency_by':'before','auto_shift':True,'lag_days':0}]
    assert configure(c,nid,props,views).status_code==200
    first=record(c,nid,'First',{'dates':{'start':'2026-10-12','end':'2026-10-14'}})
    second=record(c,nid,'Second',{'dates':{'start':'2026-10-12','end':'2026-10-13'},'before':[first]})
    rows=c.get('/api/databases/'+nid).json()['records'];second_row=next(x for x in rows if x['note_id']==second)
    assert second_row['properties']['dates']=={'start':'2026-10-15','end':'2026-10-16'}
    current=next(x for x in rows if x['note_id']==first)
    cycle=c.patch('/api/database-records/'+first,json={'properties':{'dates':current['properties']['dates'],'before':[second]},'version':current['version']});assert cycle.status_code==422
    rows=c.get('/api/databases/'+nid).json()['records'];current=next(x for x in rows if x['note_id']==first)
    assert not current['properties'].get('before')
    moved=c.patch('/api/database-records/'+first,json={'properties':{'dates':{'start':'2026-10-15','end':'2026-10-17'}},'version':current['version']});assert moved.status_code==200,moved.text
    second_row=next(x for x in c.get('/api/databases/'+nid).json()['records'] if x['note_id']==second)
    assert second_row['properties']['dates']=={'start':'2026-10-18','end':'2026-10-19'}
