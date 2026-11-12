import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get('FEIBI_SWITCH_DATA_DIR') or APP_DIR / 'runtime')
VERSION = (APP_DIR.parent / 'VERSION').read_text(encoding='utf-8').strip() if (APP_DIR.parent / 'VERSION').exists() else '1.2.0'
