"""Authenticated account-scoped transfers, independent of enabled modules."""
from app.i18n import message as localized_message
import asyncio
import base64
from app.http import Router, Request
from app.http import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from app.http import BackgroundTask
from app.backups import BackupError
from app.personal_data import PersonalData, MAX_ARCHIVE, summary
from app.storage import blocking
from app.archive_stream import receive_archive


class Export(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1, max_length=256)
    archive_password: str = Field(min_length=12, max_length=256)


def create_personal_data_router(db, ready, fail, check_password, protection, now, data_dir, public_url, gate,cipher=None):
    router = Router()
    service = PersonalData(db, data_dir, now, public_url,cipher)
    heavy = asyncio.Lock()

    def proof(request, password, rate=True):
        s = ready(request)
        protection.check(request, 'personal-data-password', s['user_id'])
        if rate: protection.rate(request, 'personal-data', 12, subject=s['user_id'])
        with db() as c: user = c.execute('SELECT password FROM users WHERE id=?', (s['user_id'],)).fetchone()
        if not user or not check_password(password, user[0]):
            protection.failure(request, 'personal-data-password', s['user_id'])
            fail(403, localized_message('incorrectAccountPassword'))
        protection.success(request, 'personal-data-password', s['user_id'])
        return s

    def available(request):
        ready(request)
        if heavy.locked() or gate.exclusive_pending:
            fail(423, localized_message('operationWithDataIsAlreadyInProgressRetry'))

    def password_header(request, name, minimum):
        value = request.headers.get(name, '')
        try:
            if len(value) > 2048: raise ValueError()
            password = base64.b64decode(value, validate=True).decode('utf-8')
            if not minimum <= len(password) <= 256: raise ValueError()
            return password
        except (ValueError, UnicodeError):
            fail(422, localized_message('enterTheAccountPasswordAndArchivePasswordAt'))

    @router.post('/api/account/data/export')
    async def export(data: Export, request: Request):
        available(request); proof(request, data.password)
        async with heavy:
            temporary = service.temp()
            try:
                async with gate.exclusive():
                    s = proof(request, data.password, False)
                    content = await blocking(service.snapshot, s['user_id'], temporary.name)
                path = await blocking(service.encrypt, content, data.archive_password, temporary.name)
                return FileResponse(path, media_type='application/octet-stream',
                                    filename='drg-personal-'+str(now())+'.drgpersonal',
                                    background=BackgroundTask(temporary.cleanup))
            except ValueError as error:
                temporary.cleanup()
                fail(422, str(error) if isinstance(error, BackupError) else 'Личные данные не прошли проверку. Экспорт не создан.')
            except BaseException:
                temporary.cleanup(); raise

    async def receive(request, importing):
        available(request)
        account_password = password_header(request, 'x-account-password-b64', 1)
        archive_password = password_header(request, 'x-archive-password-b64', 12)
        proof(request, account_password)
        apply_settings = request.headers.get('x-import-settings', 'true')
        if apply_settings not in ('true', 'false'): fail(422, localized_message('specifyWhetherToTransferSettings'))
        if importing and request.headers.get('x-import-confirmation') != 'IMPORT':
            fail(422, localized_message('confirmDataAdditionAfterArchiveVerification'))
        async with heavy:
            with service.temp() as directory:
                body=await receive_archive(request,directory,MAX_ARCHIVE,fail)
                try:
                    content = await blocking(service.prepare, body, archive_password, directory)
                    del body
                    if not importing:
                        proof(request, account_password, False)
                        return {'summary': summary(content), 'mode': 'add', 'security_imported': False}
                    async with gate.exclusive():
                        s = proof(request, account_password, False)
                        result = await blocking(service.restore, s['user_id'], content, directory, apply_settings == 'true')
                    return {'ok': True, 'imported': result}
                except BackupError as error: fail(422, str(error))

    @router.post('/api/account/data/preview')
    async def preview(request: Request):
        return await receive(request, False)

    @router.post('/api/account/data/import')
    async def import_data(request: Request):
        return await receive(request, True)

    return router
