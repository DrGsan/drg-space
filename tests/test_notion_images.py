import asyncio
from tests.test_security import reset
from tests.test_widgets import image_bytes
from app import notion_images
from app.notion_images import download_images


def test_invalid_image_and_download_budget_leave_sources_in_failure_report(monkeypatch):
    async def fetch(url,**kwargs):return (b'<html>not image</html>' if url.endswith('bad') else image_bytes()),url,'image/png'
    monkeypatch.setattr(notion_images,'fetch_public',fetch)
    data,failed=asyncio.run(download_images(['https://images.example/good','https://images.example/bad']))
    assert list(data)==['https://images.example/good'] and failed==['https://images.example/bad']
    monkeypatch.setattr(notion_images,'MAX_TOTAL',1)
    data,failed=asyncio.run(download_images(['https://images.example/good']))
    assert not data and failed==['https://images.example/good']


def test_malformed_remote_port_does_not_abort_other_downloads(monkeypatch):
    async def unavailable(*args,**kwargs):raise ValueError('Invalid URL')
    monkeypatch.setattr(notion_images,'fetch_public',unavailable)
    url='https://avatars.mds.yandex.net:bad/get-zen_doc/image'
    data,failed=asyncio.run(download_images([url]));assert data=={} and failed==[url]
    assert notion_images.candidates('https://avatars.mds.yandex.net.evil.example/get-zen_doc/image')==['https://avatars.mds.yandex.net.evil.example/get-zen_doc/image']


def test_games_image_moved_to_vkplay_keeps_original_identity(monkeypatch):
    original='https://games.mail.ru/hotbox/content_files/article/2018/3/image.jpeg'
    moved='https://media.vkplay.ru/hotbox/content_files/article/2018/3/image.jpeg'
    body=image_bytes()
    async def fetch(url,**kwargs):
        if url==moved:return body,url,'image/png'
        raise OSError('Legacy CDN unavailable')
    monkeypatch.setattr(notion_images,'fetch_public',fetch)
    data,failed=asyncio.run(download_images([original]))
    assert not failed and list(data)==[original] and data[original].body==body
    assert notion_images.candidates('https://games.mail.ru.evil.example/hotbox/content_files/image.jpeg')==['https://games.mail.ru.evil.example/hotbox/content_files/image.jpeg']


def test_current_cdn_priority_is_opt_in_and_preserves_original_export_identity():
    original='https://games.mail.ru/hotbox/content_files/image.jpeg?version=1'
    current='https://media.vkplay.ru/hotbox/content_files/image.jpeg?version=1'
    assert notion_images.candidates(original)==[original,current]
    assert notion_images.candidates(original,prefer_current=True)==[current,original]
    signed='https://files.example/a.png?X-Amz-Signature=keep'
    assert notion_images.candidates(signed,prefer_current=True)==[signed]


def test_flaticon_move_keeps_size_asset_identity_and_query_and_is_host_path_scoped():
    original='https://image.flaticon.com/icons/png/512/25/25231.png?version=1'
    current='https://cdn-icons-png.flaticon.com/512/25/25231.png?version=1'
    assert notion_images.candidates(original)==[original,current]
    assert notion_images.candidates(original,prefer_current=True)==[current,original]
    for url in (
        'https://image.flaticon.com.evil.example/icons/png/512/25/25231.png',
        'https://user@image.flaticon.com/icons/png/512/25/25231.png',
        'https://image.flaticon.com:8443/icons/png/512/25/25231.png',
        'https://image.flaticon.com/icons/png/512/../25231.png',
        'https://image.flaticon.com/icons/png/512/25/25231.svg',
        'https://image.flaticon.com:bad/icons/png/512/25/25231.png',
        'ftp://image.flaticon.com/icons/png/512/25/25231.png',
    ):
        assert notion_images.candidates(url,prefer_current=True)==[url]
