"""Apply explicit publication edits to the canonical document without resetting its epoch."""
from app.i18n import message as localized_message
import secrets
from pycrdt import XmlElement, XmlText, XmlFragment, Map
from app.collaboration import project, MAX_STATE
from app.modules.notes import validate_blocks


def replace_content(doc, title, blocks, fail):
    validate_blocks(blocks, fail)
    if not blocks: fail(422, localized_message('addAtLeastOnePageBlock'))
    formats = []

    def inline(content, inherited=None, depth=0):
        if depth > 35: fail(422, localized_message('tooMuchTextNesting'))
        if isinstance(content, str): content = [{'type': 'text', 'text': content}]
        if not isinstance(content, list): fail(422, localized_message('invalidBlockText'))
        out = []
        for part in content:
            if not isinstance(part, dict): fail(422, localized_message('invalidBlockText'))
            if part.get('type')=='equation':
                out.append(XmlElement('equation',{'latex':part['props']['latex']},[]));continue
            if part.get('type') == 'link':
                out += inline(part.get('content', []), {**(inherited or {}), 'link': {'href': part.get('href', '')}}, depth+1)
                continue
            if part.get('type') != 'text' or not isinstance(part.get('text'), str): fail(422, localized_message('invalidBlockText'))
            text = XmlText(part['text']); marks = dict(inherited or {})
            styles = part.get('styles', {})
            if not isinstance(styles, dict): fail(422, localized_message('incorrectFormatting'))
            for name in ('bold', 'italic', 'underline', 'strike', 'code', 'spoiler', 'textColor', 'backgroundColor'):
                if styles.get(name): marks[name] = styles[name]
            if marks: formats.append((text, marks))
            out.append(text)
        return out

    def table(content):
        if not isinstance(content, dict) or not isinstance(content.get('rows'), list): fail(422, localized_message('invalidTable'))
        out = []
        for index, row in enumerate(content['rows']):
            if not isinstance(row, dict) or not isinstance(row.get('cells'), list): fail(422, localized_message('invalidTableRow'))
            cells = []
            for col, cell in enumerate(row['cells']):
                rich = isinstance(cell, dict)
                attrs = dict(cell.get('props', {})) if rich else {}
                widths = content.get('columnWidths', [])
                if index == 0 and isinstance(widths, list) and col < len(widths) and widths[col] is not None: attrs['colwidth'] = [widths[col]]
                is_header = index < (content.get('headerRows') or 0) or col < (content.get('headerCols') or 0)
                cells.append(XmlElement('tableHeader' if is_header else 'tableCell', attrs, [XmlElement('tableParagraph', {}, inline(cell.get('content', []) if rich else cell))]))
            out.append(XmlElement('tableRow', {}, cells))
        return out

    def group(items):
        out = []
        for b in items:
            kind = b['type']; props = dict(b.get('props', {}))
            container = {'id': b.get('id') or secrets.token_urlsafe(12)}
            for color in ('textColor', 'backgroundColor'):
                if color in props: container[color] = props.pop(color)
            content = table(b.get('content')) if kind == 'table' else [] if kind in ('image', 'video', 'audio', 'file', 'divider', 'pageLink') else inline(b.get('content', []))
            children = [XmlElement(kind, props, content)]
            if b.get('children'): children.append(group(b['children']))
            out.append(XmlElement('blockContainer', container, children))
        return XmlElement('blockGroup', {}, out)

    root = group(blocks)
    with doc.transaction():
        fragment = doc.get('prosemirror', type=XmlFragment)
        del fragment.children[:]
        fragment.children.append(root)
        for text, marks in formats:
            if len(text): text.format(0, len(text), marks)
        doc.get('meta', type=Map)['title'] = title
    if len(doc.get_update()) > MAX_STATE: fail(413, localized_message('theSharedDocumentIsTooLarge'))
    return project(doc, fail)
