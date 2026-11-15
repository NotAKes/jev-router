"""Rebuild extension, or verify byte-for-byte: python3 -m benchmark.extended --check."""

import argparse
import json
from pathlib import Path

from benchmark.extended.algorithms import build_cases as algorithms
from benchmark.extended.data_tasks import build_cases as data_tasks
from benchmark.extended.vision import build_cases as vision

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'


def build():
    cases, assets = vision()
    cases = algorithms() + data_tasks() + cases
    ids = [c['id'] for c in cases]
    assert len(set(ids)) == len(ids)
    assert all(c['split'] == 'dev' for c in cases)
    payload = ''.join(json.dumps(c, ensure_ascii=False, separators=(',', ':')) + '\n' for c in cases).encode()
    return {'cases.jsonl': payload, **assets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='verify generated files without overwriting')
    args = parser.parse_args()
    generated = build()
    for name, data in generated.items():
        path = DATA / name
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                parser.exit(1, 'Generated data differs: ' + str(path) + '\n')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    print(('Verified' if args.check else 'Generated'), len(generated), 'files; references cross-checked offline.')


if __name__ == '__main__':
    main()
