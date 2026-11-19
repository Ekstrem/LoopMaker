#!/usr/bin/env python3
"""
Фоновая синхронизация outbox'ов чата: worktree роли -> центральное дерево.

Почему нужна: роли пишут чат в свои worktree (`XDiary-<role>/.../chat/<role>.md`),
а бот (dsh-loopmaker) читает центральное дерево (`XDiary/.../chat/<role>.md`).
Эта служба каждые INTERVAL секунд переносит новые строки из worktree в центр
(объединение: центральные строки сохраняются, добавляются только новые из worktree).
Дальше бот сам прочитает центр, доставит и закоммитит.

Запуск (без окна): pythonw chat_sync.py [--interval 5]
Автозапуск: задача Windows «XDiaryChatSync» (каждые 5 мин, pythonw — окна нет).

Безопасность: пишем только в центральный `chat/*.md`, читаем только worktree'и.
Ничего не коммитим и не пушим — это делает бот.

Одиночный запуск: heartbeat-lock (pid-файл + mtime). Живой экземпляр обновляет
mtime каждую итерацию; молчание дольше LOCK_STALE секунд = экземпляр мёртв.
"""
import argparse
import io
import os
import sys
import time

ROOT = r'C:\Users\About\Documents\GitHub'
CENTRAL = os.path.join(ROOT, 'XDiary', '5-tech-backlog', 'chat')
ROLES = ['be', 'fe', 'do', 'te', 'ar', 'tl', 'po', 'dr']
HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, '.chat_sync.pid')
LOG = os.path.join(HERE, 'chat_sync.log')
LOCK_STALE = 60  # сек: дольше — считаем экземпляр мёртвым


def log(msg):
    """Пишем в файл, а не в консоль: служба запускается без окна (pythonw)."""
    try:
        with io.open(LOG, 'a', encoding='utf-8', newline='\n') as f:
            f.write('{} {}\n'.format(time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except OSError:
        pass


def acquire_lock():
    """Один экземпляр: если lock свежий — живой экземпляр есть, выходим."""
    if os.path.exists(LOCK):
        try:
            age = time.time() - os.path.getmtime(LOCK)
            if age < LOCK_STALE:
                return False
        except OSError:
            pass
    with io.open(LOCK, 'w', encoding='ascii') as f:
        f.write(str(os.getpid()))
    return True


def touch_lock():
    try:
        os.utime(LOCK, None)
    except OSError:
        pass


def read_lines(path):
    try:
        with io.open(path, encoding='utf-8') as f:
            return f.read().replace('\r\n', '\n').split('\n')
    except (FileNotFoundError, OSError):
        return []


def sync_once():
    moved = 0
    for role in ROLES:
        wt = os.path.join(ROOT, f'XDiary-{role}', '5-tech-backlog', 'chat', f'{role}.md')
        central = os.path.join(CENTRAL, f'{role}.md')
        wt_lines = [l for l in read_lines(wt) if l.strip()]
        central_lines = read_lines(central)
        # Дедуп по rstrip: ретро-строки из worktree могут отличаться от центральных
        # только хвостовым пробелом — exact-дедуп их не видел и возвращал дублем
        # каждые 5 с (D-25, ar.md 13/15-Nov 2026-11-19).
        central_set = {l.rstrip() for l in central_lines if l.strip()}
        new = [l for l in wt_lines if l.rstrip() not in central_set]
        if not new:
            continue
        body = '\n'.join(new)
        with io.open(central, 'a', encoding='utf-8', newline='\n') as f:
            f.write(('\n' if central_lines and central_lines[-1] else '') + body + '\n')
        moved += len(new)
        log(f'{role}: +{len(new)} строк')
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=int, default=5)
    ap.add_argument('--once', action='store_true', help='один проход и выход')
    args = ap.parse_args()

    if not args.once and not acquire_lock():
        log('уже запущен — выход')
        return

    log('старт (interval={}s, pid={})'.format(args.interval, os.getpid()))
    if args.once:
        moved = sync_once()
        log(f'готово, перенесено {moved} строк')
        return
    while True:
        try:
            sync_once()
        except Exception as exc:  # одна ошибка не должна ронять службу
            log(f'ошибка {exc!r}')
        touch_lock()
        time.sleep(max(1, args.interval))


if __name__ == '__main__':
    main()
