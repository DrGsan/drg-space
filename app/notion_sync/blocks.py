"""Loss-aware API block mapping, with stable IDs and no HTML round-trip.

Raw provider blocks remain in encrypted sync state. Unsupported constructs are
visible in the read copy and prevent reverse writes, rather than being erased.
"""
import copy
import re
from urllib.parse import urlsplit
from app.notion_sync.client import page_id,digest

BASIC={'paragraph':'paragraph','bulleted_list_item':'bulletListItem','numbered_list_item':'numberedListItem','to_do':'checkListItem','toggle':'toggleListItem','quote':'quote','code':'codeBlock','divider':'divider','callout':'callout','column_list':'columnList','column':'column','image':'image','video':'video','audio':'audio','file':'file','pdf':'file'}
REVERSE={value:key for key,value in BASIC.items() if key!='pdf'}
RENDER_REVISION=2

def needs_synced_refresh(blocks):
    return any((b.get('type')=='synced_block' and not (b.get('synced_resolved') or b.get('synced_unavailable'))) or needs_synced_refresh(b.get('children',[])) for b in blocks)
COLORS={'gray','brown','orange','yellow','green','blue','purple','pink','red','default'}
ANNOTATIONS={'bold':'bold','italic':'italic','underline':'underline','strikethrough':'strike','code':'code'}


def url(value):
    try:p=urlsplit(value)
    except (ValueError,TypeError):return ''
    return value if p.scheme in ('http','https','mailto') and not p.username and not p.password else ''


def text(items):return ''.join(item.get('plain_text',item.get('text',{}).get('content','')) for item in items or [])


def color(value):
    if value and value.endswith('_background'):return {'backgroundColor':value[:-11]}
    return {'textColor':value} if value and value!='default' else {}


def rich(items,resolve=None):
    out=[]
    for item in items or []:
        annotations=item.get('annotations') or {};styles={v:True for k,v in ANNOTATIONS.items() if annotations.get(k)}
        styles.update(color(annotations.get('color')))
        kind=item.get('type','text')
        if kind=='equation':out.append({'type':'equation','props':{'latex':item.get('equation',{}).get('expression','')}});continue
        value=item.get('text',{}).get('content',item.get('plain_text',''));link=item.get('text',{}).get('link') or {}
        href=item.get('href') or link.get('url','')
        if kind=='mention' and item.get('mention',{}).get('type')=='page':
            identity=item['mention']['page']['id'];href=(resolve(identity) if resolve else None) or 'https://www.notion.so/'+identity.replace('-','')
        if resolve and href:
            try:
                host=urlsplit(href).hostname or ''
                if any(host==domain or host.endswith('.'+domain) for domain in ('notion.so','notion.site')):href=resolve(page_id(href)) or href
            except (ValueError,TypeError):pass
        node={'type':'text','text':value,'styles':styles}
        out.append({'type':'link','href':url(href),'content':[node]} if url(href) else node)
    return out


def file_url(value):
    kind=(value or {}).get('type')
    return url((value or {}).get(kind,{}).get('url','')) if kind in ('file','external') else ''


def convert(blocks,resolve=None,*,source_id=None):
    issues=[];assets=[]
    def original(identity):
        return 'https://www.notion.so/'+(source_id or identity).replace('-','')+('#'+identity.replace('-','') if source_id else '')
    def walk(items,trail=''):
        out=[]
        for raw in items:
            kind=raw.get('type','unsupported');data=raw.get(kind) or {};identity=raw.get('id','')
            bid=re.sub('[^A-Za-z0-9_-]','',identity) or 'ns_'+digest(raw)[:24]
            if trail:bid=bid+'_'+digest(trail)[:8]
            props=color(data.get('color'));children=[] if kind=='synced_block' else walk(raw.get('children',[]),trail)
            result={'id':bid,'type':BASIC.get(kind,'paragraph'),'content':rich(data.get('rich_text',[]),resolve),'children':children}
            if kind.startswith('heading_'):
                level=int(kind[-1]);result['type']='heading';props['level']=min(3,level)
                if data.get('is_toggleable'):props['isToggleable']=True
                if level>3:issues.append({'id':identity,'type':kind,'reason':'notionHeadingLevel'})
            elif kind=='to_do':props['checked']=bool(data.get('checked'))
            elif kind=='code':props.update(language='text' if data.get('language')=='plain text' else data.get('language','text'),caption=text(data.get('caption')))
            elif kind=='callout':props['icon']=data.get('icon',{}).get('emoji','💡')
            elif kind=='column':
                width=data.get('width_ratio',1)
                props['width']=width if type(width) in (int,float) and 0<width<=100 else 1
            elif kind in ('image','video','audio','file','pdf'):
                source=file_url(data);props.update(url=source,name=data.get('name',''),caption=text(data.get('caption')))
                if source:assets.append({'block_id':identity,'url':source,'kind':result['type'],'name':props['name']})
                else:issues.append({'id':identity,'type':kind,'reason':'notionFileUnavailable'})
                if kind=='pdf':issues.append({'id':identity,'type':kind,'reason':'notionPdfAsFile'})
            elif kind=='table':
                result['type']='table';result['children']=[];rows=[]
                for row in raw.get('children',[]):rows.append({'cells':[rich(cell,resolve) for cell in row.get('table_row',{}).get('cells',[])]})
                result['content']={'type':'tableContent','rows':rows,'headerRows':1 if data.get('has_column_header') else 0,'headerCols':1 if data.get('has_row_header') else 0}
            elif kind in ('child_page','child_database'):
                target=resolve(identity) if resolve else None
                if target:
                    # resolve can return a canonical page URL. Page IDs are stored
                    # in props only when the complete mapping is already known.
                    result['type']='pageLink';props={'pageId':target.rsplit('/',1)[-1],'title':data.get('title','')};result['content']=[]
                else:result['content']=[{'type':'link','href':'https://www.notion.so/'+identity.replace('-',''),'content':[{'type':'text','text':data.get('title','Notion'),'styles':{}}]}]
            elif kind=='link_to_page':
                target_kind=data.get('type')
                try:reference=page_id(data.get(target_kind,'')) if target_kind in ('page_id','database_id') else None
                except (ValueError,TypeError):reference=None
                target=resolve(reference) if resolve and reference else None
                if target:
                    result['type']='pageLink';props={'pageId':target.rsplit('/',1)[-1],'title':'Notion'};result['content']=[]
                else:
                    href='https://www.notion.so/'+reference.replace('-','') if reference else original(identity)
                    result['content']=[{'type':'link','href':href,'content':[{'type':'text','text':href,'styles':{}}]}]
                    props['linkDisplay']='mention'
                    issues.append({'id':identity,'type':kind,'reason':'notionPageOutsideSync'})
            elif kind in ('bookmark','embed','link_preview'):
                href=url(data.get('url',''));result['content']=[{'type':'link','href':href,'content':[{'type':'text','text':text(data.get('caption')) or href or 'Notion','styles':{}}]}] if href else []
                props['linkDisplay']='bookmark' if kind=='bookmark' else 'url'
                if kind!='bookmark' or not href:issues.append({'id':identity,'type':kind,'reason':'notionExternalEmbed'})
            elif kind=='equation':result['content']=[{'type':'equation','props':{'latex':data.get('expression','')}}]
            elif kind=='synced_block':
                issues.append({'id':identity,'type':kind,'reason':'notionSyncedBlockReadonly'})
                # A synced block is a transparent container, not a quotation.
                # Scope IDs to its occurrence, since copies share source IDs.
                if not raw.get('synced_unavailable'):
                    out.extend(walk(raw.get('children',[]),trail+'/'+identity if trail else identity));continue
                result['content']=[{'type':'link','href':original(identity),'content':[{'type':'text','text':'Notion ↗','styles':{}}]}]
                issues.append({'id':identity,'type':kind,'reason':'notionSourceUnavailable'})
            elif kind not in BASIC and not kind.startswith('heading_') and kind!='table_row':
                result['content']=[{'type':'link','href':original(identity),'content':[{'type':'text','text':'Notion · '+str(data.get('block_type') or kind),'styles':{}}]}];issues.append({'id':identity,'type':kind,'reason':'notionUnsupportedBlock'})
            if props:result['props']=props
            out.append(result)
        return out
    result=walk(blocks)
    return result,issues,assets


def plain(blocks):
    def content(value):
        if isinstance(value,str):return value
        if isinstance(value,list):return ''.join(content(x) for x in value)
        if isinstance(value,dict):
            if value.get('type')=='equation':return value.get('props',{}).get('latex','')
            if 'text' in value:return value['text']
            if value.get('type')=='tableContent':return '\n'.join(' | '.join(content(c) for c in r.get('cells',[])) for r in value.get('rows',[]))
            return content(value.get('content',[]))
        return ''
    return '\n\n'.join(part for b in blocks for part in [content(b.get('content',[])),plain(b.get('children',[]))] if part)


def encode_rich(items):
    result=[]
    def walk(items,href=None):
        for item in items or []:
            kind=item.get('type')
            if kind=='link':walk(item.get('content',[]),url(item.get('href','')));continue
            if kind=='equation':result.append({'type':'equation','equation':{'expression':item['props']['latex']}});continue
            if kind!='text':raise ValueError('notionReverseUnsupported')
            styles=item.get('styles') or {}
            if any(k not in {*ANNOTATIONS.values(),'textColor','backgroundColor'} and v for k,v in styles.items()):raise ValueError('notionReverseUnsupported')
            ink=styles.get('backgroundColor') or styles.get('textColor') or 'default'
            if ink not in COLORS:raise ValueError('notionReverseUnsupported')
            if styles.get('backgroundColor'):ink+='_background'
            annotations={k:bool(styles.get(v)) for k,v in ANNOTATIONS.items()};annotations['color']=ink
            value=item.get('text','')
            for offset in range(0,len(value),2000):result.append({'type':'text','text':{'content':value[offset:offset+2000],**({'link':{'url':href}} if href else {})},'annotations':annotations})
    if isinstance(items,str):items=[{'type':'text','text':items}]
    walk(items)
    if len(result)>100:raise ValueError('notionReverseUnsupported')
    return result


def encode(blocks,asset_resolver=None):
    """Pure preflight. The entire document is checked before any provider write."""
    result=[]
    for b in blocks:
        kind=b['type'];props=b.get('props') or {};target=REVERSE.get(kind)
        if kind=='heading':target='heading_'+str(props.get('level',1))
        if target is None or kind in ('column','columnList','table','pageLink'):
            # These require structural operation plans, never a lossy text rewrite.
            raise ValueError('notionReverseUnsupported')
        if any(props.get(k) for k in ('textAlignment',) if props.get(k) not in ('left',None)):raise ValueError('notionReverseUnsupported')
        data={}
        if kind in ('image','video','audio','file'):
            source=props.get('url','')
            if asset_resolver:data=asset_resolver(source,kind)
            elif source.startswith('https://'):data={'type':'external','external':{'url':source}}
            else:raise ValueError('notionReverseUnsupported')
            data={**data,'caption':encode_rich(props.get('caption',''))}
        elif kind!='divider':
            data['rich_text']=encode_rich(b.get('content',[]))
            if kind=='heading':data['is_toggleable']=bool(props.get('isToggleable'))
            if kind=='checkListItem':data['checked']=bool(props.get('checked'))
            if kind=='codeBlock':data.update(language='plain text' if props.get('language','text')=='text' else props['language'],caption=encode_rich(props.get('caption','')))
            if kind=='callout':data['icon']={'type':'emoji','emoji':props.get('icon') or '💡'}
            ink=props.get('backgroundColor') or props.get('textColor')
            if ink and ink!='default':
                if ink not in COLORS:raise ValueError('notionReverseUnsupported')
                data['color']=ink+('_background' if props.get('backgroundColor') else '')
        item={'id':b.get('id',''),'type':target,target:data,'children':encode(b.get('children',[]),asset_resolver)}
        result.append(item)
    return result


def semantic(blocks):
    """Compare provider content without edit stamps/temporary file URLs."""
    result=[]
    for b in blocks:
        kind=b['type'];data=copy.deepcopy(b.get(kind) or {})
        for field in ('rich_text','caption'):
            if field in data:data[field]=[{k:v for k,v in part.items() if k not in ('plain_text','href')} for part in data[field]]
        for field in ('file','external'):
            if field in data:
                data[field]={k:v for k,v in data[field].items() if k!='expiry_time'}
                if field=='file' and data[field].get('url'):data[field]['url']=data[field]['url'].split('?',1)[0]
        result.append({'id':b.get('id'),'type':kind,'data':data,'children':semantic(b.get('children',[]))})
    return result
