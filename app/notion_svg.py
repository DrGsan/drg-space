"""Rasterize a bounded, self-contained static icon; never resolve SVG resources."""
import re
from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

TAGS = {'svg','g','path','rect','circle','ellipse','line','polyline','polygon','defs',
        'linearGradient','radialGradient','stop','clipPath','mask','symbol','use','title','desc','style'}

def icon_png(data):
    if len(data) > 1024**2:
        raise ValueError('SVG icon too large')
    try:
        root = fromstring(data)
    except (DefusedXmlException, ParseError) as error:
        raise ValueError('Invalid SVG icon') from error
    if root.tag.rsplit('}',1)[-1] != 'svg':
        raise ValueError('Not an SVG icon')
    nodes = list(root.iter())
    if len(nodes)>10000:
        raise ValueError('SVG icon too complex')
    for node in nodes:
        tag = node.tag.rsplit('}',1)[-1]
        if tag not in TAGS:
            raise ValueError('Unsupported SVG element')
        for key, value in node.attrib.items():
            key = key.rsplit('}',1)[-1].lower()
            if key.startswith('on') or key in ('href','src') and not value.startswith('#'):
                raise ValueError('SVG external resource')
            check_css(value)
        if tag=='style':
            check_css(node.text or '')
    from resvg_py import svg_to_bytes
    # svg_string never supplies a file base; external references were rejected.
    return svg_to_bytes(svg_string=data.decode('utf-8-sig'),width=192,height=192,skip_system_fonts=True)

def check_css(value):
    if '@' in value or '\\' in value or re.search(r'url\s*\((?!\s*[\'"]?#)',value,re.I):
        raise ValueError('SVG external style resource')
