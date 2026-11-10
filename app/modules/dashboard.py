"""Per-account instance configuration, with optimistic conflict protection."""
from app.i18n import message as localized_message
import json
from app.http import Router, Request
from app.modules.widget_models import DashboardLayout, dashboard, validate_layout
from app.modules.preferences import section_preferences

def create_dashboard_router(db,ready,fail):
    router=Router()
    @router.get('/api/dashboard/layout')
    def get_layout(request:Request):
        s=ready(request)
        return dashboard(json.loads(s['settings']))

    @router.put('/api/dashboard/layout')
    def save_layout(data:DashboardLayout,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            st=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            if dashboard(st)['version'] != data.version: fail(409,localized_message('theHomepageSettingsHaveBeenChangedInA'))
            validate_layout(data,c,s['user_id'],fail)
            data.version+=1
            st['dashboard']=data.model_dump()
            if 'modules' in st:st['modules']=section_preferences(st)
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(st),s['user_id']))
        return data.model_dump()
    return router
