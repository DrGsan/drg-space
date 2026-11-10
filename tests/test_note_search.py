from tests.test_security import owner,member,client,reset

def create(c,title,body='',**kw):
    r=c.post('/api/notes',json={'title':title,'body':body,**kw});assert r.status_code==200,r.text;return r.json()['id']
def find(c,query):
    r=c.get('/api/notes/search',params={'q':query});assert r.status_code==200,r.text;return r.json()

def test_search_title_text_nested_pages_and_unicode_without_other_users_or_trash():
    c,_,_=owner();g,_=member(c)
    title=create(c,'Путешествие в Москву','Билеты и гостиница')
    parent=create(c,'Личная папка');child=create(c,'Бюджет поездки','Москва: ещё нужен обратный билет',parent_id=parent)
    create(g,'Секретная Москва','Москва');trash=create(c,'Москва в корзине');c.delete('/api/notes/'+trash)
    result=find(c,'МОСКВ');assert result['total']==2 and result['items'][0]['id']==title
    assert {r['id'] for r in result['items']}=={title,child} and any('обратный билет' in r['excerpt'] for r in result['items'])
    assert find(c,'еще')['items'][0]['id']==child
    assert find(c,'  БИЛЕТ    москва ')['items'][0]['id']==child
    assert find(c,'несуществующее слово')['total']==0 and find(c,'  ')['items']==[]
    assert client().get('/api/notes/search?q=Москва').status_code==401
    assert c.get('/api/notes/search',params={'q':'a'*201}).status_code==422

def test_search_finds_block_and_table_text_with_literal_special_characters():
    c,_,_=owner()
    rich=create(c,'Документ без Markdown',blocks=[{'type':'paragraph','content':[{'type':'text','text':'Необычная фраза 100% _secret_ <script>','styles':{}}]}])
    table=create(c,'Таблица',blocks=[{'type':'table','content':{'type':'tableContent','rows':[{'cells':[[{'type':'text','text':'Проект Северный ветер','styles':{}}]]}]}}])
    assert find(c,'НЕОБЫЧНАЯ')['items'][0]['id']==rich
    assert find(c,'100% _secret_')['items'][0]['id']==rich
    assert find(c,'Северный ветер')['items'][0]['id']==table
    assert find(c,"' OR 1=1 --")['items']==[]
    assert find(c,'<script>')['items'][0]['excerpt'].find('<script>')>=0
    assert c.put('/api/modules',json={'enabled':{'notes':False}}).status_code==200
    assert c.get('/api/notes/search?q=ветер').status_code==403
