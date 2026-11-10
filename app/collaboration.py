"""Durable Yjs/Yrs documents. Rich blocks are derived from the merged CRDT,
never trusted as a separate client snapshot. The SQLite transaction is the
serialization boundary, so reconnects, restarts and backups preserve edits.
"""
from app.i18n import message as localized_message
from pycrdt import Doc, XmlFragment, XmlElement, XmlText, Map
from app.modules.notes import validate_blocks, ALLOWED_BLOCKS

MAX_STATE=1500000

def load_doc(state):
    doc=Doc({'prosemirror':XmlFragment(),'meta':Map()})
    if state:doc.apply_update(state)
    return doc

def project(doc,fail):
    count=0
    def inline(node,depth=0):
        if depth>35:fail(422,localized_message('tooMuchTextNesting'))
        out=[]
        for child in node.children:
            if isinstance(child,XmlText):
                for text,marks in child.diff():
                    if not isinstance(text,str):fail(422,localized_message('invalidTextContent'))
                    marks=marks or {};styles={}
                    for key in ('bold','italic','underline','strike','code','spoiler'):
                        if key in marks:styles[key]=True
                    for key in ('textColor','backgroundColor'):
                        if key in marks:
                            value=marks[key]
                            styles[key]=value.get('stringValue',value.get(key,'default')) if isinstance(value,dict) else value
                    part={'type':'text','text':text,'styles':styles}
                    if 'link' in marks:
                        link=marks['link']
                        if not isinstance(link,dict):fail(422,localized_message('invalidLink'))
                        part={'type':'link','href':link.get('href',''),'content':[part]}
                    out.append(part)
            elif isinstance(child,XmlElement) and child.tag=='hardBreak':out.append({'type':'text','text':'\n','styles':{}})
            elif isinstance(child,XmlElement) and child.tag=='equation':out.append({'type':'equation','props':{'latex':dict(child.attributes).get('latex','')}})
            else:fail(422,localized_message('unknownEmbeddedTextElement'))
        return out
    def table(node):
        rows=[];widths=[];headers=[]
        for row in node.children:
            if not isinstance(row,XmlElement) or row.tag!='tableRow':fail(422,localized_message('invalidTable'))
            cells=[];header=[]
            for cell in row.children:
                if not isinstance(cell,XmlElement) or cell.tag not in ('tableCell','tableHeader'):fail(422,localized_message('invalidCell'))
                attrs=dict(cell.attributes);content=[]
                for para in cell.children:
                    if not isinstance(para,XmlElement) or para.tag!='tableParagraph':fail(422,localized_message('invalidTableText'))
                    if content:content.append({'type':'text','text':'\n','styles':{}})
                    content+=inline(para)
                cells.append({'type':'tableCell','content':content,'props':{k:v for k,v in attrs.items() if k in ('colspan','rowspan','textColor','backgroundColor','textAlignment')}})
                header.append(cell.tag=='tableHeader')
                if not rows:widths.extend(attrs.get('colwidth') or [None]*int(attrs.get('colspan',1)))
            rows.append({'cells':cells});headers.append(header)
        return {'type':'tableContent','rows':rows,'columnWidths':widths,'headerRows':sum(bool(h) and all(h) for h in headers),'headerCols':sum(all(i<len(h) and h[i] for h in headers) for i in range(len(headers[0]) if headers else 0))}
    def group(node,depth=0):
        nonlocal count
        if depth>30:fail(422,localized_message('tooMuchBlockNesting'))
        if not isinstance(node,XmlElement) or node.tag!='blockGroup':fail(422,localized_message('invalidPageStructure'))
        out=[]
        for container in node.children:
            count+=1
            if count>2000 or not isinstance(container,XmlElement) or container.tag!='blockContainer':fail(422,localized_message('invalidBlock'))
            children=list(container.children)
            if len(children) not in (1,2) or not isinstance(children[0],XmlElement):fail(422,localized_message('invalidBlockStructure'))
            content=children[0];kind=content.tag
            if kind not in ALLOWED_BLOCKS:fail(422,localized_message('unknownBlockType'))
            props=dict(content.attributes)
            # In current BlockNote versions color attributes live on the container.
            props.update({k:v for k,v in dict(container.attributes).items() if k in ('textColor','backgroundColor')})
            block={'id':dict(container.attributes).get('id'),'type':kind,'props':props,'children':group(children[1],depth+1) if len(children)==2 else []}
            if kind=='table':block['content']=table(content)
            elif kind=='codeBlock':
                parts=inline(content)
                if any(part.get('type')!='text' for part in parts):fail(422,localized_message('onlyTextIsAllowedInTheCodeBlock'))
                block['content']=''.join(part['text'] for part in parts)
            elif kind not in ('image','video','audio','file','divider','pageLink'):block['content']=inline(content)
            out.append(block)
        return out
    roots=list(doc.get('prosemirror',type=XmlFragment).children)
    if len(roots)!=1:fail(422,localized_message('thePageShouldContainOnlyOneBlockGroup'))
    blocks=group(roots[0]);validate_blocks(blocks,fail)
    title=doc.get('meta',type=Map).get('title','Без названия')
    if not isinstance(title,str) or len(title)>200:fail(422,localized_message('titleUpTo200Characters'))
    title=title.strip() or 'Без названия'
    def text(items):
        if isinstance(items,str):return items
        if not isinstance(items,list):return ''
        result=''
        for part in items:
            if part.get('type')=='equation':result+='$'+part.get('props',{}).get('latex','')+'$';continue
            if part.get('type')=='link':result+='['+text(part.get('content',[]))+']('+part.get('href','')+')';continue
            t=part.get('text','');styles=part.get('styles',{})
            for key,marker in [('code','`'),('bold','**'),('italic','*'),('strike','~~'),('spoiler','||')]:
                if styles.get(key):t=marker+t+marker
            result+=t
        return result
    def markdown(items,level=0):
        parts=[]
        for b in items:
            kind=b['type'];p=b['props'];t=text(b.get('content',[]));prefix='  '*level
            if kind=='heading':t='#'*int(p.get('level',1))+' '+t
            elif kind in ('bulletListItem','toggleListItem'):t='- '+t
            elif kind=='numberedListItem':t='1. '+t
            elif kind=='checkListItem':t='- ['+('x' if p.get('checked') else ' ')+'] '+t
            elif kind=='callout' and p.get('variant') in ('info','success','warning','tip'):
                content=t+('\n\n'+markdown(b['children'],0) if b['children'] else '')
                parts.append(prefix+':::'+p['variant']+'\n'+content+'\n:::')
                continue
            elif kind in ('quote','callout'):t='> '+t
            elif kind=='codeBlock':t='```'+str(p.get('language',''))+'\n'+text(b.get('content',[]))+'\n```'
            elif kind=='divider':t='---'
            elif kind in ('image','video','audio','file'):t=('!' if kind=='image' else '')+'['+str(p.get('caption') or p.get('name') or kind)+']('+str(p.get('url',''))+')'
            elif kind=='pageLink':t=p.get('title','Подстраница')
            elif kind=='table':t='\n'.join('| '+' | '.join(text(cell.get('content',[])) if isinstance(cell,dict) else text(cell) for cell in row['cells'])+' |' for row in b['content']['rows'])
            parts.append(prefix+t)
            if b['children']:parts.append(markdown(b['children'],level+1))
        return '\n\n'.join(parts)
    body=markdown(blocks)
    if len(body)>200000:fail(422,localized_message('pageIsTooLarge'))
    return title,body,blocks

def links_ok(c,blocks,scope,fail,previous=None):
    known=set()
    def collect(items):
        for b in items or []:
            if b.get('type')=='pageLink':known.add(b.get('props',{}).get('pageId'))
            collect(b.get('children',[]))
    collect(previous)
    def walk(items):
        for b in items:
            if b['type']=='pageLink':
                pid=b['props'].get('pageId','')
                target=c.execute('SELECT space_id FROM notes WHERE id=?',(pid,)).fetchone()
                if (target and target['space_id']!=scope) or (not target and pid not in known):fail(422,localized_message('linkToSubpageMustPointToTheSame'))
            walk(b['children'])
    walk(blocks)
