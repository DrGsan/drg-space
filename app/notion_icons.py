"""Convert bounded Notion SVG/ICO decorations to a regular raster image."""
import io
import struct

from PIL import Image
from PIL.IcoImagePlugin import IcoFile


def raster_icon(body):
    if b'<svg' in body[:512]:
        from app.notion_svg import icon_png
        return icon_png(body)
    if not body.startswith(b'\x00\x00\x01\x00'):return body
    # ICO's Pillow decoder loads its selected frame while opening. Validate
    # every embedded frame's dimensions before invoking that decoder.
    if len(body)<6 or len(body)>1024**2:raise ValueError('ICO icon too large')
    count=struct.unpack_from('<H',body,4)[0]
    if not 1<=count<=64 or len(body)<6+16*count:raise ValueError('Invalid ICO directory')
    icon=IcoFile(io.BytesIO(body))
    for entry in icon.entry:
        if entry.offset<6+16*count or entry.size<=0 or entry.offset+entry.size>len(body):raise ValueError('Invalid ICO frame')
        frame=body[entry.offset:entry.offset+entry.size]
        if frame.startswith(b'\x89PNG\r\n\x1a\n'):
            with Image.open(io.BytesIO(frame)) as image:
                if image.format!='PNG' or not 0<image.width<=256 or not 0<image.height<=256:raise ValueError('ICO frame too large')
        else:
            if len(frame)<40:raise ValueError('Invalid ICO bitmap')
            header,width,height=struct.unpack_from('<Iii',frame)
            if header<40 or header>len(frame) or not 0<width<=256 or not 0<height<=512:raise ValueError('ICO bitmap too large')
    with Image.open(io.BytesIO(body)) as source:
        image=source.convert('RGBA');stream=io.BytesIO();image.save(stream,format='PNG');image.close()
        return stream.getvalue()
