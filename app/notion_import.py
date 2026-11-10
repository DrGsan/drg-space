"""Untrusted Notion exports -> private notes, without extraction or schema changes.

Only files with Notion UUID names become pages. Other HTML/CSV files are
attachments. Every local link resolves against the archive, never the host FS.
"""
import csv
import hashlib
import io
import json
import mimetypes
import posixpath
import re
import secrets
import stat
import unicodedata
import zipfile
from collections import defaultdict
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from app.archive_stream import EncryptedSpool
from app.modules.notes import validate_blocks
from app.modules.notion import validate_config, validate_values

MAX_UPLOAD = 512 * 1024**2
MAX_EXPANDED = 1024**3
MAX_TEXT = 8 * 1024**2
PAGE_NAME = re.compile(r'^(.*) ([0-9a-f]{32})(?:(_all))?\.(md|html|csv)$', re.I)


class NotionImportError(ValueError):
    pass


def invalid(message):
    raise NotionImportError(message)


def norm(value):
    return unicodedata.normalize('NFC', value)


def node_styles(node):
    props = {}
    for cls in node.attrs.get('class', '').split():
        match = re.fullmatch(r'block-color-(gray|brown|orange|yellow|green|teal|blue|purple|pink|red)(_background)?', cls)
        if match:
            props['backgroundColor' if match[2] else 'textColor'] = 'green' if match[1] == 'teal' else match[1]
    align = re.search(r'(?:^|;)\s*text-align\s*:\s*(left|right|center|justify)\b', node.attrs.get('style', ''), re.I)
    if align:
        props['textAlignment'] = align[1].lower()
    return props


@dataclass
class Node:
    tag: str = ''
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def has(self, name):
        return name in self.attrs.get('class', '').split()

    def all(self, tag=None, cls=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or child.has(cls)):
                    yield child
                yield from child.all(tag, cls)

    def text(self):
        if self.tag in ('script', 'style', 'svg'):
            return ''
        if self.tag == 'br':
            return '\n'
        return ''.join(child.text() if isinstance(child, Node) else child for child in self.children)


class Tree(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.count = 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 100000 or len(self.stack) > 100:
            invalid('Слишком сложная HTML-страница в архиве.')
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def block(kind='paragraph', content=None, props=None, children=None):
    # Exported text links have an explicit appearance; editor paste heuristics
    # must not expand them into bookmark cards on opening the imported page.
    if kind == 'paragraph' and content and len(content) == 1 and (content[0].get('type') == 'link' or
            content[0].get('type') == 'text' and re.fullmatch(r'https?://\S+', content[0].get('text',''))):
        props = {'linkDisplay':'url', **(props or {})}
    result = {'id': secrets.token_urlsafe(12), 'type': kind, 'children': children or []}
    if content is not None:
        result['content'] = content
    if props:
        result['props'] = props
    return result


def plain(value, styles=None):
    return {'type': 'text', 'text': value, 'styles': styles or {}}


def safe_url(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username
            or parsed.scheme == 'mailto' or value.startswith('#') and len(value) < 150)


@dataclass
class Page:
    source: str
    uuid: str
    title: str
    kind: str
    identity: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    parent: object = None
    tree: object = None
    body: str = ''
    blocks: object = None
    icon: str = ''
    decorations: dict = field(default_factory=dict)
    properties: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    title_column: int = 0
    links: list = field(default_factory=list)
    position: int = 0
    saved_assets: set = field(default_factory=set)


class NotionArchive:
    namespace_parts = True
    def __init__(self, source, directory, parts=None):
        self.stack = ExitStack()
        self.directory = directory
        self.files = {}
        self.pages = {}
        self.aliases = {}
        self.directories = {}
        self.ambiguous_directories = set()
        self.by_uuid = {}
        self.attachments = {}
        self.warnings = set()
        self.issues = []
        self.missing = set()
        self.references = set()
        self.remote_images = set()
        self.document_images = set()
        self.image_downloads = {}
        self.image_failures = []
        self.downloaded_files = []
        self.file_aliases = {}
        self.logical_files = defaultdict(set)
        self.total_expanded = 0
        self.digest = hashlib.sha256()
        try:
            source.seek(0)
            while chunk := source.read(1024**2):
                self.digest.update(chunk)
            source.seek(0)
            if parts:
                self.digest.update(json.dumps(parts).encode())
                offset = 0
                for index, size in enumerate(parts):
                    self._zip(ArchiveSlice(source, offset, size), namespace=f'Export-upload-{index}/' if len(parts)>1 and self.namespace_parts else '')
                    offset += size
            else:
                self._zip(source)
            self._ignore_indexes()
            self._pages()
            self._deduplicate_files()
            for name, (_, info) in self.files.items():
                self.logical_files[norm(info.filename)].add(self.file_aliases.get(name, name))
            self._databases()
            self._documents()
            self._order()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.stack.close()

    def _zip(self, source, depth=0, namespace=''):
        if depth > 2:
            invalid('В архиве слишком много уровней вложенных ZIP.')
        archive = self.stack.enter_context(zipfile.ZipFile(source))
        entries = archive.infolist()
        if len(entries) > 20000:
            invalid('В архиве больше 20 000 файлов.')
        seen = set()
        for info in entries:
            name = norm(info.filename)
            path = PurePosixPath(name)
            if (name in seen or name.startswith('/') or '\\' in name or '\x00' in name
                    or any(part in ('.', '..') for part in name.rstrip('/').split('/'))
                    or re.match(r'^[A-Za-z]:', name) or stat.S_ISLNK(info.external_attr >> 16)):
                invalid('ZIP содержит небезопасные или повторяющиеся пути.')
            seen.add(name)
            if info.flag_bits & 1:
                invalid('ZIP защищён паролем. Экспортируйте обычный ZIP из Notion.')
            if info.is_dir() or '__MACOSX' in path.parts or path.name == '.DS_Store':
                continue
            name = namespace+name
            self.total_expanded += info.file_size
            if self.total_expanded > MAX_EXPANDED:
                invalid('Распакованный архив превышает 1 Гб.')
            if len(self.files) >= 20000:
                invalid('В частях экспорта больше 20 000 файлов.')
            if name in self.files:
                invalid('Части экспорта содержат повторяющиеся пути.')
            self.files[name] = (archive, info)
        # Notion wraps export parts in another ZIP. Ordinary attached ZIPs stay files.
        own_names = [name for name, (owner, _) in self.files.items() if owner is archive]
        parts = [name for name in own_names if re.fullmatch(r'(?:.*/)?Export-[^/]+(?:-Part-\d+)?\.zip', name, re.I)]
        if parts and not any(PAGE_NAME.match(PurePosixPath(name).name) for name in own_names):
            for index, name in enumerate(parts):
                owner, info = self.files.pop(name)
                if info.file_size > MAX_UPLOAD:
                    invalid('Вложенный ZIP превышает 512 Мб.')
                spool = EncryptedSpool(self.directory)
                self.stack.callback(spool.close)
                with owner.open(info) as stream:
                    while chunk := stream.read(1024**2):
                        spool.write(chunk)
                spool.finish()
                self._zip(spool, depth+1, namespace=namespace+f'Export-nested-{index}/' if len(parts)>1 and self.namespace_parts else namespace)

    def read(self, name, text=False):
        archive, info = self.files[name]
        if text and info.file_size > MAX_TEXT:
            invalid('Текстовая страница или CSV превышает 8 Мб.')
        data = archive.read(info)
        return data.decode('utf-8-sig') if text else data

    def _ignore_indexes(self):
        # Notion adds a workspace table of contents to each export format.
        # A user's own attachment named index.html must still survive.
        for name in list(self.files):
            if PurePosixPath(name).name != 'index.html' or self.files[name][1].file_size > MAX_TEXT:
                continue
            tree = Tree(self.read(name, True)).root
            title = next(tree.all('title'), Node()).text()
            if title.startswith('Export-') and 'Workspace identifier:' in tree.text() and any(node.attrs.get('id', '').startswith('id::') for node in tree.all('ul')):
                del self.files[name]

    def _pages(self):
        variants = defaultdict(list)
        for name in self.files:
            match = PAGE_NAME.match(PurePosixPath(name).name)
            if match:
                variants[match[2].lower()].append((name, match))
        for uuid, entries in variants.items():
            # HTML has richer block semantics; _all.csv has all exported view rows.
            name, match = min(entries, key=lambda item: {'html': 0, 'md': 1, 'csv': 2}[item[1][4].lower()])
            title = match[1].strip() or 'Без названия'
            if len(title) > 200:
                invalid('Название страницы длиннее 200 символов.')
            page = Page(name, uuid, title, match[4].lower())
            if page.kind == 'html':
                page.tree = Tree(self.read(name, True)).root
                article = next(page.tree.all('article', 'page'), None)
                if article is None:
                    invalid('HTML с идентификатором страницы не является экспортом Notion.')
                heading = next(article.all(cls='page-title'), None)
                if heading is not None:
                    page.title = heading.text().strip() or title
                page.tree = article
            elif page.kind == 'md':
                page.body = self.read(name, True)
                first, _, rest = page.body.partition('\n')
                if first.startswith('# '):
                    page.title = first[2:].strip() or title
                    page.body = rest.lstrip('\n')
            if len(page.title) > 200:
                invalid('Название страницы длиннее 200 символов.')
            self.pages[name] = page
            self.by_uuid[uuid] = page
            for alias, _ in entries:
                self.aliases[alias] = page
        if not self.pages:
            invalid('В ZIP не найдены страницы Notion. Нужен экспорт HTML или Markdown & CSV с подстраницами.')
        # Current exports omit UUIDs from directory names. Keep both spellings,
        # from every format/part, since the richest page may live in another ZIP.
        directories = defaultdict(dict)
        for name, page in self.aliases.items():
            for spelling in {name, norm(self.files[name][1].filename)}:
                match = PAGE_NAME.match(PurePosixPath(spelling).name)
                stem = spelling.rsplit('.', 1)[0]
                if match[3]:
                    stem = stem[:-4]
                for directory in (stem, posixpath.join(posixpath.dirname(spelling), match[1])):
                    directories[norm(directory)][page.identity] = page
        self.directories = {name: next(iter(owners.values())) for name, owners in directories.items() if len(owners) == 1}
        self.ambiguous_directories = {name for name, owners in directories.items() if len(owners) > 1}
        for page in self.pages.values():
            page.parent = self.folder_owner(page.source, exclude=page)
        # Validate after resolving every edge, independently of ZIP entry order.
        for page in self.pages.values():
            depth, parent = 0, page.parent
            while parent:
                depth += 1
                if depth > 30 or parent is page:
                    invalid('Дерево страниц глубже 30 уровней.')
                parent = parent.parent

    def folder_owner(self, name, exclude=None):
        source = self.files.get(name)
        spellings = [name] + ([norm(source[1].filename)] if source and norm(source[1].filename) != name else [])
        for spelling in spellings:
            folder = posixpath.dirname(spelling)
            while folder:
                if folder in self.ambiguous_directories:
                    self.warnings.add('В экспорте есть неоднозначные имена папок: вложенность таких страниц нужно проверить.')
                    return None
                owner = self.directories.get(folder) or next((self.aliases[folder+ext] for ext in ('.html', '.md', '.csv') if folder+ext in self.aliases), None)
                if owner is not None and owner is not exclude:
                    return owner
                folder = posixpath.dirname(folder)
        return None

    def _deduplicate_files(self):
        # Two export formats may be uploaded together. UUIDs merge pages; equal
        # path+bytes merge attachments. Changed attachments survive as two files.
        groups = defaultdict(list)
        for name in self.files:
            if name not in self.aliases:
                parts = name.split('/')
                while len(parts)>1 and parts[0].startswith('Export-'):
                    parts = parts[1:]
                logical = '/'.join(parts)
                groups[logical].append(name)
        for names in groups.values():
            if len(names) < 2:
                continue
            prefixes = {page.source.split('/')[0] for page in self.pages.values() if page.tree}
            names.sort(key=lambda name: name.split('/')[0] not in prefixes)
            hashes = {}
            for name in names:
                digest = hashlib.sha256()
                archive, info = self.files[name]
                with archive.open(info) as stream:
                    while chunk := stream.read(1024**2):
                        digest.update(chunk)
                key = digest.digest()
                if key in hashes:
                    self.file_aliases[name] = hashes[key]
                else:
                    hashes[key] = name

    def local(self, page, value):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc or not parsed.path:
            return None
        path = norm(posixpath.normpath(posixpath.join(posixpath.dirname(page.source), unquote(parsed.path))))
        if path in self.files:
            return self.file_aliases.get(path, path)
        # Export parts have independent namespaces. A relative link can point
        # into another part; resolve its original ZIP path only when unambiguous.
        source = self.files.get(page.source)
        if source:
            logical = norm(posixpath.normpath(posixpath.join(posixpath.dirname(source[1].filename), unquote(parsed.path))))
            candidates = self.logical_files.get(logical, set())
            if len(candidates) == 1:
                return next(iter(candidates))
        return None

    def page_link(self, page, value):
        local = self.local(page, value)
        if local in self.aliases:
            return self.aliases[local]
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if not parsed.scheme and not parsed.netloc:
            match = PAGE_NAME.match(PurePosixPath(unquote(parsed.path)).name)
            if match:
                return self.by_uuid.get(match[2].lower())
        if parsed.hostname and (parsed.hostname in ('notion.so', 'notion.com') or parsed.hostname.endswith(('.notion.so', '.notion.site', '.notion.com'))):
            uuid = re.search(r'([0-9a-f]{32})(?:$|[/?#])', parsed.path.replace('-', ''), re.I)
            if uuid:
                return self.by_uuid.get(uuid[1].lower())
        return None

    def link(self, page, value, image=False):
        target = self.page_link(page, value)
        if target:
            page.links.append(target.identity)
            fragment = urlsplit(value).fragment.replace('-', '')
            return '/page/'+target.identity+('#block='+fragment if re.fullmatch(r'[a-f0-9]{32}', fragment, re.I) and fragment != target.uuid else '')
        if value.startswith('#') and re.fullmatch(r'[a-f0-9-]{32,36}', value[1:], re.I):
            return '#block='+value[1:].replace('-', '')
        local = self.local(page, value)
        if local:
            self.references.add(local)
            owner = self.folder_owner(local) or page
            item = self.attachments.setdefault(local, {'id': secrets.token_urlsafe(12), 'page': owner})
            return '/api/notes/'+item['page'].identity+'/files/'+item['id']
        if safe_url(value):
            if image and value.startswith(('https:', 'http:')):
                self.remote_images.add(value)
                self.document_images.add(value)
            return value
        if value:
            self.missing.add((page.uuid, value))
        return ''

    def inline(self, page, nodes, styles=None):
        output = []
        for node in nodes:
            if isinstance(node, str):
                if node:
                    output.append(plain(node, dict(styles or {})))
                continue
            if node.tag in ('script', 'style', 'svg') or node.has('icon') or node.has('checkbox'):
                continue
            marks = dict(styles or {})
            mark = {'strong': 'bold', 'b': 'bold', 'em': 'italic', 'i': 'italic', 'del': 'strike', 's': 'strike', 'u': 'underline', 'code': 'code'}.get(node.tag)
            if mark:
                marks[mark] = True
            for cls in node.attrs.get('class', '').split():
                match = re.fullmatch(r'highlight-(\w+?)(_background)?', cls)
                if match and match[1] in {'gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'}:
                    marks['backgroundColor' if match[2] else 'textColor'] = match[1]
            if node.tag == 'br':
                output.append(plain('\n', marks))
            elif node.tag == 'a':
                content = self.inline(page, node.children, marks)
                # Bookmark thumbnails can themselves be links. BlockNote links
                # contain styled text only, never another inline link.
                content = [text for part in content for text in (part['content'] if part['type'] == 'link' else [part])]
                url = self.link(page, node.attrs.get('href', ''))
                output.append({'type': 'link', 'href': url, 'content': content} if url else plain(node.text(), marks))
            elif node.tag == 'img':
                # Inline images still retain their source as a link.
                url = self.link(page, node.attrs.get('src', ''), True)
                if url:
                    output.append({'type': 'link', 'href': url, 'content': [plain(node.attrs.get('alt') or 'Изображение')]})
            else:
                output.extend(self.inline(page, node.children, marks))
        return output

    def html_blocks(self, page, nodes, depth=0):
        if depth > 30:
            invalid('Блоки вложены глубже 30 уровней.')
        result, pending = [], []
        def flush():
            if any(isinstance(x, Node) or x.strip() for x in pending):
                result.append(block(content=self.inline(page, pending)))
            pending.clear()
        containers = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'figure', 'pre', 'blockquote', 'table', 'hr', 'details', 'img', 'audio', 'video'}
        for node in nodes:
            if not isinstance(node, Node) or node.tag not in containers and not node.has('bookmark'):
                if not isinstance(node, Node) or node.tag not in ('script', 'style', 'svg'):
                    pending.append(node)
                continue
            flush()
            first = len(result)
            tag = node.tag
            if node.has('column-list'):
                columns = []
                for column in node.children:
                    if not isinstance(column, Node) or not column.has('column'):
                        continue
                    width = re.search(r'(?:^|;)\s*width\s*:\s*([0-9]+(?:\.[0-9]+)?)%', column.attrs.get('style', ''), re.I)
                    ratio = float(width[1]) if width and 0 < float(width[1]) <= 100 else 1
                    children = self.html_blocks(page, column.children, depth+2)
                    columns.append(block('column', props={'width': ratio}, children=children or [block()]))
                if columns:
                    result.append(block('columnList', children=columns))
            elif tag == 'blockquote':
                parts = self.html_blocks(page, node.children, depth+1)
                content = parts.pop(0)['content'] if parts and parts[0]['type'] == 'paragraph' else []
                result.append(block('quote', content, children=parts))
            elif tag == 'p' or re.fullmatch(r'h[1-6]', tag):
                elements = [child for child in node.children if isinstance(child, Node) or child.strip()]
                anchor = elements[0] if tag == 'p' and len(elements) == 1 and isinstance(elements[0], Node) and elements[0].tag == 'a' else None
                local = self.local(page, anchor.attrs.get('href', '')) if anchor and not next(anchor.all('img'), None) else None
                if local and local not in self.aliases:
                    result.append(block('file', props={'url': self.link(page, anchor.attrs['href']),
                                                      'name': PurePosixPath(local).name, 'caption': anchor.text()}))
                    continue
                kind = 'heading' if tag.startswith('h') else 'quote' if tag == 'blockquote' else 'paragraph'
                result.append(block(kind, self.inline(page, node.children), {'level': min(3, int(tag[1]))} if kind == 'heading' else None))
            elif tag == 'pre':
                code = next(node.all('code'), node)
                language = next((c.removeprefix('language-') for c in code.attrs.get('class', '').split() if c.startswith('language-')), 'text').lower()
                language = {'plain': 'text', 'plain text': 'text', 'shell': 'bash'}.get(language, language)
                result.append(block('codeBlock', [plain(code.text())], {'language': language}))
            elif tag == 'hr':
                result.append(block('divider'))
            elif tag in ('ul', 'ol'):
                for index, item in enumerate(x for x in node.children if isinstance(x, Node) and x.tag == 'li'):
                    details = next((x for x in item.children if isinstance(x, Node) and x.tag == 'details'), None)
                    if details and all(isinstance(x, str) and not x.strip() or isinstance(x, Node) and x.tag == 'details' for x in item.children):
                        result.extend(self.html_blocks(page, item.children, depth+1))
                        continue
                    split = next((i for i, x in enumerate(item.children) if isinstance(x, Node) and x.tag in containers), len(item.children))
                    content = self.inline(page, item.children[:split])
                    children = self.html_blocks(page, item.children[split:], depth+1)
                    kind = 'numberedListItem' if tag == 'ol' else 'toggleListItem' if node.has('toggle') else 'checkListItem' if node.has('to-do-list') else 'bulletListItem'
                    props = {}
                    if kind == 'numberedListItem' and index == 0:
                        start = node.attrs.get('start', '1')
                        if not start.isdecimal() or not 1 <= int(start) <= 1000000:
                            invalid('Некорректный номер начала списка в HTML.')
                        props['start'] = int(start)
                    if kind == 'checkListItem':
                        props['checked'] = any(x.has('checkbox-on') for x in item.all()) or any('checked' in x.attrs for x in item.all('input'))
                    if not content and children and children[0]['type'] == 'paragraph':
                        content = children.pop(0)['content']
                    result.append(block(kind, content, props, children))
            elif tag == 'details':
                summary = next((x for x in node.children if isinstance(x, Node) and x.tag == 'summary'), Node())
                heading = next(summary.all(), None)
                props = {'level': min(3, int(heading.tag[1])), 'isToggleable': True} if heading and re.fullmatch(r'h[1-6]', heading.tag) else None
                result.append(block('heading' if props else 'toggleListItem', self.inline(page, summary.children), props,
                                    self.html_blocks(page, [x for x in node.children if x is not summary], depth+1)))
            elif tag == 'table':
                if node.has('collection-content'):
                    targets = [self.page_link(page, anchor.attrs.get('href', '')) for anchor in node.all('a')]
                    database = next((target.parent for target in targets if target and target.parent and target.parent.kind == 'database'), None)
                    if database and database is not page:
                        page.links.append(database.identity)
                        result.append(block('pageLink', props={'pageId': database.identity, 'title': database.title}))
                    if database or page.kind == 'database':
                        continue
                    self.warnings.add('Встроенная база без отдельной страницы или CSV сохранена как таблица; её строки и ссылки сохранены.')
                rows = [[self.inline(page, cell.children) for cell in row.children if isinstance(cell, Node) and cell.tag in ('td', 'th')] for row in node.all('tr')]
                if rows:
                    width = max(map(len, rows))
                    result.append(block('table', {'type': 'tableContent', 'rows': [{'cells': cells+[[]]*(width-len(cells))} for cells in rows]}))
            elif tag in ('img', 'audio', 'video'):
                url = self.link(page, node.attrs.get('src', ''), tag == 'img')
                if url:
                    result.append(block('image' if tag == 'img' else tag, props={'url': url, 'caption': node.attrs.get('alt', '')}))
            elif tag == 'figure' and node.has('callout'):
                icon = next(node.all(cls='icon'), None)
                parts = self.html_blocks(page, [child for child in node.children if child is not icon], depth+1)
                content = parts.pop(0)['content'] if parts and parts[0]['type'] == 'paragraph' else []
                result.append(block('callout', content, {'icon': icon.text() if icon else '💡'}, parts))
            elif tag == 'figure' and node.has('image'):
                image = next(node.all('img'), None)
                if image:
                    url = self.link(page, image.attrs.get('src', ''), True)
                    caption = next(node.all('figcaption'), Node()).text()
                    if url:
                        result.append(block('image', props={'url': url, 'caption': caption, 'name': PurePosixPath(unquote(urlsplit(image.attrs.get('src', '')).path)).name}))
            elif node.has('link-to-page'):
                anchor = next(node.all('a'), None)
                target = anchor and self.page_link(page, anchor.attrs.get('href', ''))
                if target:
                    page.links.append(target.identity)
                    result.append(block('pageLink', props={'pageId': target.identity, 'title': target.title}))
                else:
                    result.extend(self.html_blocks(page, node.children, depth+1))
            elif node.has('bookmark'):
                anchor = node if node.tag == 'a' else next(node.all('a'), None)
                title = next(node.all(cls='bookmark-title'), Node()).text().strip()
                description = next(node.all(cls='bookmark-description'), Node()).text().strip()
                preview = next(node.all(cls='bookmark-image'), None)
                image = preview if preview and preview.tag == 'img' else next(preview.all('img'), None) if preview else None
                if image is None:
                    image = next((item for item in node.all('img') if not item.has('bookmark-icon')), None)
                props = {'linkDisplay':'bookmark', 'title':title[:500], 'description':description[:5000]}
                if image:
                    props['url'] = self.link(page, image.attrs.get('src', ''), True)
                url = self.link(page, anchor.attrs.get('href', '')) if anchor else ''
                # Preserve visible export text with its block boundaries, while
                # keeping structured card metadata editable across all clients.
                def lines(item):
                    if isinstance(item,str):return item
                    if item.tag in ('svg','img','script','style') or item.has('icon'):return ''
                    text = ''.join(lines(child) for child in item.children)
                    return '\n'+text+'\n' if item.tag in ('div','p','figcaption') else text
                label = '\n'.join(line.strip() for line in lines(node).splitlines() if line.strip())
                result.append(block(content=[{'type':'link','href':url,'content':[plain(label or title or url)]}] if url else [plain(label)], props=props))
            elif tag == 'figure' and (node.has('file') or node.has('attachment') or
                                      any('data-notion-file' in anchor.attrs for anchor in node.all('a'))):
                anchor = next(node.all('a'), None)
                value = anchor.attrs.get('href', '') if anchor else ''
                local = self.local(page, value)
                if local and local not in self.aliases:
                    url = self.link(page, value)
                    name = anchor.attrs.get('data-notion-file-alias','')
                    if not name or len(name)>255 or any(char in '/\\' or ord(char)<32 for char in name):
                        name = PurePosixPath(local).name
                    self.attachments[local]['name'] = name
                    result.append(block('file', props={'url': url, 'name': name,
                                                      'caption': next(node.all('figcaption'), Node()).text()}))
                else:
                    result.append(block(content=self.inline(page, node.children)))
            else:
                result.extend(self.html_blocks(page, node.children, depth+1))
            created = result[first:]
            if created:
                identity = node.attrs.get('id', '').replace('-', '')
                if re.fullmatch(r'[a-f0-9]{32}', identity, re.I):
                    created[0]['id'] = identity
                styles = node_styles(node)
                for item in created:
                    for key, value in styles.items():
                        item.setdefault('props', {}).setdefault(key, value)
        flush()
        return result

    def _databases(self):
        for page in list(self.pages.values()):
            csv_names = [name for name, alias in self.aliases.items() if alias is page and name.endswith('.csv')]
            table = next(page.tree.all('table', 'collection-content'), None) if page.tree else None
            body = next(page.tree.all(cls='page-body'), Node()) if page.tree else None
            full_table = table is not None and body.text().strip() == table.text().strip()
            if not csv_names and not full_table:
                continue
            page.kind = 'database'
            headers, types, data, row_targets = [], [], [], []
            if csv_names:
                csv_name = next((name for name in csv_names if name.endswith('_all.csv')), csv_names[0])
                rows = list(csv.reader(io.StringIO(self.read(csv_name, True))))
                if rows:
                    headers, data = rows[0], rows[1:]
                if len(headers) > 31 or len(data) > 2000 or any(len(row) != len(headers) for row in data):
                    invalid('База в CSV превышает лимит колонок/записей или содержит повреждённые строки.')
            if table:
                head = next(table.all('thead'), Node())
                columns = list(head.all('th'))
                html_headers = [column.text().strip() for column in columns]
                for column in columns:
                    svg = next(column.all('svg'), Node())
                    types.append(svg.attrs.get('class', ''))
                if not headers:
                    headers = html_headers
                else:
                    types = [types[html_headers.index(name)] if name in html_headers else '' for name in headers]
                page.title_column = next((i for i, kind in enumerate(types) if kind == 'typesTitle'), 0)
                if not csv_names:
                    for row in table.all('tr'):
                        cells = [cell for cell in row.children if isinstance(cell, Node) and cell.tag == 'td']
                        if not cells:
                            continue
                        if len(cells) != len(headers):
                            invalid('Число ячеек HTML-базы не совпадает с заголовками.')
                        values = []
                        for index, cell in enumerate(cells):
                            kind = types[index] if index < len(types) else ''
                            if kind == 'typesCheckbox':
                                value = 'Yes' if any(x.has('checkbox-on') for x in cell.all()) or any('checked' in x.attrs for x in cell.all('input')) else 'No'
                            elif kind == 'typesUrl':
                                anchor = next(cell.all('a'), None)
                                value = anchor.attrs.get('href', '') if anchor else cell.text().strip()
                            elif kind == 'typesMultipleSelect':
                                selected = list(cell.all(cls='selected-value'))
                                value = ', '.join(x.text().strip() for x in selected) if selected else cell.text().strip()
                            else:
                                value = cell.text().strip()
                            values.append(value)
                        data.append(values)
                        anchor = next(cells[page.title_column].all('a'), None)
                        row_targets.append(self.page_link(page, anchor.attrs.get('href', '')) if anchor else None)
                    if len(headers)>31 or len(data)>2000:
                        invalid('База HTML превышает лимит колонок/записей.')
            if not headers:
                invalid('База Notion не содержит заголовков колонок.')
            for index, name in enumerate(headers):
                if index == page.title_column:
                    continue
                if not name.strip() or len(name) > 80:
                    invalid('Название свойства базы пустое или длиннее 80 символов.')
                kind = types[index] if index < len(types) else ''
                typ = {'typesNumber': 'number', 'typesCheckbox': 'checkbox', 'typesSelect': 'select', 'typesMultipleSelect': 'multi_select', 'typesUrl': 'url'}.get(kind, 'text')
                values = [row[index] for row in data if row[index]]
                if typ == 'number':
                    try:
                        for value in values:
                            if not -1e15 <= float(value.replace(',', '')) <= 1e15:
                                raise ValueError()
                    except ValueError:
                        typ = 'text'
                        self.warnings.add('Числовое свойство с неподдерживаемым оформлением сохранено как текст без изменения исходных значений.')
                        self.issues.append({'page':page.title, 'page_id':page.identity, 'kind':'property', 'source':name, 'reason':'property_as_text'})
                elif kind and kind not in ('typesText', 'typesNumber', 'typesCheckbox', 'typesSelect', 'typesMultipleSelect', 'typesUrl'):
                    self.warnings.add('Некоторые типы свойств Notion сохранены как текст: экспорт не содержит их правил вычисления или формат не поддерживается.')
                    self.issues.append({'page':page.title, 'page_id':page.identity, 'kind':'property', 'source':name, 'reason':'property_as_text'})
                if not kind and values and all(value in ('Yes', 'No') for value in values):
                    typ = 'checkbox'
                prop = {'id': 'col_'+str(index), 'name': name, 'type': typ}
                if typ in ('select', 'multi_select'):
                    options = list(dict.fromkeys(part.strip() for value in values for part in (value.split(',') if typ == 'multi_select' else [value]) if part.strip()))
                    if len(options) > 50 or any(len(option) > 80 for option in options):
                        typ = prop['type'] = 'text'
                        self.warnings.add('Некоторые колонки сохранены как текст: значения превышают лимит вариантов.')
                    else:
                        prop['options'] = options
                page.properties.append(prop)
            page.rows = data
            children = [child for child in self.pages.values() if child.parent is page]
            if not table and data and children:
                titles = {norm(child.title) for child in children}
                scores = [sum(norm(row[i]) in titles for row in data) for i in range(len(headers))]
                inferred = max(range(len(scores)), key=scores.__getitem__)
                if inferred != page.title_column:
                    old = page.title_column
                    page.title_column = inferred
                    page.properties = [p for p in page.properties if p['id'] != 'col_'+str(inferred)]
                    page.properties.insert(0, {'id': 'col_'+str(old), 'name': headers[old], 'type': 'text'})
            by_title = defaultdict(list)
            for child in children:
                by_title[norm(child.title)].append(child)
            ordered = []
            if table:
                for row in table.all('tr'):
                    anchor = next(row.all('a'), None)
                    target = anchor and self.page_link(page, anchor.attrs.get('href', ''))
                    if target:
                        ordered.append(target)
            used = set()
            for row_index, row in enumerate(data):
                title = row[page.title_column]
                linked = row_targets[row_index] if row_targets else None
                match = linked if linked and linked.parent is page and linked.identity not in used else next((child for child in by_title[norm(title)] if child.identity not in used), None)
                if match is None and not title.strip():
                    # Empty database titles are exported as Untitled page files.
                    unnamed = [child for child in children if child.title in ('Untitled', 'Без названия') and child.identity not in used]
                    if len(unnamed) == 1:
                        match = unnamed[0]
                if match is None:
                    match = Page(page.source+'/record-'+str(len(used)), '', title or 'Без названия', 'md', parent=page)
                    self.pages[match.source] = match
                used.add(match.identity)
                if all(child is not match for child in ordered):
                    ordered.append(match)
                for prop in page.properties:
                    index = int(prop['id'][4:])
                    raw = row[index]
                    value = raw
                    if prop['type'] == 'checkbox':
                        value = raw.lower() in ('yes', 'true', '1', 'checked')
                    elif prop['type'] == 'number':
                        try:
                            value = float(raw.replace(',', '')) if raw else None
                            if value is not None and (not -1e15 <= value <= 1e15):
                                raise ValueError()
                        except ValueError:
                            invalid('Число в базе Notion не удалось перенести без потери значения.')
                    elif prop['type'] == 'multi_select':
                        value = [part.strip() for part in raw.split(',') if part.strip()]
                    if isinstance(value, str) and len(value) > 2000:
                        invalid('Значение свойства базы длиннее 2000 символов.')
                    match.values[prop['id']] = value
            page.links.extend(child.identity for child in ordered)

    def _documents(self):
        for page in list(self.pages.values()):
            if page.tree:
                header = next(page.tree.all('header'), Node())
                icon = next(header.all(cls='page-header-icon'), None)
                if icon:
                    image = icon if icon.tag == 'img' else next(icon.all('img'), None)
                    if image:
                        page.decorations['icon'] = image.attrs.get('src', '')
                    else:
                        page.icon = icon.text().strip()[:100]
                if not page.icon and 'icon' not in page.decorations:
                    exported_icon = page.tree.attrs.get('data-notion-page-icon', '')
                    if exported_icon and len(exported_icon) <= 100 and not exported_icon.startswith(('http:', 'https:', 'data:')):
                        page.icon = exported_icon
                cover = next(header.all(cls='page-cover-image'), None)
                if cover:
                    page.decorations['cover'] = cover.attrs.get('src', '')
                body = next(page.tree.all(cls='page-body'), Node())
                page.blocks = self.html_blocks(page, body.children)
                description = next(header.all(cls='page-description'), None)
                if description and description.text().strip():
                    page.blocks.insert(0, block(content=self.inline(page, description.children)))
                # CSV views can omit a record that still has its own HTML page.
                # Recover its exported properties; retain any unmapped metadata.
                props = next(page.tree.all('table', 'properties'), None)
                if props:
                    unmapped = []
                    mapping = {p['name']: p for p in page.parent.properties} if page.parent and page.parent.kind == 'database' else {}
                    for row in props.all('tr'):
                        label = next(row.all('th'), Node()).text().strip()
                        cell = next(row.all('td'), Node())
                        prop = mapping.get(label)
                        if prop and prop['id'] in page.values:
                            continue
                        value = cell.text().strip()
                        if prop:
                            try:
                                if prop['type'] == 'checkbox':
                                    value = any(item.has('checkbox-on') for item in cell.all())
                                elif prop['type'] == 'number':
                                    value = float(value.replace(',', '')) if value else None
                                elif prop['type'] == 'multi_select':
                                    value = [item.text() for item in cell.all(cls='selected-value')]
                                elif prop['type'] == 'url':
                                    value = next((item.attrs['href'] for item in cell.all('a') if item.attrs.get('href', '').startswith(('https://', 'http://'))), value)
                                validate_values({prop['id']: value}, [prop], lambda status, message: invalid(message))
                                page.values[prop['id']] = value
                                continue
                            except (ValueError, TypeError):
                                self.warnings.add('Часть метаданных записи сохранена таблицей в её содержимом.')
                        unmapped.append(row)
                    if unmapped:
                        page.blocks = self.html_blocks(page, [Node('table', {}, [Node('tbody', {}, unmapped)])])+page.blocks
            elif page.kind != 'database':
                page.body = self.markdown_links(page, page.body)
                if type(self) is NotionArchive:
                    self.warnings.add('Markdown-экспорт Notion может не содержать иконку, обложку и признаки раскрывающихся блоков. Для точного переноса выберите HTML-экспорт; можно загрузить оба формата вместе. Заголовки не превращаются в раскрывающиеся блоки автоматически.')
            for kind, value in page.decorations.items():
                local = self.local(page, value)
                if local:
                    self.link(page, value, True)
                elif safe_url(value):
                    self.remote_images.add(value)
            if len(page.body) > 200000:
                invalid('Страница Markdown длиннее 200 000 символов.')
        # Include every attachment, including files not linked by the export.
        for name, (_, info) in self.files.items():
            if name in self.aliases or name in self.file_aliases:
                continue
            if info.file_size > 50*1024**2:
                invalid('Вложение превышает лимит заметок 50 Мб.')
            if len(PurePosixPath(name).name) > 255:
                invalid('Имя вложенного файла длиннее 255 символов.')
            owner = self.folder_owner(name) or next(iter(self.pages.values()))
            item = self.attachments.setdefault(name, {'id': secrets.token_urlsafe(12), 'page': owner})
            if name not in self.references:
                url = '/api/notes/'+item['page'].identity+'/files/'+item['id']
                if item['page'].blocks is not None:
                    item['page'].blocks.append(block('file', props={'url': url, 'name': PurePosixPath(name).name}))
                else:
                    item['page'].body += '\n\n['+PurePosixPath(name).name+']('+url+')'
        for page in self.pages.values():
            validate_blocks(_absolute(page.blocks, 'https://notion-import.invalid'), lambda status, message: invalid(message))
            if page.kind == 'database':
                validate_config(page.properties, views(), lambda status, message: invalid(message))
            if page.parent and page.parent.kind == 'database':
                validate_values(page.values, page.parent.properties, lambda status, message: invalid(message))
        if self.missing:
            self.warnings.add(f'В экспорте отсутствуют цели {len(self.missing)} локальных ссылок. Их подписи сохранены.')
            for uuid, value in sorted(self.missing):
                page = self.by_uuid.get(uuid)
                self.issues.append({'page':page.title if page else '', 'page_id':page.identity if page else '', 'kind':'link', 'source':value, 'reason':'missing_from_archive'})
        if self.remote_images:
            self.warnings.add('Часть изображений и оформления находится по внешним адресам: Notion не включил их в ZIP. Доступные изображения, иконки и обложки загружаются отдельно при импорте. Недоступные источники будут перечислены в отчёте.')

    def _order(self):
        children = defaultdict(list)
        for page in self.pages.values():
            children[page.parent.identity if page.parent else None].append(page)
        root_sequences = defaultdict(list)
        for name, (owner, _) in self.files.items():
            page = self.aliases.get(name)
            if page and page.parent is None:
                sequence = root_sequences[id(owner)]
                if page.identity not in sequence:
                    sequence.append(page.identity)
        roots = []
        for sequence in root_sequences.values():
            for index, identity in enumerate(sequence):
                if identity not in roots:
                    following = next((value for value in sequence[index+1:] if value in roots), None)
                    roots.insert(roots.index(following) if following else len(roots), identity)
        for siblings in children.values():
            parent = siblings[0].parent
            links = parent.links if parent else roots
            siblings.sort(key=lambda page: links.index(page.identity) if page.identity in links else len(links))
            for index, page in enumerate(siblings):
                page.position = index

    def markdown_links(self, page, body, replacements=None):
        return rewrite_markdown_links(body,lambda url,image:replacements.get(url,url) if replacements is not None else self.link(page,url,image))

    def summary(self):
        return {'notes': len(self.pages), 'databases': sum(page.kind == 'database' for page in self.pages.values()),
                'files': len(self.attachments)+len(self.downloaded_files), 'file_bytes': sum(self.files[name][1].file_size for name in self.attachments)+sum(item['size'] for item in self.downloaded_files),
                'remote_images': len(self.document_images), 'downloaded_images': len(self.image_downloads), 'unavailable_images': self.image_failures,
                'roots': [page.title for page in sorted((p for p in self.pages.values() if p.parent is None), key=lambda p: p.position)],
                'issues': self.issues,
                'page_images': {kind:{'expected':sum(kind in p.decorations or kind=='icon' and bool(p.icon) for p in self.pages.values()),
                                      'saved':sum(kind in p.saved_assets or kind=='icon' and bool(p.icon) for p in self.pages.values())} for kind in ('icon','cover')},
                'warnings': sorted(self.warnings), 'missing_links': len(self.missing), 'sha256': self.digest.hexdigest()}


def views():
    return [{'id': 'table', 'name': 'Таблица', 'type': 'table', 'visible': []}]


class ArchiveSlice:
    """A seekable view of one ZIP in a streamed multi-file upload."""
    def __init__(self, source, offset, size):
        self.source, self.offset, self.size, self.position = source, offset, size, 0

    def seek(self, offset, whence=0):
        value = offset if whence == 0 else self.position+offset if whence == 1 else self.size+offset
        if value < 0:
            raise OSError('Invalid ZIP offset.')
        self.position = value
        return value

    def tell(self):
        return self.position

    def seekable(self):
        return True

    def read(self, size=-1):
        count = max(0, self.size-self.position)
        if size >= 0:
            count = min(count, size)
        self.source.seek(self.offset+self.position)
        body = self.source.read(count)
        self.position += len(body)
        return body


def _absolute(value, public_url):
    if isinstance(value, list):
        return [_absolute(item, public_url) for item in value]
    if isinstance(value, dict):
        return {key: public_url.rstrip('/')+item if key in ('url', 'href') and isinstance(item, str) and item.startswith(('/api/notes/', '/page/'))
                else _absolute(item, public_url) for key, item in value.items()}
    return value


def restore(archive, db, store, uid, now, public_url, decorations=None, downloaded=None, receipt=None):
    """Commit the complete tree atomically; abort immutable objects on any failure."""
    writers, images = [], []
    from app.notion_images import stage_images, insert_images, replace_images, document_urls
    existing_images = {}
    from app.data_crypto import media_store
    from app.modules.note_assets import thumbnail
    media = media_store(db, store.root.parent)
    try:
        for name, item in archive.attachments.items():
            identity = secrets.token_hex(16)
            writer = store.writer(identity)
            writers.append(writer)
            owner, info = archive.files[name]
            with owner.open(info) as stream:
                while chunk := stream.read(1024**2):
                    writer.write(chunk)
            item['size'], digest = writer.finish()
            item['object_id'] = identity
            if (archive.mime_type(name) if hasattr(archive,'mime_type') else mimetypes.guess_type(name)[0]) in ('image/png','image/jpeg','image/webp','image/gif'):existing_images.setdefault(digest,item)
        owners = {}
        for page in archive.pages.values():
            urls = document_urls(page.blocks,page.body)
            for url in (downloaded or {}).keys() & urls:owners.setdefault(url,page)
        links,archive.downloaded_files = stage_images(downloaded or {},owners,store,writers,existing_images)
        archive.image_downloads = {url:True for url in links}
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
                invalid('Аккаунт больше недоступен.')
            base = c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM notes WHERE user_id=? AND space_id IS NULL AND parent_id IS NULL', (uid,)).fetchone()[0]
            def depth(page):
                count = 0
                while page.parent:
                    count += 1
                    page = page.parent
                return count
            for page in sorted(archive.pages.values(), key=depth):
                blocks = _absolute(replace_images(page.blocks,links), public_url)
                body = archive.markdown_links(page,page.body,replacements=links)
                if blocks is not None:
                    # Search/export projection; rich content remains canonical.
                    body = '\n\n'.join(_block_text(item) for item in blocks)
                else:
                    # Fence contents are source code, never imported link targets.
                    pattern = r'(^```[^\n]*\n[\s\S]*?^```[^\n]*$|`[^`\n]+`)|\]\((/(?:api/notes|page)/[^)]+)\)'
                    body = re.sub(pattern, lambda m: m[0] if m[1] else ']('+public_url.rstrip('/')+m[2]+')', body, flags=re.M)
                c.execute('INSERT INTO notes(id,user_id,title,body,blocks,parent_id,position,icon,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (page.identity, uid, page.title, body, json.dumps(blocks, ensure_ascii=False) if blocks is not None else None,
                           page.parent.identity if page.parent else None, page.position+(base if page.parent is None else 0), page.icon, now(), now()))
            for page in archive.pages.values():
                if page.kind == 'database':
                    validate_config(page.properties, views(), lambda status, message: invalid(message))
                    c.execute('INSERT INTO note_databases(note_id,properties,views) VALUES(?,?,?)', (page.identity, json.dumps(page.properties, ensure_ascii=False), json.dumps(views())))
            for page in archive.pages.values():
                if page.parent and page.parent.kind == 'database':
                    validate_values(page.values, page.parent.properties, lambda status, message: invalid(message))
                    c.execute('INSERT INTO database_records(note_id,database_id,properties) VALUES(?,?,?)', (page.identity, page.parent.identity, json.dumps(page.values, ensure_ascii=False)))
            folders = set()
            for name, item in archive.attachments.items():
                page = item['page']
                if page.identity not in folders:
                    c.execute('INSERT INTO files(id,user_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?)',
                              (page.identity, uid, 'folder', 'Вложения — '+page.title[:200], 'application/x-drg-note;space=private', now(), now()))
                    folders.add(page.identity)
                c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (item['id'], uid, page.identity, 'file', item.get('name',PurePosixPath(name).name), (archive.mime_type(name) if hasattr(archive,'mime_type') else mimetypes.guess_type(name)[0]) or 'application/octet-stream', item['size'], item['object_id'], now(), now()))
            insert_images(c,archive.downloaded_files,uid,now)
            for page in archive.pages.values():
                for kind, value in page.decorations.items():
                    local = archive.local(page, value)
                    data = archive.read(local) if local else (decorations or {}).get(value)
                    if data is None and value.startswith('data:'):
                        from app.notion_images import embedded_image
                        try:
                            data = embedded_image(value)
                        except ValueError:
                            archive.issues.append({'page':page.title, 'page_id':page.identity, 'kind':kind, 'source':'data:image', 'reason':'unsupported_image'})
                            continue
                    if data is None:
                        archive.issues.append({'page':page.title, 'page_id':page.identity, 'kind':kind, 'source':value, 'reason':'unavailable_image'})
                        continue
                    try:
                        if kind == 'icon' and (urlsplit(value).path.lower().endswith('.svg') or b'<svg' in data[:512]):
                            from app.notion_svg import icon_png
                            data = icon_png(data)
                        data, width, height = thumbnail(data, kind)
                    except (ValueError, OSError):
                        archive.warnings.add('Некоторые иконки/обложки не поддерживаются; исходные файлы сохранены во вложениях, если они есть в ZIP.')
                        archive.issues.append({'page':page.title, 'page_id':page.identity, 'kind':kind, 'source':value, 'reason':'unsupported_image'})
                        continue
                    aid = secrets.token_hex(16)
                    images.append(aid)
                    media.write(aid, data)
                    c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)', (aid, uid, 'image/webp', len(data), width, height, 1, now()))
                    c.execute('INSERT INTO note_assets VALUES(?,?,?,?,?)', (aid, page.identity, kind, '{}', now()))
                    c.execute('UPDATE notes SET '+kind+'=? WHERE id=?', ('image:'+aid, page.identity))
                    page.saved_assets.add(kind)
            if receipt:
                from app.import_receipts import committed
                committed(c, uid, receipt['source'], receipt['operation'], archive.digest.hexdigest(), archive.summary(), now())
    except BaseException:
        for writer in writers:
            with suppress(Exception):
                writer.abort()
        for aid in images:
            with suppress(Exception):
                media.delete(aid)
        raise
    return archive.summary()


def _block_text(item):
    def text(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return ''.join(text(part) for part in value)
        if isinstance(value, dict):
            if value.get('type') == 'link':
                return '['+text(value.get('content', []))+']('+value['href']+')'
            if value.get('type') == 'tableContent':
                return '\n'.join(' | '.join(text(cell) for cell in row['cells']) for row in value['rows'])
            return value.get('text', '')
        return ''
    result = text(item.get('content', []))
    props = item.get('props', {})
    if item['type'] in ('image', 'file', 'audio', 'video'):
        result = '['+(props.get('name') or props.get('caption') or item['type'])+']('+props['url']+')'
    if item['type'] == 'pageLink':
        result = props.get('title', '')
    return result+'\n'+'\n'.join(_block_text(child) for child in item.get('children', []))


def rewrite_markdown_links(body, resolve):
    output, offset = [], 0
    # Balanced destinations are common in Notion page names. Never rewrite code.
    tokens = re.compile(r'(^```[^\n]*\n[\s\S]*?^```[^\n]*$|`[^`\n]+`|!?\[([^\]\n]*)\]\()', re.M)
    for match in tokens.finditer(body):
        if match.start() < offset:
            continue
        output.append(body[offset:match.start()])
        if match[2] is None:
            output.append(match[0]); offset = match.end(); continue
        end, depth = match.end(), 1
        while end < len(body) and depth:
            if body[end] == '\\':
                end += 2; continue
            if body[end] == '(':
                depth += 1
            elif body[end] == ')':
                depth -= 1
            end += 1
        if depth:
            output.append(match[0]); offset = match.end(); continue
        raw = body[match.end():end-1]
        title = re.search(r'\s+"[^"\n]*"$', raw)
        destination = raw[:title.start()] if title else raw
        url = resolve(destination.strip('<>'), match[0].startswith('!'))
        output.append(match[0]+url+(title[0] if title else '')+')' if url else match[2])
        offset = end
    return ''.join(output)+body[offset:]
