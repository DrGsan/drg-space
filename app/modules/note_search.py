"""Unicode-aware search over owned live pages and their visible block text."""
import unicodedata,json

def normalize(value):return ' '.join(unicodedata.normalize('NFKC',value).casefold().replace('ё','е').split())
def visible_blocks(blocks):
    parts=[]
    def visit(value):
        if isinstance(value,list):
            for item in value:visit(item)
        elif isinstance(value,dict):
            for key,item in value.items():
                if key in ('text','content','children','rows','cells','tableContent'):visit(item)
                elif key=='props' and isinstance(item,dict):
                    for name in ('title','caption','alt'):
                        if isinstance(item.get(name),str):parts.append(item[name])
        elif isinstance(value,str):parts.append(value)
    visit(blocks);return ' '.join(parts)

def search_pages(rows,query):
    terms=list(dict.fromkeys(normalize(query).split()));matches=[]
    if not terms:return []
    for row in rows:
        title=normalize(row['title']);text=' '.join((row['body']+' '+visible_blocks(json.loads(row['blocks']) if row['blocks'] else [])).split())
        normalized=normalize(text)
        if not all(term in title+' '+normalized for term in terms):continue
        position=next((normalized.find(term) for term in terms if term in normalized),0)
        # Keep literal punctuation: the preview is escaped as plain text by UI.
        preview=text
        position=max(0,min(position,len(preview))-45);excerpt=preview[position:position+180]
        if position:excerpt='…'+excerpt
        if position+180<len(preview):excerpt+='…'
        score=0 if title==normalize(query) else 1 if all(term in title for term in terms) else 2
        matches.append((score,{'id':row['id'],'title':row['title'],'icon':row['icon'],'parent_id':row['parent_id'],'updated':row['updated'],'excerpt':excerpt}))
    matches.sort(key=lambda item:(item[0],-item[1]['updated'],item[1]['title']))
    return [item[1] for item in matches]
