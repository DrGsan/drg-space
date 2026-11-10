"""Validate obsolete 3.0.x metadata when reading full backups.

Retention ignores these records: every trashed page keeps its original 30 days.
"""
import json
PREFIX='note-deletion:'
PERIOD=86400

def validate_archive(c,fail):
    pages={r['id']:r['deleted'] for r in c.execute('SELECT id,deleted FROM notes')}
    try:
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)):
            value=json.loads(row['value']);nid=row['key'][len(PREFIX):]
            if (set(value)!={'version','created','delete_at'} or type(value['version']) is not int or value['version']!=1
                or type(value['created']) is not int or value['created']<0 or pages.get(nid)!=value['created']
                or type(value['delete_at']) is not int or value['delete_at']-value['created']!=PERIOD):raise ValueError()
    except (ValueError,TypeError,KeyError):fail()
