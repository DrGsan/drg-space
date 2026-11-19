"""Outline Markdown exports share the bounded ZIP/attachment import pipeline."""
import hashlib
import json
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit,unquote,parse_qs
import mimetypes
from app.i18n import text
from app.notion_import import MAX_TEXT,NotionArchive,Page,invalid,norm,rewrite_markdown_links

# Outline puts the icon before the title, separated by whitespace. Keep flags,
# skin tones, variation selectors and ZWJ sequences together as one icon.
EMOJI = r'(?:[\U0001f300-\U0001faff\u2600-\u27bf\u231a-\u231b\u23e9-\u23fa\u25aa-\u25fe\u2b05-\u2b07\u2b1b-\u2b1c\u2b50\u2b55](?:\ufe0f)?[\U0001f3fb-\U0001f3ff]?)'
ICON = re.compile(r'^([\U0001f1e6-\U0001f1ff]{2}|[#*0-9]\ufe0f?\u20e3|[©®™‼⁉]\ufe0f|[\U0001f004\U0001f0cf\U0001f170-\U0001f251]\ufe0f?|'+EMOJI+r'(?:\u200d'+EMOJI+r')*)(?:\s+|$)')


def page_title(value):
    icon = ICON.match(value)
    return (value[icon.end():] or text('untitled'), icon[1]) if icon else (value, '')

class OutlineArchive(NotionArchive):
    def collection_icons(self):
        # A native Outline JSON export can accompany Markdown with the icon
        # metadata omitted by the Markdown collection directories.
        manifests = {}
        for name,(owner,info) in self.files.items():
            if info.filename!='metadata.json' or info.file_size>64*1024:continue
            try:metadata=json.loads(self.read(name,True))
            except (UnicodeError,ValueError):continue
            if isinstance(metadata,dict) and metadata.get('exportVersion')==1:
                manifests[owner]=name
        collections={}
        consumed=set()
        for name,(owner,info) in self.files.items():
            path=PurePosixPath(info.filename)
            if (owner not in manifests or name==manifests[owner] or info.file_size>MAX_TEXT
                    or len(path.parts)!=1 or path.suffix!='.json'):
                continue
            try:data=json.loads(self.read(name,True))
            except (UnicodeError,ValueError):continue
            if not isinstance(data,dict) or not all(isinstance(data.get(key),dict) for key in ('collection','documents','attachments')):
                continue
            if data['attachments']:
                invalid(text('outlineCollectionJsonWithoutAttachments'))
            collection=data['collection']
            icon=collection.get('icon')
            if isinstance(icon,str) and len(icon)<=100 and ICON.fullmatch(icon):
                collections.setdefault(norm(path.stem),set()).add(icon)
            consumed.update((name,manifests[owner]))
        for name in consumed:
            self.files.pop(name)
        return {name:next(iter(icons)) for name,icons in collections.items() if len(icons)==1}

    def _pages(self):
        collection_icons=self.collection_icons()
        for name,(_,info) in self.files.items():
            path=PurePosixPath(info.filename)
            if path.suffix.lower()!='.md' or any(part.lower() in ('attachments','uploads') for part in path.parts[:-1]):continue
            body=self.read(name,True);first,_,rest=body.partition('\n')
            title,icon=page_title(first[2:].strip() if first.startswith('# ') else path.stem)
            if not 1<=len(title)<=200:invalid('Название страницы Outline: от 1 до 200 символов.')
            identity=hashlib.sha256(name.encode()).hexdigest()[:32]
            page=Page(name,identity,title,'md',icon=icon);page.body=rest.lstrip('\n') if first.startswith('# ') else body
            self.pages[name]=page;self.aliases[name]=page;self.by_uuid[identity]=page
        if not self.pages:invalid('В ZIP не найдены страницы Outline. Нужен экспорт Markdown с вложениями.')
        collections={}
        for page in list(self.pages.values()):
            source=norm(self.files[page.source][1].filename)
            root=source.split('/')[0] if '/' in source else None
            if not root:continue
            top=page.source.removesuffix(source)+root
            folder=posixpath.dirname(page.source)
            while folder:
                parent=self.aliases.get(folder+'.md') or collections.get(folder)
                if parent:page.parent=parent;break
                if folder==top:
                    title,icon=page_title(root)
                    if not 1<=len(title)<=200:invalid('Название страницы Outline: от 1 до 200 символов.')
                    source=folder+'/.outline-collection'
                    identity=hashlib.sha256(source.encode()).hexdigest()[:32]
                    parent=Page(source,identity,title,'md',icon=collection_icons.get(root,icon))
                    collections[folder]=self.pages[source]=self.by_uuid[identity]=parent
                    page.parent=parent
                    break
                folder=posixpath.dirname(folder)

    def markdown_links(self,page,body,replacements=None):
        if replacements is not None:
            return super().markdown_links(page,body,replacements)

        def attachment_label(value,label):
            local=self.local(page,value)
            if local not in self.attachments:return label
            path=PurePosixPath(local)
            if not any(part.lower() in ('uploads','attachments') for part in path.parts[:-1]):return label
            if label==f'{path.name} {self.files[local][1].file_size}':return path.name
            return label

        return rewrite_markdown_links(body,lambda url,image:self.link(page,url,image),attachment_label)

    def local(self,page,value):
        found=super().local(page,value)
        if found:return found
        try:parsed=urlsplit(value)
        except ValueError:return None
        path=norm(unquote(parsed.path)).lstrip('/')
        if not parsed.scheme and not parsed.netloc:
            candidates=self.logical_files.get(path,set())
            if len(candidates)==1:return next(iter(candidates))
        # Single-document exports may keep attachment redirect URLs while the
        # binary is bundled under its UUID. Resolve only a file inside this ZIP.
        identity=parse_qs(parsed.query).get('id',[''])[0]
        if identity and len(identity)==36:
            matches=[name for name in self.files if PurePosixPath(name).stem==identity and 'attachments' in PurePosixPath(name).parts]
            source=self.files.get(page.source)
            own=[name for name in matches if source and self.files[name][0] is source[0]]
            if own:matches=own
            if len(matches)==1:return self.file_aliases.get(matches[0],matches[0])
        return None

    def mime_type(self,name):
        mime=mimetypes.guess_type(name)[0]
        if mime and mime!='application/octet-stream':return mime
        owner,info=self.files[name]
        with owner.open(info) as stream:header=stream.read(16)
        if header.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
        if header.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
        if header.startswith((b'GIF87a',b'GIF89a')):return 'image/gif'
        if header.startswith(b'RIFF') and header[8:12]==b'WEBP':return 'image/webp'
        if header.startswith(b'%PDF-'):return 'application/pdf'
        return 'application/octet-stream'
