"""Disposable browser server: real WebRTC, isolated transport availability fixture."""
import os
from app import main
from app.modules import calls
# The ordinary regression suite uses host ICE on localhost. The separate real
# TURN scenario supplies URLs/secret and exercises the authenticated probe.
if not os.getenv('CALL_TURN_URLS'):
    calls.turn_available=lambda:os.getenv('DRG_TEST_LIVEKIT')!='1'
app=main.app

# Deterministic remote download fixture, enabled only by the isolated import test.
if os.getenv('DRG_NOTION_IMAGE_FIXTURE')=='1':
    import io
    from PIL import Image
    from app import notion_images
    buffer=io.BytesIO();Image.new('RGB',(48,32),'#e6ad67').save(buffer,format='PNG')
    fixture_image=buffer.getvalue()
    async def image_fixture(url,**kwargs):
        if url=='https://notion-image.example/original.png':return fixture_image,url,'image/png'
        raise OSError('No remote fixture')
    notion_images.fetch_public=image_fixture
