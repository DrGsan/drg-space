"""Release identity and user-facing notes shared by local and Docker installs."""
from datetime import date
from pathlib import Path
import re
from app.i18n import catalog, normalize_language, DEFAULT_LANGUAGE, LANGUAGES

_ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = (_ROOT / 'VERSION').read_text(encoding='utf-8').strip()


def release_from_changelog(changelog: str, version: str) -> dict:
    heading = re.search(r'^## \[' + re.escape(version) + r'\] — (\d{4}-\d{2}-\d{2}) — (.+)$', changelog, re.MULTILINE)
    if not heading:
        raise ValueError(f'CHANGELOG.md has no release entry for {version}')
    released = date.fromisoformat(heading[1]).isoformat()
    section = changelog[heading.end():].split('\n## ', 1)[0]
    changes = []
    # Opening bullets describe the product; bold deployment fields stay in the log.
    for line in section.splitlines():
        if line.startswith('- **'):
            break
        if line.startswith('- '):
            changes.append(line[2:].strip())
    if not changes:
        raise ValueError(f'CHANGELOG.md has no user-facing changes for {version}')
    return {'version': version, 'date': released, 'title': heading[2], 'changes': changes}


APP_RELEASE = release_from_changelog((_ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'), APP_VERSION)


def release_for_language(release: dict, language: str) -> dict:
    selected = normalize_language(language) or DEFAULT_LANGUAGE
    prefix = 'release' + re.sub('[^A-Za-z0-9]', '_', release['version'])
    keys = [prefix + 'Title'] + [prefix + 'Change' + str(i + 1) for i in range(len(release['changes']))]
    table = catalog(selected, 'release_notes')
    if any(not table.get(key) for key in keys):
        raise ValueError('Missing release notes for ' + release['version'] + ' in ' + selected)
    return {**release, 'title': table[keys[0]], 'changes': [table[key] for key in keys[1:]]}


def validate_release_notes(release: dict) -> None:
    english = release_for_language(release, 'en')
    if english != release:
        raise ValueError('English release catalog differs from CHANGELOG.md')
    for language in LANGUAGES:
        release_for_language(release, language)


# A release is incomplete without all supported UI languages. The same check
# runs during the web build, before an image can replace a working installation.
validate_release_notes(APP_RELEASE)
