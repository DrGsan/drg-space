"""Measured queue work on encrypted storage; the provider is never load tested."""
import json
import time
from pathlib import Path
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,imported,ROOT,paragraph
from app import main as m
from app.database import Connection
from app.notion_sync.state import dump,enqueue


def test_500_accounts_make_two_provider_reads_per_changed_page_and_no_full_tree_poll(monkeypatch):
    c,s,provider,service,stamp=initialized();provider.blocks[ROOT]=[]
    first,mapping=imported(service,s)
    with m.db() as db:
        source=dict(db.execute('SELECT * FROM notion_pages WHERE root_id=?',(first,)).fetchone())
        note=dict(db.execute('SELECT * FROM notes WHERE id=?',(mapping[ROOT],)).fetchone())
        db.execute('BEGIN IMMEDIATE')
        roots=[first]
        for i in range(1,500):
            uid='load-user-'+str(i);cid='load-connection-'+str(i);rid='load-root-'+str(i);nid='load-note-'+str(i)
            db.execute("INSERT INTO users(id,login,password,role,created) VALUES(?,?,?,'owner',?)",(uid,uid,'test-only-unusable-password',int(stamp[0])))
            db.execute("INSERT INTO notion_connections(id,user_id,kind,workspace_id,bot_id,payload,created,updated) VALUES(?,?,'read',?,'fixture',?,?,?)",(cid,uid,'load-workspace-'+str(i),dump({'access_token':'mock-token'}),int(stamp[0]),int(stamp[0])))
            db.execute('INSERT INTO notes(id,user_id,title,body,blocks,icon,cover,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',(nid,uid,note['title'],note['body'],note['blocks'],note['icon'],note['cover'],int(stamp[0]),int(stamp[0])))
            db.execute("INSERT INTO notion_roots(id,user_id,connection_id,notion_id,note_id,status,payload,created,updated) VALUES(?,?,?,?,?,'active','{}',?,?)",(rid,uid,cid,ROOT,nid,int(stamp[0]),int(stamp[0])))
            db.execute("INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,status,payload,updated) VALUES(?,?,?,?,?,'active',?,?)",('load-page-'+str(i),uid,rid,ROOT,nid,source['payload'],int(stamp[0])))
            roots.append(rid)
        for root in roots:enqueue(db,root,ROOT,'pull',int(stamp[0]))
    provider.blocks[ROOT]=[paragraph('Changed on Notion')]
    before=len(provider.requests);queries=[0];execute=Connection._execute
    def counted(connection,sql,parameters=()):queries[0]+=1;return execute(connection,sql,parameters)
    monkeypatch.setattr(Connection,'_execute',counted)
    started=time.perf_counter()
    for _ in roots:assert service.work_once()
    assert not service.work_once()
    elapsed=time.perf_counter()-started
    assert len(provider.requests)-before==1000
    assert not provider.downloads
    assert queries[0] <= 500*60+100, 'Changed simple pages exceeded the SQL request budget'
    repair_start=queries[0];http_start=len(provider.requests);cursor='';batches=0
    while cursor is not None:
        cursor=service.recover_icons(cursor);batches+=1
    repair_queries=queries[0]-repair_start
    assert batches==6 and repair_queries==12
    assert len(provider.requests)==http_start, 'Local icon recovery must not poll Notion per page'
    from app.notion_sync.rendering import recover
    render_start=queries[0];cursor='';render_batches=0
    while cursor is not None:
        cursor=recover(service,cursor);render_batches+=1
    render_queries=queries[0]-render_start
    assert render_batches==6 and render_queries==6
    assert len(provider.requests)==http_start, 'Rendering recovery must not poll Notion per page'
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM notion_pages').fetchone()[0]==500
    report={'accounts':500,'changed_pages':500,'provider_requests':len(provider.requests)-before,'provider_requests_per_changed_page':2,'sql_statements':queries[0]-repair_queries-render_queries,'icon_recovery_batches':batches,'icon_recovery_sql':repair_queries,'icon_recovery_http':0,'render_recovery_batches':render_batches,'render_recovery_sql':render_queries,'render_recovery_http':0,'elapsed_seconds':round(elapsed,3),'provider':'httpx.MockTransport','storage':'PostgreSQL' if m.database.kind=='postgres' else 'SQLCipher' if m.database.cipher.enabled else 'plaintext','background_or_production_traffic':False}
    target=Path(__file__).resolve().parents[1]/'artifacts/drg-62'/('load-500-postgres.json' if m.database.kind=='postgres' else 'load-500.json');target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n')
