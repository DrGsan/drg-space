"""One bundled wallpaper catalog shared by the server, web and native clients."""
import json
import secrets
from pathlib import Path
from app.i18n import text


CATEGORIES = json.loads(Path(__file__).with_name('wallpaper_catalog.json').read_text(encoding='utf-8'))
ORDERED_PRESETS = tuple(item['id'] for category in CATEGORIES for item in category['items'])
# Retired artwork remains readable for already saved settings and backups,
# but is excluded from the picker and random defaults for new accounts.
RETIRED = json.loads(Path(__file__).with_name('wallpaper_retired.json').read_text(encoding='utf-8'))
PRESETS = frozenset(ORDERED_PRESETS) | {item['id'] for item in RETIRED}


def default_wallpaper():
    # Only account creation calls this: reads, imports and restored accounts
    # must keep their saved choice. Every photograph has equal probability.
    return secrets.choice(ORDERED_PRESETS)


def catalog():
    return [{'id': category['id'], 'name': text(category['name_key']), 'items': [
        {'id': item['id'], 'name': text(item['name_key']),
         'url': '/assets/wallpapers/' + item['id'] + '.webp'}
        for item in category['items']]} for category in CATEGORIES]
