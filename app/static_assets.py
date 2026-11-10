"""HTTP cache for packaged, public build resources only."""
import hashlib
import json
import logging
import mimetypes
from functools import lru_cache
from pathlib import Path

from werkzeug.http import parse_etags
from werkzeug.security import safe_join

from app.http import FileResponse, Response, StreamingResponse


@lru_cache(maxsize=512)
def _digest(path, modified, size):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


class StaticAssets:
    def __init__(self, static, *, cache=None):
        self.root = Path(static) / 'assets'
        manifest = Path(static) / 'asset-manifest.json'
        values = json.loads(manifest.read_text()) if manifest.is_file() else {}
        self.aliases = values.get('aliases', {})
        self.immutable = set(values.get('immutable', []))
        self.cache = None
        if cache is not None:
            from app.public_builds import PublicBuilds
            try:
                self.cache = PublicBuilds(cache)
                self.cache.publish(static)
            except (OSError, ValueError):
                # A full/read-only cache cannot take down the current app.
                logging.getLogger('drg.static').error('Public build retention unavailable; current packaged assets remain available.')

    def prune(self):
        if self.cache is not None:
            self.cache.prune()

    def response(self, asset, request):
        name = self.aliases.get(asset, asset)
        target = safe_join(str(self.root), name)
        if not target or not Path(target).is_file() or not Path(target).resolve().is_relative_to(self.root.resolve()):
            return self.previous(asset, request)
        path = Path(target)
        stat = path.stat()
        digest = _digest(str(path), stat.st_mtime_ns, stat.st_size)
        headers = {'ETag': '"' + digest + '"', 'Cache-Control': 'public, max-age=31536000, immutable' if asset in self.immutable else 'public, max-age=0, must-revalidate'}
        response = (Response(status_code=304, headers=headers)
                    if parse_etags(request.headers.get('if-none-match', '')).contains_weak(digest)
                    else FileResponse(path, headers=headers))
        # The security boundary accepts this marker only from the packaged-file
        # route. Private files, HTML, API and all error responses keep no-store.
        response.public_static_asset = True
        return response

    def previous(self, asset, request):
        # Only exact immutable names recorded by a trusted build manifest.
        # Mutable aliases, HTML, API and private files never use this path.
        if self.cache is None or asset in self.aliases:
            return None
        try:
            found = self.cache.open(asset)
        except (OSError, ValueError):
            return None
        if found is None:
            return None
        file, record = found
        headers = {'ETag': '"' + record['digest'] + '"', 'Cache-Control': 'public, max-age=31536000, immutable'}
        if parse_etags(request.headers.get('if-none-match', '')).contains_weak(record['digest']):
            file.close()
            response = Response(status_code=304, headers=headers)
        else:
            def chunks():
                try:
                    while chunk := file.read(1024 * 1024):
                        yield chunk
                finally:
                    file.close()
            headers['Content-Length'] = str(record['size'])
            response = StreamingResponse(chunks(), headers=headers, media_type=mimetypes.guess_type(asset)[0] or 'application/octet-stream', background=file.close)
        response.public_static_asset = True
        return response
