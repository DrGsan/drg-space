"""Outline Markdown exports share the bounded ZIP/attachment import pipeline."""
import hashlib
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit,unquote,parse_qs
import mimetypes
from app.i18n import text
from app.notion_import import NotionArchive,Page,invalid,norm,rewrite_markdown_links

# Outline puts the icon before the title, separated by whitespace. Keep flags,
# skin tones, variation selectors and ZWJ sequences together as one icon.
EMOJI = r'(?:[\U0001f300-\U0001faff\u2600-\u27bf\u231a-\u231b\u23e9-\u23fa\u25aa-\u25fe\u2b05-\u2b07\u2b1b-\u2b1c\u2b50\u2b55](?:\ufe0f)?[\U0001f3fb-\U0001f3ff]?)'
ICON = re.compile(r'^([\U0001f1e6-\U0001f1ff]{2}|[#*0-9]\ufe0f?\u20e3|[©®™‼⁉]\ufe0f|[\U0001f004\U0001f0cf\U0001f170-\U0001f251]\ufe0f?|'+EMOJI+r'(?:\u200d'+EMOJI+r')*)(?:\s+|$)')


def page_title(value):
    icon = ICON.match(value)
    return (value[icon.end():] or text('untitled'), icon[1]) if icon else (value, '')

class OutlineArchive(NotionArchive):
    namespace_parts = False
    def _pages(self):
        for name in self.files:
            path=PurePosixPath(name)
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
            folder=posixpath.dirname(page.source)
            while folder:
                parent=self.aliases.get(folder+'.md') or collections.get(folder)
                if parent:page.parent=parent;break
                upper=posixpath.dirname(folder)
                if not upper:
                    title,icon=page_title(folder)
                    if not 1<=len(title)<=200:invalid('Название страницы Outline: от 1 до 200 символов.')
                    source=folder+'/.outline-collection'
                    identity=hashlib.sha256(source.encode()).hexdigest()[:32]
                    parent=Page(source,identity,title,'md',icon=icon)
                    collections[folder]=self.pages[source]=self.by_uuid[identity]=parent
                    page.parent=parent
                folder=upper

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
        if not parsed.scheme and path in self.files:return self.file_aliases.get(path,path)
        # Single-document exports may keep attachment redirect URLs while the
        # binary is bundled under its UUID. Resolve only a file inside this ZIP.
        identity=parse_qs(parsed.query).get('id',[''])[0]
        if identity and len(identity)==36:
            matches=[name for name in self.files if PurePosixPath(name).stem==identity and 'attachments' in PurePosixPath(name).parts]
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
