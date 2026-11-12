"""Discover pet identities using names, descriptions and cached artwork fingerprints."""
import json
import os
from pathlib import Path
import uuid

import control

DEFAULT_ROLES = Path(__file__).resolve().parent / 'pet-roles.json'
ROLES = DEFAULT_ROLES.parent / 'runtime' / 'pet-roles.json'


def load():
    return json.loads((ROLES if ROLES.exists() else DEFAULT_ROLES).read_text(encoding='utf-8'))


def save(roles):
    ROLES.parent.mkdir(parents=True, exist_ok=True)
    temporary = ROLES.with_name(ROLES.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, ROLES)


def cached_artwork():
    directory = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    state = control.read_json(directory / '.codex-global-state.json')
    for value in state.values():
        if isinstance(value, dict) and isinstance(value.get('cloud-pet-artwork-cache'), dict):
            return value['cloud-pet-artwork-cache'].get('pets', [])
    return []


def refresh(selected_id=None, cache=None):
    roles = load()
    cache = cached_artwork() if cache is None else cache
    changed = False
    for role in roles.values():
        current = next((p for p in cache if p['id'] == role['id']), None)
        if current and current.get('spritesheetFingerprint'):
            fingerprint = current['spritesheetFingerprint']
            if role.get('fingerprint') != fingerprint:
                role['fingerprint'] = fingerprint
                changed = True
        matches = [p for p in cache if role.get('fingerprint') and p.get('spritesheetFingerprint') == role['fingerprint']]
        candidate = next((p for p in matches if p['id'] == selected_id), None)
        if candidate is None and len(matches) == 1:
            candidate = matches[0]
        if candidate and candidate['id'] != role['id']:
            role['id'] = candidate['id']
            changed = True
    if changed:
        save(roles)
    return roles


def bind(form, pet_id):
    if not isinstance(pet_id, str) or not pet_id.startswith(('pet_', 'custom:')):
        raise RuntimeError('无法确认原生宠物 ID。')
    roles = load()
    roles[form]['id'] = pet_id
    save(roles)


def matches(form, text):
    role = load()[form]
    description = role.get('description', '')
    return (text == role['name'] or text.startswith(role['name'] + ' ')
            or bool(description and text.endswith(description)))


def observe_names(names):
    roles = load()
    changed = False
    for form, text in names.items():
        description = roles[form].get('description', '')
        if description and text.endswith(description):
            name = text[:-len(description)].strip()
            if name and roles[form]['name'] != name:
                roles[form]['name'] = name
                changed = True
    if changed:
        save(roles)
