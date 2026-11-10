"""Request-scoped system messages; never translate user-authored payloads."""
from contextvars import ContextVar
from functools import lru_cache
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent / 'locales'
CONFIGURATION = json.loads((ROOT / 'languages.json').read_text(encoding='utf-8'))
LANGUAGES = CONFIGURATION['languages']
DEFAULT_LANGUAGE = CONFIGURATION['defaultLanguage']
current_language = ContextVar('drg_language', default=DEFAULT_LANGUAGE)


def normalize_language(value):
    base = str(value or '').lower().replace('_', '-').split('-')[0]
    return base if base in LANGUAGES else None


def negotiate_language(header=''):
    candidates = []
    for index, part in enumerate(header[:2048].split(',')):
        tag, *parameters = part.strip().split(';')
        quality = 1.0
        try:
            for parameter in parameters:
                if parameter.strip().startswith('q='):
                    quality = float(parameter.strip()[2:])
        except ValueError:
            continue
        language = normalize_language(tag)
        if language and 0 < quality <= 1:
            candidates.append((-quality, index, language))
    return min(candidates)[2] if candidates else DEFAULT_LANGUAGE


@lru_cache(maxsize=64)
def catalog(language, namespace='server'):
    path = ROOT / (normalize_language(language) or DEFAULT_LANGUAGE) / (namespace + '.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


class SystemLabel(str):
    """A server-owned label, explicitly distinct from user-authored text."""


class Message(str):
    def __new__(cls, key, parameters):
        value = super().__new__(cls, text(key, language=DEFAULT_LANGUAGE, **parameters))
        value.message_key, value.parameters = key, parameters
        return value

    def __reduce__(self):
        return Message, (self.message_key, self.parameters)


def message(key, **parameters):
    return Message(key, parameters)


def text(key, *, language=None, **parameters):
    selected = normalize_language(language) or current_language.get()
    template = catalog(selected).get(key, catalog(DEFAULT_LANGUAGE).get(key, key))
    def substitute(match):
        value = parameters.get(match[1], match[0])
        return str(system_text(str(value), language=selected) if isinstance(value, SystemLabel) else value)
    return re.sub(r'\{\{(\w+)\}\}', substitute, template)


@lru_cache(maxsize=1)
def message_index():
    exact, patterns = {}, []
    for key, value in catalog(DEFAULT_LANGUAGE).items():
        if '{{' not in value:
            exact.setdefault(value, key)
        else:
            names = re.findall(r'\{\{(\w+)\}\}', value)
            parts = re.split(r'\{\{\w+\}\}', value)
            # Match a complete known system message, including its fixed text.
            pattern = re.compile('^' + '(.*?)'.join(re.escape(part) for part in parts) + '$', re.DOTALL)
            patterns.append((sum(map(len, parts)), pattern, key, names))
    return exact, sorted(patterns, key=lambda item: item[0], reverse=True)


def identify_message(value):
    if isinstance(value, Message):
        return value.message_key, value.parameters
    if not isinstance(value, str):
        return None
    exact, patterns = message_index()
    if value in exact:
        return exact[value], {}
    for _, pattern, key, names in patterns:
        match = pattern.fullmatch(value)
        if match:
            return key, dict(zip(names, match.groups()))
    return None


def system_text(value, *, language=None):
    identified = identify_message(value)
    return text(identified[0], language=language, **identified[1]) if identified else value


def localize_error(content, status_code=400):
    if not isinstance(content, dict) or 'detail' not in content:
        return content
    detail = content['detail']
    identified = identify_message(detail)
    if identified:
        key, parameters = identified
        return {**content, 'detail': text(key, **parameters), 'code': content.get('code', key), 'params': parameters}
    # Pydantic errors keep their structure and field locations.
    if isinstance(detail, list):
        translated = []
        for error in detail:
            if not isinstance(error, dict):
                translated.append(error); continue
            message = error.get('msg', '')
            prefix = 'Value error, '
            message = message.removeprefix(prefix)
            translated.append({**error, 'msg': system_text(message)})
        return {**content, 'detail': translated, 'code': content.get('code', 'validation_error'), 'params': content.get('params', {})}
    if isinstance(detail, dict) and isinstance(detail.get('message'), str):
        localized = localize_error({'detail': detail['message']}, status_code)
        return {**content, 'detail': {**detail, 'message': localized['detail']}, 'code': content.get('code', localized['code']), 'params': content.get('params', localized['params'])}
    return {**content, 'code': content.get('code', 'http_' + str(status_code)), 'params': content.get('params', {})}


def recipient_language(settings, device_language=None):
    preference = settings.get('language', 'system')
    return normalize_language(preference) or normalize_language(device_language) or DEFAULT_LANGUAGE
