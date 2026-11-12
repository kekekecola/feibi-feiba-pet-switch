"""Read semantic UI labels from the running client's own locale bundles."""
import json
import logging
from pathlib import Path
import re
import struct

KEYS = {
    'new_window': 'electron.appMenu.file.newWindow',
    'profile': 'codex.giftCards.profileMenu',
    'settings': 'codex.profileDropdown.settingsPage',
    'pets': 'settings.nav.miniAndPets',
}
FALLBACK = {
    'new_window': ['新建窗口', 'New Window'],
    'profile': ['打开个人资料菜单', 'Open profile menu'],
    'settings': ['设置', 'Settings'],
    'pets': ['迷你界面和智能伙伴', 'Mini and pets', 'Mini & pets'],
}
_labels = None


def labels():
    global _labels
    if _labels is not None:
        return _labels
    import native_uia as native
    window = native.Windows().main()
    archive = Path(native.Windows().executable(window.pid)).parent / 'resources' / 'app.asar'
    result = {key: set(values) for key, values in FALLBACK.items()}
    from paths import RUNTIME
    cache = RUNTIME / 'ui-labels.json'
    try:
        stamp = [str(archive), archive.stat().st_mtime_ns]
        if cache.exists():
            data = json.loads(cache.read_text(encoding='utf-8'))
            if data.get('stamp') == stamp:
                _labels = data['labels']
                return _labels
        def files(tree):
            for name, item in tree.items():
                if 'files' in item:
                    yield from files(item['files'])
                elif name.endswith('.js') and not item.get('unpacked'):
                    yield item
        patterns = {key: re.compile('"' + re.escape(value) + r'"\s*:\s*("(?:[^"\\]|\\.)*")')
                    for key, value in KEYS.items()}
        with archive.open('rb') as stream:
            _, size, _, length = struct.unpack('<IIII', stream.read(16))
            header = json.loads(stream.read(length))
            for item in files(header['files']):
                stream.seek(8 + size + int(item['offset']))
                source = stream.read(item['size']).decode('utf-8', 'replace')
                for key, pattern in patterns.items():
                    result[key].update(json.loads(m.group(1)) for m in pattern.finditer(source))
        result = {key: sorted(values) for key, values in result.items()}
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({'stamp': stamp, 'labels': result}, ensure_ascii=False), encoding='utf-8')
    except (OSError, ValueError, KeyError, struct.error):
        logging.exception('Could not load client locale bundles; using fallback UI labels')
    _labels = result
    return result


def pattern(key):
    return r'(?i)^(?:' + '|'.join(re.escape(value) for value in labels()[key]) + r')(?:\s|$)'
