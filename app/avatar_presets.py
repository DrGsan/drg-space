"""Bundled, versioned avatar artwork; never consumes a user's media quota."""
import io
import secrets
from functools import lru_cache
from pathlib import Path
from PIL import Image
from app.i18n import text

# Stable translation keys; artwork IDs and stored profile values stay unchanged.
CATEGORIES = {
    'abstract': ('avatarAbstractCategory', [
        'avatarAbstract1', 'avatarAbstract2', 'avatarAbstract3',
        'avatarAbstract4', 'avatarAbstract5', 'avatarAbstract6',
        'avatarAbstract7', 'avatarAbstract8', 'avatarAbstract9']),
    'nature': ('avatarNatureCategory', [
        'avatarNature1', 'avatarNature2', 'avatarNature3',
        'avatarNature4', 'avatarNature5', 'avatarNature6',
        'avatarNature7', 'avatarNature8', 'avatarNature9']),
    'animals': ('avatarAnimalsCategory', [
        'avatarAnimals1', 'avatarAnimals2', 'avatarAnimals3',
        'avatarAnimals4', 'avatarAnimals5', 'avatarAnimals6',
        'avatarAnimals7', 'avatarAnimals8', 'avatarAnimals9']),
    'space': ('avatarSpaceCategory', [
        'avatarSpace1', 'avatarSpace2', 'avatarSpace3',
        'avatarSpace4', 'avatarSpace5', 'avatarSpace6',
        'avatarSpace7', 'avatarSpace8', 'avatarSpace9']),
}
PRESETS = frozenset(f'{category}-{i}' for category in CATEGORIES for i in range(1, 10))

def default_profile():
    return {'avatar_preset': f'nature-{secrets.randbelow(9) + 1}'}

def catalog():
    return [{'id': key, 'name': text(title), 'items': [
        {'id': f'{key}-{i}', 'name': text(name), 'url': f'/api/avatar-presets/{key}-{i}'}
        for i, name in enumerate(names, 1)]} for key, (title, names) in CATEGORIES.items()]

@lru_cache(maxsize=36)
def artwork(preset):
    if preset not in PRESETS: raise ValueError('Unknown avatar preset')
    category, number = preset.rsplit('-', 1)
    # Each source is the approved 3×3 concept atlas. Crop just inside each
    # circular illustration; clients apply their usual circular avatar mask.
    centers = {
        'abstract': ([234, 626, 1019], [217, 607, 992], 176),
        'nature': ([232, 626, 1020], [223, 610, 997], 179),
        'animals': ([240, 626, 1012], [222, 604, 982], 183),
        'space': ([240, 626, 1012], [224, 604, 986], 179),
    }
    xs, ys, radius = centers[category]; index = int(number) - 1
    x, y = xs[index % 3], ys[index // 3]
    with Image.open(Path(__file__).with_suffix('') / f'{category}.png') as source:
        image = source.crop((x-radius, y-radius, x+radius, y+radius)).resize((256, 256), Image.Resampling.LANCZOS)
        output = io.BytesIO(); image.save(output, format='WEBP', quality=90)
        return output.getvalue()
