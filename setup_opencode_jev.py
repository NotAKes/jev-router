import argparse
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile

from jev_router import router_port


def configure(root, port, make_default=False):
    path = root / 'opencode.json'
    if path.with_suffix('.jsonc').exists():
        raise ValueError('Existing opencode.jsonc found; use the manual setup in the guide.')
    previous = path.read_bytes() if path.exists() else None
    config = json.loads(previous.decode('utf-8-sig')) if previous is not None else {}
    if not isinstance(config, dict) or not isinstance(config.get('providers', {}), dict):
        raise ValueError('Unexpected OpenCode config format; no changes made.')
    url = f'http://127.0.0.1:{port}/v1'
    provider = {
        'name': 'Jev Router',
        'package': '@opencode/ai/providers/openai-compatible',
        'settings': {'baseURL': url},
        'models': {'auto': {'name': 'Jev Auto', 'capabilities': {
            'tools': True, 'input': ['text', 'image'], 'output': ['text'],
        }}},
    }
    providers = config.setdefault('providers', {})
    if 'jev' in providers:
        existing = providers['jev']
        # Recognize our own configuration even after the user changes the port.
        endpoint = existing.get('settings', {}) if isinstance(existing, dict) else None
        old_url = endpoint.get('baseURL') if isinstance(endpoint, dict) else None
        match = re.fullmatch(r'http://127\.0\.0\.1:([1-9][0-9]{0,4})/v1', old_url) if isinstance(old_url, str) else None
        if (not match or int(match[1]) > 65535
                or dict(existing, settings=dict(endpoint, baseURL=url)) != provider):
            raise ValueError('Existing custom Jev settings found; no changes made.')
        if existing == provider and (not make_default or config.get('model') == 'jev/auto'):
            return path, None
    providers['jev'] = provider
    if previous is None or make_default:
        config['model'] = 'jev/auto'
    config.setdefault('$schema', 'https://opencode.ai/config.json')
    root.mkdir(parents=True, exist_ok=True)
    temporary = None
    backup = None
    try:
        with NamedTemporaryFile(dir=root, prefix='opencode.', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            file.write((json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        current = path.read_bytes() if path.exists() else None
        if current != previous or path.with_suffix('.jsonc').exists():
            raise ValueError('OpenCode config changed during setup; try again.')
        if previous is not None:
            with NamedTemporaryFile(dir=root, prefix='opencode.json.', suffix='.bak', delete=False) as file:
                file.write(previous)
                backup = Path(file.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path, backup


def main():
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or newer is required.')
    parser = argparse.ArgumentParser(description='Add Jev Auto to OpenCode V2')
    parser.add_argument('--default', action='store_true', help='Replace the current default model')
    args = parser.parse_args()
    root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'opencode'
    port = router_port(os.environ)
    path, backup = configure(root, port, args.default)
    print(f'Jev Auto configured: http://127.0.0.1:{port}/v1')
    print(f'Config: {path}')
    if backup:
        print(f'Backup: {backup}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
