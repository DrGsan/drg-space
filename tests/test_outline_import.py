import io,json
from zipfile import ZipFile
from PIL import Image
from tests.test_security import owner,client,reset
from app import main as m

def zip_body(files):
 out=io.BytesIO()
 with ZipFile(out,'w') as z:
  for name,body in files.items():z.writestr(name,body)
 return out.getvalue()

def png():
 out=io.BytesIO();Image.new('RGB',(10,5),'blue').save(out,'PNG');return out.getvalue()

def test_outline_multiple_exports_keep_tree_embedded_binaries_and_existing_pages():
 c,_,_=owner();existing=c.post('/api/notes',json={'title':'Existing','body':'Keep me'}).json()
 identity='9c95ddaa-fca8-4618-89c0-22c9e4dd3f9f'
 parts=[zip_body({'Collection/Root.md':'# Root\n\n[Child](Root/Child.md)\n\n![Picture](uploads/photo.png)','Collection/Root/Child.md':'# Child\n\nText','Collection/uploads/photo.png':png()}),zip_body({'Single.md':'# Single\n\n![Binary](/api/attachments.redirect?id='+identity+')','attachments/'+identity+'.bin':png()}),zip_body({'Third.md':'# Third\n\n[File](attachments/readme.txt)','attachments/readme.txt':'PRIVATE_ATTACHMENT'})]
 body=b''.join(parts);headers={'X-Outline-Parts':json.dumps([len(part) for part in parts])}
 result=c.post('/api/account/outline/preview',content=body,headers=headers);assert result.status_code==200,result.text
 summary=result.json();assert summary['notes']==4 and summary['files']==3 and summary['warnings']==[]
 assert c.post('/api/account/outline/import',content=body,headers=headers).status_code==422
 bad=c.post('/api/account/outline/import',content=body,headers={**headers,'X-Import-Confirmation':'IMPORT','X-Outline-Preview':'wrong'});assert bad.status_code==409
 imported=c.post('/api/account/outline/import',content=body,headers={**headers,'X-Import-Confirmation':'IMPORT','X-Outline-Preview':summary['sha256']});assert imported.status_code==200,imported.text
 notes=c.get('/api/notes').json();by_title={note['title']:note for note in notes};assert by_title['Child']['parent_id']==by_title['Root']['id']
 assert c.get('/api/notes/'+existing['id']).json()['body']=='Keep me'
 assert 'attachments.redirect' not in by_title['Single']['body']
 assert '/api/notes/'+by_title['Root']['id']+'/files/' in by_title['Root']['body']
 with m.db() as d:
  files=d.execute("SELECT * FROM files WHERE kind='file' ORDER BY name").fetchall()
  binary=dict(next(row for row in files if row['name'].endswith('.bin')));assert binary['mime']=='image/png'
 response=c.get('/api/notes/'+binary['parent_id']+'/files/'+binary['id']);assert response.status_code==200 and response.content==png()
 assert client().get('/api/notes/'+binary['parent_id']+'/files/'+binary['id']).status_code==401


def test_outline_rejects_unsafe_paths_and_non_markdown_without_creating_data():
 c,_,_=owner()
 for files in ({'../escape.md':'# Bad'},{'index.html':'<script>bad</script>'}):
  response=c.post('/api/account/outline/preview',content=zip_body(files));assert response.status_code==422,response.text
 assert c.get('/api/notes').json()==[]
