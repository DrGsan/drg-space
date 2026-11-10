"""Outline Markdown exports share the bounded ZIP/attachment import pipeline."""
import hashlib
import posixpath
from pathlib import PurePosixPath
from urllib.parse import urlsplit,unquote,parse_qs
import mimetypes
from app.notion_import import NotionArchive,Page,invalid,norm

class OutlineArchive(NotionArchive):
    namespace_parts = False
    def _pages(self):
        for name in self.files:
            path=PurePosixPath(name)
            if path.suffix.lower()!='.md' or any(part.lower() in ('attachments','uploads') for part in path.parts[:-1]):continue
            body=self.read(name,True);first,_,rest=body.partition('\n')
            title=first[2:].strip() if first.startswith('# ') else path.stem
            if not 1<=len(title)<=200:invalid('Название страницы Outline: от 1 до 200 символов.')
            identity=hashlib.sha256(name.encode()).hexdigest()[:32]
            page=Page(name,identity,title,'md');page.body=rest.lstrip('\n') if first.startswith('# ') else body
            self.pages[name]=page;self.aliases[name]=page;self.by_uuid[identity]=page
        if not self.pages:invalid('В ZIP не найдены страницы Outline. Нужен экспорт Markdown с вложениями.')
        for page in self.pages.values():
            folder=posixpath.dirname(page.source)
            while folder:
                parent=self.aliases.get(folder+'.md')
                if parent:page.parent=parent;break
                folder=posixpath.dirname(folder)

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
