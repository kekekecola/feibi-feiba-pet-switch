"""Reuse this client's migration identity; never create or delete cloud pets."""
import json
from pathlib import Path

PETS = {'feibi':('feibi-jiubi','菲比啾比'), 'feiba':('feiba-jiubi','菲八啾比')}


def inspect(home):
    result = {'reuse':{}, 'conflicts':[]}
    try:
        state = json.loads((home / '.codex-global-state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    scopes = []
    for value in state.values() if isinstance(state,dict) else []:
        if not isinstance(value,dict):
            continue
        mappings = value.get('migrated-cloud-pet-ids-v1', {})
        cache = value.get('cloud-pet-artwork-cache')
        if not isinstance(mappings,dict):
            continue
        if isinstance(cache,dict) and cache.get('accountId') and cache.get('userId'):
            key = json.dumps([cache['accountId'],cache['userId']], separators=(',',':'))
            matching = mappings.get(key)
            if isinstance(matching,dict):
                scopes.append(matching)
        elif any(isinstance(scope,dict) and any('custom:'+item[0] in scope for item in PETS.values()) for scope in mappings.values()):
            result['conflicts'].append('存在旧迁移记录但无法确认当前账号；请使用 --skip-pets 并指定已有ID。')
    for form,(identifier,name) in PETS.items():
        mapped = {scope.get('custom:'+identifier) for scope in scopes}
        mapped = {item for item in mapped if isinstance(item,str) and item.startswith('pet_')}
        if len(mapped)==1:
            result['reuse'][form] = next(iter(mapped))
        elif len(mapped)>1:
            result['conflicts'].append(name+'存在不同账号的迁移ID，已阻止自动新增。')
        local = []
        for metadata in (home / 'pets').glob('*/pet.json'):
            if metadata.parent.is_symlink() or not metadata.resolve().is_relative_to((home/'pets').resolve()):
                continue
            try:
                pet = json.loads(metadata.read_text(encoding='utf-8'))
            except (OSError,ValueError):
                continue
            if pet.get('displayName')==name and metadata.parent.name!=identifier:
                local.append(metadata.parent.name)
        if local:
            result['conflicts'].append(name+'已有其他同名本地条目（'+', '.join(local)+'）；请复用已有宠物，不再新增。')
    return result
