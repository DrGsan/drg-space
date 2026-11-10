"""Language is an additive account setting, independent of SQL schema."""
from app.i18n import message as localized_message
import json
from pydantic import BaseModel, ConfigDict
from app.http import Router, Request
from app.i18n import LANGUAGES


class LanguagePreference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: str


def create_localization_router(db, ready, fail):
    router = Router()

    @router.put('/api/language')
    def save_language(data: LanguagePreference, request: Request):
        session = ready(request)
        if data.language != 'system' and data.language not in LANGUAGES:
            fail(422, localized_message('chooseASupportedLanguage'))
        with db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            settings = json.loads(connection.execute('SELECT settings FROM users WHERE id=?', (session['user_id'],)).fetchone()[0])
            settings['language'] = data.language
            connection.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), session['user_id']))
        return {'language': data.language}

    return router
