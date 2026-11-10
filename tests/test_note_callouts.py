"""Typed alerts and Mermaid survive canonical shared storage and Markdown projection."""
from tests.test_security import owner
from app import main as m
from app.collaboration import load_doc,project
from app.share_editing import replace_content

def test_typed_alerts_and_diagram_roundtrip():
    doc=load_doc(None)
    blocks=[{'id':'alert','type':'callout','props':{'variant':'warning'},'content':[{'type':'text','text':'Warning','styles':{'bold':True}}],'children':[{'id':'link','type':'paragraph','content':[{'type':'link','href':'https://example.com','content':[{'type':'text','text':'Details'}]}]}]},
            {'id':'diagram','type':'codeBlock','props':{'language':'mermaid'},'content':'flowchart LR\n A-->B'}]
    title,body,saved=replace_content(doc,'Title',blocks,m.fail)
    assert ':::warning\n**Warning**' in body and '[Details](https://example.com)' in body
    assert '```mermaid\nflowchart LR\n A-->B\n```' in body
    assert saved[0]['props']['variant']=='warning' and saved[0]['children'][0]['id']=='link'
    assert project(load_doc(doc.get_update()),m.fail)==(title,body,saved)
