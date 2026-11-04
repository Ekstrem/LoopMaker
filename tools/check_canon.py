#!/usr/bin/env python3
"""
check_canon.py — канон = единственный источник правды (гейт).

Маркер `<!-- canon: <id> -->` помечает блок-канон. Он обязан встречаться в
репозитории РОВНО ОДИН раз: второе вхождение — копия нормы, которая разойдётся
на первой же правке (класс «второй источник правды»). В остальных местах
допустима только ссылка «канон — <файл>», а не сам текст нормы.

Источник: AR 2026-11-05 00:40 (норма квитанции (ii-b) в ADR-026 была скопирована
в 9-tests/README.md и ITERATION-PLAN §19.1; просьбы «убери копию» выполнялись
частично, потому что правило держалось на памяти, а не на механике).

Запуск:
    python tools/check_canon.py                 # по умолчанию все *.md/*.py
    python tools/check_canon.py --config loop.config.json
    python tools/check_canon.py --selftest      # 2/2
"""

import argparse
import glob
import json
import os
import re
import sys
import tempfile

MARK = re.compile(r'<!--\s*canon:\s*([\w][\w.-]*)\s*-->')
DEFAULT_PATTERNS = ['**/*.md', '**/*.py']


def scan(root, patterns):
    """id канона -> список путей, где маркер встретился."""
    counts = {}
    for pat in patterns:
        for path in glob.glob(os.path.join(root, pat), recursive=True):
            if os.path.isdir(path):
                continue
            rel = path.replace('\\', '/')
            if '/.git/' in rel or '/node_modules/' in rel:
                continue
            try:
                text = open(path, encoding='utf-8-sig').read()
            except (OSError, UnicodeDecodeError):
                continue
            for m in MARK.finditer(text):
                counts.setdefault(m.group(1), []).append(rel)
    return counts


def run(root, patterns):
    counts = scan(root, patterns)
    errors = 0
    if not counts:
        print('нет маркеров canon: — нечего проверять')
        return 0
    for cid in sorted(counts):
        paths = counts[cid]
        if len(paths) > 1:
            print(f'ERROR: canon:{cid} встречается {len(paths)} раз (нужен ровно 1): ' + ', '.join(paths))
            errors += 1
        else:
            print(f'ok: canon:{cid} — {paths[0]}')
    return 1 if errors else 0


def selftest():
    tmp = tempfile.mkdtemp(prefix='check_canon_')
    try:
        # 1) дубль маркера -> ошибка
        d = os.path.join(tmp, 'd1')
        os.makedirs(d)
        open(os.path.join(d, 'a.md'), 'w', encoding='utf-8').write('<!-- canon: x -->\n')
        open(os.path.join(d, 'b.md'), 'w', encoding='utf-8').write('<!-- canon: x -->\n')
        r1 = run(d, ['*.md'])
        # 2) один маркер -> ok
        d2 = os.path.join(tmp, 'd2')
        os.makedirs(d2)
        open(os.path.join(d2, 'a.md'), 'w', encoding='utf-8').write('<!-- canon: y -->\n')
        r2 = run(d2, ['*.md'])
        print(f'selftest: {r1}/{r2} (ждём 1/0)')
        return 0 if (r1 == 1 and r2 == 0) else 1
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description='гейт: маркер canon: встречается ровно один раз')
    ap.add_argument('--config', help='loop.config.json (секция canon.patterns)')
    ap.add_argument('--root', default='.')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)

    if args.selftest:
        sys.exit(selftest())

    patterns = DEFAULT_PATTERNS
    if args.config and os.path.isfile(args.config):
        cfg = json.load(open(args.config, encoding='utf-8'))
        patterns = cfg.get('canon', {}).get('patterns', DEFAULT_PATTERNS)

    sys.exit(run(args.root, patterns))


if __name__ == '__main__':
    main()
