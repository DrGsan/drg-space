"""Read view settings without granting writes or traversing linked databases."""
from urllib.parse import unquote
from app.notion_sync.client import NotionError,page_id

SUPPORTED={'table','list','board','calendar','gallery','timeline'}


def read(client,database,source):
    try:refs=client.all('/v1/views',data={'database_id':database},maximum=2000)
    except NotionError as error:
        if error.status not in (403,404):raise
        return [],[{'id':source,'type':'view','reason':'notionViewSettingsUnsupported'}]
    views=[];issues=[]
    if len(refs)>20:issues.append({'id':source,'type':'view','reason':'notionViewLimit'})
    for ref in refs[:20]:
        try:identity=page_id(ref.get('id') or (ref.get('view') or {}).get('id',''))
        except (ValueError,AttributeError):raise NotionError('notionInvalidResponse') from None
        try:view=client.request('GET','/v1/views/'+identity)
        except NotionError as error:
            if error.status!=404:raise
            issues.append({'id':identity,'type':'view','reason':'notionViewSettingsUnsupported'});continue
        if view.get('data_source_id')!=source:continue
        parent=(view.get('parent') or {}).get('database_id')
        if parent and page_id(parent)!=page_id(database):continue
        views.append(view)
    return views,issues


def semantic(views):
    return [{key:view.get(key) for key in ('id','data_source_id','name','type','filter','sorts','configuration')} for view in views]


def convert(views,properties):
    mapping={};kinds={};output=[];issues=[]
    for name,prop in properties.items():
        key='title' if prop['type']=='title' else 'n_'+prop['id'].encode().hex()[:35]
        for alias in (name,prop['id'],unquote(prop['id'])):mapping[alias]=key
        kinds[key]=prop['type']
    def field(value):return mapping.get(value) if isinstance(value,str) else None
    for raw in views:
        warned=False
        def warn():
            nonlocal warned
            if not warned:issues.append({'id':raw['id'],'type':'view','reason':'notionViewSettingsUnsupported'});warned=True
        kind=raw.get('type')
        if kind not in SUPPORTED:warn();continue
        view={'id':'nv_'+page_id(raw['id']).replace('-',''),'name':str(raw.get('name') or kind)[:80],'type':kind,'visible':[]}
        query=raw.get('filter')
        while isinstance(query,dict) and set(query) in ({'and'},{'or'}) and isinstance(next(iter(query.values())),list) and len(next(iter(query.values())))==1:query=next(iter(query.values()))[0]
        if query:
            prop=field(query.get('property'))
            condition=next((value for key,value in query.items() if key not in ('property','type') and isinstance(value,dict)),{})
            operation=next(iter(condition),None);value=condition.get(operation)
            operators={'contains':'contains','does_not_contain':'not_contains','equals':'equals','does_not_equal':'not_equals','is_empty':'empty','is_not_empty':'not_empty'}
            empty=operation in ('is_empty','is_not_empty')
            if prop and len(condition)==1 and operation in operators and (empty or isinstance(value,(str,int,float)) and not isinstance(value,bool) and len(str(value))<=200):
                view['filter']={'property':prop,'operator':operators[operation],'value':'' if empty else str(value)}
            else:warn()
        sorts=raw.get('sorts') or []
        if sorts:
            prop=field(sorts[0].get('property'));direction={'ascending':'asc','descending':'desc'}.get(sorts[0].get('direction'))
            if len(sorts)==1 and prop and direction:view['sort']={'property':prop,'direction':direction}
            else:warn()
        config=raw.get('configuration') or {};settings=config.get(kind,config)
        for item in settings.get('properties',[]):
            prop=field(item.get('property_id'))
            if item.get('visible') and prop and prop!='title':view['visible'].append(prop)
        if settings.get('properties') and not view['visible'] and any(k!='title' for k in kinds):warn()
        for setting,target,allowed in [('group_by','group_by',{'select','status'}),('date_property','date_by',{'date'})]:
            value=settings.get(setting)
            if isinstance(value,dict):value=value.get('property_id') or value.get('property')
            if value:
                prop=field(value)
                if prop and kinds.get(prop) in allowed:view[target]=prop
                else:warn()
        if kind=='board' and not view.get('group_by') or kind in ('calendar','timeline') and not view.get('date_by'):
            warn();view['type']='table'
        # The original settings stay in PageSnapshot, including unsupported
        # parts. This is a declared projection, never a fabricated match.
        output.append(view)
    return output or [{'id':'table','name':'Notion','type':'table','visible':[]}],issues
