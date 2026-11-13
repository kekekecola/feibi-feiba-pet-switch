import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


def runtime_directory(app_dir):
    if os.environ.get('FEIBI_SWITCH_DATA_DIR'):
        return Path(os.environ['FEIBI_SWITCH_DATA_DIR'])
    for ancestor in list(app_dir.resolve().parents)[:5]:
        if ancestor.name=='versions' and (ancestor.parent/'current.json').is_file() and (ancestor.parent/'launcher.py').is_file():
            return ancestor.parent/'runtime'
    return app_dir/'runtime'


RUNTIME = runtime_directory(APP_DIR)
VERSION = (APP_DIR.parent / 'VERSION').read_text(encoding='utf-8').strip() if (APP_DIR.parent / 'VERSION').exists() else '1.2.0'
