"""Small project artwork lives inside the encrypted, quota-accounted payload."""
import base64
import hashlib
import io
from PIL import Image
from app.modules.profile import thumbnail


def normalize(value):
    if not value: return ''
    try:
        body=base64.b64decode(value,validate=True)
        if len(body)>1024*1024:raise ValueError()
        with Image.open(io.BytesIO(body)) as image:
            if image.width>1024 or image.height>1024:raise ValueError()
            canonical=image.format=='WEBP' and image.size==(256,256) and image.n_frames==1
            if canonical:image.verify()
        if not canonical:body=thumbnail(body)
        if len(body)>256*1024:raise ValueError()
        return base64.b64encode(body).decode('ascii')
    except (ValueError,OSError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        from app.i18n import message
        raise ValueError(message('youNeedPngJpegWebpOrAGif')) from None


def version(value):return hashlib.sha256(value.encode()).hexdigest()[:16] if value else ''


def metadata(payload):
    result=dict(payload);body=result.pop('avatar','')
    return result | {'avatar_version':version(body),'has_avatar':bool(body)}
