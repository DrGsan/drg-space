import io,json,zipfile
from tests.test_security import m,owner,member,client,reset
from tests.test_next_features import ok
from tests.test_widgets import image_bytes,enable
from tests.test_personal_data import export,unseal,headers
PEOPLE=[{'id':id,'name':name} for id,name in [('me','Вы'),('wife','Жена'),('vova','Вова'),('nastya','Настя')]]
GROUPS=[{'id':'pair_us','members':['me','wife']},{'id':'pair_them','members':['vova','nastya']}]
def bill(c):return ok(c.post('/api/expenses',json={'title':'Dinner','participants':PEOPLE,'groups':GROUPS}))
def add(c,b,**body):return ok(c.post('/api/expenses/'+b['id']+'/entries',json={'payer':'me','category':'restaurant','event_version':b['version'],**body}))
def test_group_total_personal_purchase_and_repayment_keep_actual_creditor():
 c,_,_=owner();b=bill(c)
 b=add(c,b,amount=500000,allocations={'pair_us':300000,'pair_them':200000})
 assert b['cost']['me']==0 and b['group_cost']=={'pair_us':300000,'pair_them':200000} and b['my_cost']==300000
 assert b['debts']==[{'from':'pair_them','to':'me','amount':200000}]
 b=add(c,b,amount=20000,allocations={'nastya':20000},category='bar')
 assert b['group_cost']['pair_them']==220000 and b['my_incoming']==220000
 b=add(c,b,amount=120000,payer='nastya',allocations={'pair_us':50000,'pair_them':70000})
 assert {'from':'pair_us','to':'nastya','amount':50000} in b['debts']
 b=add(c,b,kind='repayment',amount=220000,payer='vova',allocations={'me':220000},repaid_for='pair_them')
 assert b['debts']==[{'from':'pair_us','to':'nastya','amount':50000}] and b['my_balance']==-50000
 b=add(c,b,kind='repayment',amount=50000,payer='wife',allocations={'nastya':50000},repaid_for='pair_us')
 assert b['debts']==[] and b['my_balance']==0 and b['my_cost']==350000

def test_group_validation_preservation_and_used_group_cannot_be_reassigned():
 c,_,_=owner();b=bill(c)
 assert c.post('/api/expenses/'+b['id']+'/entries',json={'payer':'me','amount':100,'allocations':{'pair_them':50,'nastya':50},'event_version':b['version']}).status_code==422
 b=add(c,b,amount=100,allocations={'pair_them':100})
 body={'title':b['title'],'participants':b['participants'],'version':b['version']}
 b=ok(c.put('/api/expenses/'+b['id'],json=body));assert b['groups']==GROUPS
 assert c.put('/api/expenses/'+b['id'],json={**body,'version':b['version'],'groups':[]}).status_code==409
 assert c.post('/api/expenses',json={'title':'Bad','participants':PEOPLE,'groups':[GROUPS[0],{'id':'pair_bad','members':['wife','nastya']}]}).status_code==422
 assert c.post('/api/expenses/'+b['id']+'/entries',json={'kind':'repayment','payer':'me','amount':100,'allocations':{'nastya':100},'repaid_for':'pair_them','event_version':b['version']}).status_code==422

def test_fifty_encrypted_photos_zip_shares_and_archive_roundtrip():
 c,_,_=owner();g,_=member(c);enable(g,'expenses');b=bill(c);body=image_bytes()
 for i in range(50):b=ok(c.post('/api/expenses/'+b['id']+'/photos',content=body))
 assert c.post('/api/expenses/'+b['id']+'/photos',content=body).status_code==422
 assert g.get('/api/expenses/'+b['id']+'/photos.zip').status_code==404
 share=ok(c.post('/api/expenses/'+b['id']+'/shares',json={'duration':2,'unit':'days'}));anonymous=client();path='/api/shared-expenses/'+share['token']
 response=anonymous.get(path+'/photos.zip');assert response.status_code==200
 with zipfile.ZipFile(io.BytesIO(response.content)) as z:
  assert len(z.namelist())==50 and all(z.read(name)==body for name in z.namelist())
 assert anonymous.get(path+'/photos/'+b['photos'][0]+'?download=true').content==body
 preview=anonymous.get(path+'/photos/'+b['photos'][0]+'?preview=true');assert preview.status_code==200 and preview.headers['content-type'].startswith('image/webp')
 assert g.get('/api/expenses/'+b['id']+'/photos/'+b['photos'][0]+'?preview=true').status_code==404
 archive=export(c);manifest,_=unseal(archive);assert len(manifest['data']['expenses'][0]['photos'])==50
 ok(g.post('/api/account/data/import',content=archive,headers=headers(password='Friend-password-123')))
 copied=ok(g.get('/api/expenses'))[0];assert copied['groups']==GROUPS and len(copied['photos'])==50
 ok(c.delete('/api/expense-shares/'+share['token']));assert anonymous.get(path+'/photos.zip').status_code==404
