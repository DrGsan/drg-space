"""Public failure categories, never provider bodies or signed URLs."""
import socket
import httpx
from app.notion_sync.client import NotionError


class AssetError(NotionError):
    def __init__(self,reason='notionAssetUnavailable',delay=30,**params):
        # Attachment HTTP status must never be mistaken for lost Notion access.
        super().__init__('notionAssetUnavailable',502,delay,reason=reason,**params)


def classify(error):
    if isinstance(error,httpx.HTTPStatusError):
        status=error.response.status_code
        if status in (404,410):return AssetError('notionAssetMissing')
        if status in (401,403):return AssetError('notionAssetDenied')
        if status in (429,503):
            try:delay=max(30,min(86400,int(error.response.headers.get('Retry-After','60'))))
            except ValueError:delay=60
            return AssetError('notionAssetBusy',delay)
    if isinstance(error,(httpx.TimeoutException,TimeoutError)):return AssetError('notionAssetTimeout')
    cause=error
    for _ in range(8):
        if isinstance(cause,socket.gaierror):return AssetError('notionAssetDns')
        cause=getattr(cause,'__cause__',None)
        if cause is None:break
    return AssetError()
