"""Account-scoped, additive import with an explicit preview digest."""
from app.i18n import message as localized_message
from app.i18n import system_text
import asyncio
import csv
import json
import re
import zipfile
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from contextlib import nullcontext

import httpx
from app.archive_stream import EncryptedSpool
from app.file_store import file_store
from app.notion_images import download_images
from app.http import Request, Router
from app.notion_import import MAX_UPLOAD, NotionArchive, NotionImportError, restore
from app.process_locks import leased_temp, try_file_lock
from app.safe_fetch import fetch_public
from app.storage import blocking


async def decoration_images(archive, downloaded=None):
    semaphore = asyncio.Semaphore(8)
    values = {value for page in archive.pages.values() for value in page.decorations.values()
              if value.startswith(('https://', 'http://'))}
    # Remote decoration is optional. Never fetch arbitrary exported URLs unboundedly.
    if len(values) > 500:
        archive.warnings.add('Внешних иконок/обложек больше 500; они не загружены.')
        return {}
    output = {value: image.body for value, image in (downloaded or {}).items() if value in values}
    values -= output.keys()
    total = 0
    async def fetch(value):
        nonlocal total
        async with semaphore:
            try:
                source=value
                parsed=urlsplit(value)
                if parsed.hostname=='images.unsplash.com':
                    query=dict(parse_qsl(parsed.query));query.update(w='1600',q='85',fm='jpg')
                    source=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(query),''))
                body, _, _ = await fetch_public(source, max_bytes=5*1024**2, accept='image/*')
                if total+len(body) <= 64*1024**2:
                    output[value] = body
                    total += len(body)
                else:
                    archive.warnings.add('Общий размер внешнего оформления превышает 64 Мб; часть изображений пропущена.')
            except (ValueError, OSError, httpx.HTTPError, TimeoutError):
                archive.warnings.add('Некоторые внешние иконки/обложки недоступны и не были загружены.')
    try:
        async with asyncio.timeout(25):
            await asyncio.gather(*(fetch(value) for value in values))
    except TimeoutError:
        archive.warnings.add('Истекло время загрузки внешнего оформления; недоступные иконки/обложки пропущены.')
    return output


def create_notion_import_router(db, ready, rate, now, fail, data_dir, public_url, gate, source="notion"):
    from app.outline_import import OutlineArchive
    archive_type=OutlineArchive if source=="outline" else NotionArchive
    source_title="Outline" if source=="outline" else "Notion"
    router = Router()
    store = file_store(db, data_dir)
    heavy = asyncio.Lock()

    def report(value):
        # Only known system warnings are translated, never page titles/URLs.
        return value | {'warnings': [system_text(warning) for warning in value.get('warnings', [])]}

    async def receive(request, importing):
        session = ready(request)
        rate(request, source+'-import', 12, subject=session['user_id'])
        if heavy.locked() or gate.exclusive_pending:
            fail(423, localized_message('importIsAlreadyInProgressRetryLater'))
        if importing and request.headers.get('x-import-confirmation') != 'IMPORT':
            fail(422, localized_message('firstCheckTheArchiveAndConfirmTheImport'))
        operation = request.headers.get('x-import-operation', '') if importing else ''
        if operation and not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', operation):
            fail(422, localized_message('invalidBlockIdentifier'))
        length = request.headers.get('content-length', '')
        if length.isdigit() and int(length) > MAX_UPLOAD:
            fail(413, localized_message('theZipFileShouldNotExceed512Mb'))
        async with heavy:
            with (try_file_lock(data_dir,'notion-import.lock') or nullcontext()) as lease, leased_temp(data_dir, '.drg-notion-work-') as directory:
                if lease is None:fail(423,localized_message('importIsAlreadyInProgressRetryLater'))
                spool = EncryptedSpool(directory)
                archive = None
                try:
                    async for chunk in request.stream():
                        if spool.size+len(chunk) > MAX_UPLOAD:
                            fail(413, localized_message('theZipFileShouldNotExceed512Mb'))
                        await blocking(spool.write, chunk)
                    await blocking(spool.finish)
                    parts = None
                    if request.headers.get('x-'+source+'-parts'):
                        try:
                            parts = json.loads(request.headers['x-'+source+'-parts'])
                            if not isinstance(parts, list) or not 1 <= len(parts) <= 20 or any(type(n) is not int or n <= 0 for n in parts) or sum(parts) != spool.size:
                                raise ValueError()
                        except (ValueError, TypeError):
                            fail(422, localized_message('invalidExportPartList'))
                    archive = await blocking(archive_type, spool, directory, parts)
                    if not importing:
                        ready(request)
                        return report(archive.summary()) | {'idempotent_import': True}
                    if request.headers.get('x-'+source+'-preview') != archive.digest.hexdigest():
                        fail(409, localized_message('archiveHasBeenModifiedCheckItAgainBefore'))
                    if operation:
                        from app.import_receipts import previous
                        with db() as c:
                            try:
                                saved = previous(c, session['user_id'], source, operation, archive.digest.hexdigest(), now())
                            except ValueError:
                                fail(409, localized_message('archiveHasBeenModifiedCheckItAgainBefore'))
                        if saved is not None:
                            ready(request)
                            return {'ok': True, 'imported': report(saved), 'replayed': True}
                    downloaded, archive.image_failures = await download_images(archive.document_images)
                    images = await decoration_images(archive, downloaded)
                    async with gate.exclusive():
                        session = ready(request)
                        result = await blocking(restore, archive, db, store, session['user_id'], now, public_url, images, downloaded, {'source':source, 'operation':operation} if operation else None)
                    return {'ok': True, 'imported': report(result)}
                except NotionImportError as error:
                    fail(422, str(error))
                except (zipfile.BadZipFile, UnicodeError, csv.Error, RecursionError, EOFError):
                    fail(422, 'Архив '+source_title+' повреждён или имеет неподдерживаемый формат.')
                finally:
                    if archive:
                        archive.close()
                    spool.close()

    @router.post('/api/account/'+source+'/preview')
    async def preview(request: Request):
        return await receive(request, False)

    @router.post('/api/account/'+source+'/import')
    async def import_data(request: Request):
        return await receive(request, True)

    return router
