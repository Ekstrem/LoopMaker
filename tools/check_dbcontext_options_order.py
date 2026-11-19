"""check_dbcontext_options_order.py — аудит регистрации non-generic DbContextOptions в BC.

Класс дефекта (BE-133, §27 edm-canon): библиотечные ctor-ы (`EventStoreDbContext`,
`ReadDbContext`) требуют `DbContextOptions<T>`; если в DI нет **non-generic**
`DbContextOptions`, зарегистрированного **после** всех store-методов, Autofac падает
`None of the constructors found ... can be invoked` и контейнер уходит в рестарт-цикл.

Три формы, все ловятся:
  1. `AddSingleton<DbContextOptions>` стоит ДО `AddEventStorePostgres`/`AddReadStorePostgres`
     → поздняя регистрация перекрывает singleton (catalog, somatic-series, ...);
  2. `AddSingleton<DbContextOptions>` отсутствует, хотя store-вызовы есть
     → (privacy, portability);
  3. singleton стоит между store-вызовами (partners) — предупреждение: работает случайно,
     порядок зависит от того, какой store последний.

Выход: rc=0 — чисто; rc=1 — есть нарушения; rc=2 — нечего проверять (нет файлов).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STORE_RE = re.compile(r'Add(?:EventStore|ReadStore)Postgres')
SINGLETON_RE = re.compile(r'AddSingleton<\s*(?:Microsoft\.EntityFrameworkCore\.)?DbContextOptions\s*>')
IGNORE_DIRS = {'bin', 'obj', 'node_modules', '.git'}


def check_file(path: Path) -> list[tuple[str, int, int, str]]:
    """Возвращает [(kind, line_singleton, line_last_store, note)] — только проблемы."""
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    stores, singles = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith('//'):
            continue
        if STORE_RE.search(line):
            stores.append(i)
        if SINGLETON_RE.search(line):
            singles.append(i)
    if not stores:
        return []
    if not singles:
        return [('missing', 0, stores[-1], 'store-вызовы есть, non-generic DbContextOptions не зарегистрирован')]
    out = []
    last_store = stores[-1]
    for s in singles:
        if s < last_store:
            out.append(('before', s, last_store, 'singleton ДО последнего store — будет перекрыт'))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Аудит порядка AddSingleton<DbContextOptions> в BC')
    ap.add_argument('root', nargs='?', default='6-msa', help='корень поиска (по умолчанию 6-msa)')
    ap.add_argument('--strict', action='store_true', help='считать предупреждения ошибками')
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print('нет каталога %s — нечего проверять' % root, file=sys.stderr)
        return 2

    files = [p for p in root.rglob('Program.cs') if not (set(p.parts) & IGNORE_DIRS)]
    problems = 0
    for p in sorted(files):
        for kind, sl, ls, note in check_file(p):
            problems += 1
            where = ('строка %d' % sl) if sl else 'нет'
            print('%s: %s (%s; последний store — строка %d)' % (p.as_posix(), kind, where, ls))
            print('    %s' % note)
    print('проверено Program.cs: %d, проблем: %d' % (len(files), problems))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
