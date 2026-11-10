"""Bounded thumbnails with no disk cache, shared by private file namespaces."""
from app.i18n import message as localized_message
import io
from PIL import Image,ImageOps,UnidentifiedImageError


def thumbnail(body,fail):
    try:
        with Image.open(io.BytesIO(body)) as image:
            if image.width*image.height>40_000_000:fail(415,localized_message('theImageIsTooLargeForAThumbnail'))
            image.draft('RGB',(256,256))
            image=ImageOps.exif_transpose(image)
            image.thumbnail((256,256))
            output=io.BytesIO();image.convert('RGBA').save(output,format='WEBP',quality=80)
    except (UnidentifiedImageError,Image.DecompressionBombError,OSError,ValueError):fail(415,localized_message('theImageFormatDoesNotSupportThumbnails'))
    return output.getvalue()
