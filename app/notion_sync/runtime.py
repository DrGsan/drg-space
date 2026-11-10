"""One bounded server worker; no timer or SQL loop when integration is disabled."""
import asyncio
import logging
import traceback
from app.process_locks import try_file_lock
from app.storage import blocking
from app.notion_sync.events import reconcile,route_unknown

logger=logging.getLogger('drg.notion-sync')


async def run(service,gate):
    if not service.config.configured:return
    lease=None;cleanup=0;icons_due=0;icons_after='';render_due=0;render_after=''
    try:
        while True:
            if lease is None:lease=try_file_lock(service.data_dir,'notion-runner.lock')
            busy=False
            if lease:
                operation='storage'
                try:
                    async with gate.shared():
                        operation='queue'
                        busy=await blocking(service.work_once)
                        operation='event-routing'
                        busy=await blocking(route_unknown,service) or busy
                        operation='reconcile'
                        busy=await blocking(reconcile,service) or busy
                        if service.clock()>=render_due:
                            from app.notion_sync.rendering import recover
                            operation='render-recovery'
                            render_due=service.clock()+300
                            cursor=await blocking(recover,service,render_after)
                            render_after=cursor or ''
                            if cursor is None:render_due=service.clock()+3600
                            else:render_due=0;busy=True
                        if service.clock()>=icons_due:
                            operation='icon-recovery'
                            icons_due=service.clock()+300
                            cursor=await blocking(service.recover_icons,icons_after)
                            icons_after=cursor or ''
                            if cursor is None:icons_due=service.clock()+3600
                            else:icons_due=0;busy=True
                        if service.clock()>=cleanup:
                            # Maintenance failure must not repeat at the idle
                            # polling rate or prevent other queue work.
                            cleanup=service.clock()+300
                            operation='maintenance'
                            await blocking(retention,service)
                            cleanup=service.clock()+3600
                except Exception as error:
                    # Provider data, URLs and tokens must never appear in traces.
                    frame=traceback.extract_tb(error.__traceback__)[-1]
                    logger.error('Notion sync operation failed: %s (%s in %s:%s); durable work retained for retry.',operation,type(error).__name__,frame.name,frame.lineno)
            await asyncio.sleep(.25 if busy else 5)
    finally:
        if lease:lease.close()


def retention(service):
    stamp=int(service.clock())
    with service.db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM notion_events WHERE created<?',(stamp-7*86400,))
        c.execute('DELETE FROM notion_oauth_states WHERE expires<=?',(stamp,))
        from app.notion_sync.pending_assets import PREFIX
        c.execute("DELETE FROM app_settings WHERE key LIKE ? AND key NOT IN (SELECT ?||p.note_id FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id WHERE r.status!='detached')",(PREFIX+'%',PREFIX))
        import json
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',('notion-webhook-verification:%',)).fetchall():
            if stamp-json.loads(row['value'])['created']>86400:c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))
        # A preview has not created user notes. Abandoned previews may expire;
        # confirmed links and copied content are never deleted by this cleanup.
        c.execute("DELETE FROM notion_roots WHERE note_id IS NULL AND updated<?",(stamp-86400,))
