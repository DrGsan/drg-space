"""Read selected subtrees with durable page checkpoints and bounded traversal."""
from dataclasses import dataclass,field
from app.notion_sync.client import NotionError,page_id
from app.notion_sync.blocks import text,file_url,needs_synced_refresh

@dataclass
class PageSnapshot:
    id:str
    parent:str|None
    title:str
    raw:dict
    blocks:list
    kind:str='page'
    properties:list=field(default_factory=list)
    values:dict=field(default_factory=dict)
    issues:list=field(default_factory=list)
    views:list=field(default_factory=list)

    def value(self):return self.__dict__


def title(page):
    if page.get('object') in ('database','data_source'):
        return text(page.get('title',[])).strip()[:200] or 'Notion'
    for prop in page.get('properties',{}).values():
        if prop.get('type')=='title':return text(prop.get('title',[])).strip()[:200] or 'Notion'
    return text(page.get('title',[])).strip()[:200] or 'Notion'


def schema_properties(data):
    fields=[];issues=[]
    mapping={'rich_text':'text','number':'number','select':'select','status':'select','multi_select':'multi_select','checkbox':'checkbox','date':'date','url':'url','email':'text','phone_number':'text'}
    for name,prop in data.items():
        kind=prop['type']
        if kind=='title':continue
        identity='n_'+prop['id'].encode().hex()[:35]
        field={'id':identity,'name':name[:80],'type':mapping.get(kind,'text')}
        if kind in ('select','status','multi_select'):
            options=prop.get(kind,{}).get('options',[])
            if len(options)>50:raise NotionError('notionPageTooLarge',422)
            field['options']=[x['name'] for x in options]
        if kind not in mapping:issues.append({'id':prop['id'],'type':kind,'reason':'notionComputedReadonly'})
        fields.append(field)
    if len(fields)>30:raise NotionError('notionPageTooLarge',422)
    return fields,issues


def property_value(prop):
    kind=prop.get('type');value=prop.get(kind)
    if kind in ('rich_text','title'):return text(value)
    if kind in ('number','checkbox','url','email','phone_number'):return value
    if kind in ('select','status'):return value.get('name') if value else None
    if kind=='multi_select':return [x['name'] for x in value or []]
    if kind=='date':return {'start':value['start'][:10],**({'end':value['end'][:10]} if value.get('end') else {})} if value else None
    if kind=='formula':return property_value(value or {})
    if kind in ('created_time','last_edited_time'):return value or ''
    if kind=='people':return ', '.join(x.get('name') or x.get('id','') for x in value or [])
    if kind=='relation':return ', '.join('https://www.notion.so/'+x['id'].replace('-','') for x in value or [])
    if kind=='files':return ', '.join(file_url(x) for x in value or [])
    if kind=='rollup':
        if value and value.get('type')=='array':return ', '.join(str(property_value(x)) for x in value.get('array',[]))
        return str((value or {}).get((value or {}).get('type'),''))
    return str(value or '')


def read_subtree(client,root_id,*,cached=None,checkpoint=None,maximum=2000,known_ids=None,parent_id=None,root_kind='page',root_properties=None,root_raw=None,load_cached=None,defer_ids=None,descend=True):
    """No HTTP/DB transaction spans the tree. Each completed page is reusable."""
    result={};cached=cached or {};checkpoint=checkpoint or (lambda page:None);total=0;defer_ids=defer_ids or set()
    def remember(current,previous=None):
        nonlocal total
        if len(result)>=maximum:raise NotionError('notionPageTooLarge',422)
        total+=len(str(current.value()).encode())
        if total>64*1024**2:raise NotionError('notionPageTooLarge',422)
        result[current.id]=current
        reusable=previous and previous.get('id')==current.id and current.raw is previous['raw'] and current.blocks is previous['blocks'] and current.parent==previous.get('parent') and current.title==previous.get('title') and current.properties==previous.get('properties',[]) and current.values==previous.get('values',{}) and current.views==previous.get('views',[]) and current.issues==previous.get('issues',[])
        if not reusable:checkpoint(current)
    def page(identity,parent=None,properties=None):
        identity=page_id(identity)
        if identity in defer_ids:return
        if identity in result:return
        if len(result)>=maximum:raise NotionError('notionPageTooLarge',422)
        previous=cached.get(identity)
        raw=root_raw if root_raw is not None and identity==page_id(root_id) else previous['raw'] if previous else client.page(identity)
        if raw.get('in_trash') or raw.get('archived'):raise NotionError('notionSourceUnavailable',404)
        blocks=previous['blocks'] if previous and not needs_synced_refresh(previous['blocks']) else client.tree(identity)
        current=PageSnapshot(identity,parent,title(raw),raw,blocks,properties=properties or [])
        if properties:
            for prop in raw.get('properties',{}).values():
                if prop.get('type')=='title':continue
                key='n_'+prop['id'].encode().hex()[:35];value=property_value(prop)
                target=next((x for x in properties if x['id']==key),None)
                if target:current.values[key]=value if target['type']!='text' or isinstance(value,str) else str(value if value is not None else '')
                if prop.get('has_more') or prop.get(prop.get('type'),{}).__class__ is dict and prop.get(prop.get('type'),{}).get('has_more'):
                    current.issues.append({'id':prop['id'],'type':prop.get('type'),'reason':'notionPropertyTruncated'})
        remember(current,previous)
        def children(items):
            for block in items:
                # Mirrored child-page links belong to the original's hierarchy,
                # not to this selected subtree. Never discover pages via a copy.
                if block.get('type')=='synced_block' and block.get('synced_block',{}).get('synced_from'):continue
                if block.get('type')=='child_page' and (known_ids is None or page_id(block['id']) not in known_ids):page(block['id'],identity)
                elif block.get('type')=='child_database' and (known_ids is None or page_id(block['id']) not in known_ids):database(block['id'],identity)
                children(block.get('children',[]))
        if descend:children(blocks)
    def database(identity,parent):
        identity=page_id(identity)
        if identity in defer_ids:return
        if identity in result:return
        previous=cached.get(identity) if known_ids is None else None
        raw=root_raw if root_raw is not None and identity==page_id(root_id) else previous['raw'] if previous else client.request('GET','/v1/databases/'+identity)
        if raw.get('in_trash') or raw.get('archived'):raise NotionError('notionSourceUnavailable',404)
        sources=raw.get('data_sources') or []
        # Multiple sources are kept as explicit child databases, never merged by
        # matching names or incompatible property schemas.
        links=[{'id':source['id'],'type':'child_database','child_database':{'title':source.get('name') or title(raw)}} for source in sources]
        current=PageSnapshot(identity,parent,title(raw),raw,previous['blocks'] if previous and previous['blocks']==links else links,kind='page')
        remember(current,previous)
        if descend:
            for source in sources:data_source(source['id'],identity)
    def data_source(identity,parent):
        identity=page_id(identity)
        if identity in defer_ids:return
        if identity in result:return
        previous_source=cached.get(identity) if known_ids is None else None
        data=root_raw if root_raw is not None and identity==page_id(root_id) else previous_source['raw'] if previous_source else client.request('GET','/v1/data_sources/'+identity)
        if data.get('in_trash') or data.get('archived'):raise NotionError('notionSourceUnavailable',404)
        fields,issues=schema_properties(data.get('properties',{}))
        old_schema=load_cached(identity) if load_cached else cached.get(identity)
        schema_changed=bool(old_schema and old_schema.get('properties')!=fields)
        from app.notion_sync.views import read as read_views,convert as convert_views
        container=(data.get('parent') or {}).get('database_id')
        views=previous_source.get('views',[]) if previous_source and 'views' in previous_source else []
        view_issues=[]
        if container and not (previous_source and 'views' in previous_source):
            views,view_issues=read_views(client,container,identity)
        _,conversion_issues=convert_views(views,data.get('properties',{}));view_issues.extend(conversion_issues)
        database_page=PageSnapshot(identity,parent,title(data),data,previous_source['blocks'] if previous_source else [],kind='database',properties=fields,issues=issues+view_issues+[{'id':identity,'type':'database','reason':'notionDatabaseReadonly'}],views=views)
        remember(database_page,previous_source)
        if not descend:return
        for item in client.all('/v1/data_sources/'+identity+'/query','POST',maximum=maximum):
            if item.get('object')=='page':
                if page_id(item['id']) in defer_ids:continue
                previous=cached.get(page_id(item['id']))
                if known_ids is None and previous and previous.get('id')==page_id(item['id']) and 'blocks' in previous:
                    # Initial preview fixes each completed snapshot, just as
                    # ordinary cached pages do. Formulas such as now() can
                    # change on every query without changing last_edited_time;
                    # replacing their raw values would yield the same row
                    # forever. Normal pulls below still read fresh values.
                    page(item['id'],identity,fields)
                    continue
                unchanged=bool(previous and previous['raw'].get('last_edited_time')==item.get('last_edited_time'))
                if known_ids is not None and page_id(item['id']) in known_ids and unchanged and not schema_changed:continue
                reusable=previous if previous and 'blocks' in previous else load_cached(item['id']) if unchanged and load_cached else None
                # Preserve the completed checkpoint identity/parent/values.
                # Dropping them makes a reused row look new, so every yielded
                # scan checkpoints the same first record and never advances.
                cached[page_id(item['id'])]={**(reusable or {}),'raw':item,'blocks':reusable['blocks'] if reusable and unchanged else client.tree(item['id'])}
                page(item['id'],identity,fields)
    if root_kind=='data_source':data_source(root_id,parent_id)
    elif root_kind=='database':database(root_id,parent_id)
    else:page(root_id,parent_id,root_properties)
    return result
