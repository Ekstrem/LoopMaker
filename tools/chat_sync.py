#!/usr/bin/env python3
"""
Фоновая синхронизация outbox'ов чата: worktree роли -> центральное дерево.

Почему нужна: роли пишут чат в свои worktree (`XDiary-<role>/.../chat/<role>.md`),
а бот (dsh-loopmaker) читает центральное дерево (`XDiary/.../chat/<role>.md`).
Эта служба каждые INTERVAL секунд переносит новые строки из worktree в центр
(объединение: центральные строки сохраняются, добавляются только новые из worktree).
Дальше бот сам прочитает центр, доставит и закоммитит.

Запуск (в фоне, в отдельном окне/сессии):
    python chat_sync.py [--interval 5]

Безопасность: пишем только в центральный `chat/*.md`, читаем только worktree'и.
Ничего не коммитим и не пушим — это делает бот.
"""
import argparse
import io
import os
import sys
import time

ROOT = r'C:\Users\About\Documents\GitHub'
CENTRAL = os.path.join(ROOT, 'XDiary', '5-tech-backlog', 'chat')
ROLES = ['be', 'fe', 'do', 'te', 'ar', 'tl', 'po', 'dr']
LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.chat_sync.pid')


def acquire_lock():
    """Один экземпляр службы: pid-файл; если процесс жив — не стартуем."""
    if os.path.exists(LOCK):
        try:
            with io.open(LOCK, encoding='ascii') as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # на Windows: поднимает OSError, если процесса нет
            return False
        except (OSError, ValueError, IOError):
            try:
                os.remove(LOCK)
            except OSError:
                pass
    with io.open(LOCK, 'w', encoding='ascii') as f:
        f.write(str(os.getpid()))
    return True


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
        central_set = set(central_lines)
        new = [l for l in wt_lines if l not in central_set]
        if not new:
            continue
        # Дописываем в конец, не трогая существующие строки.
        body = '\n'.join(new)
        with io.open(central, 'a', encoding='utf-8', newline='\n') as f:
            f.write(('\n' if central_lines and central_lines[-1] else '') + body + '\n')
        moved += len(new)
        print(f'[{time.strftime("%H:%M:%S")}] {role}: +{len(new)} строк', flush=True)
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=int, default=5)
    ap.add_argument('--once', action='store_true', help='один проход и выход')
    args = ap.parse_args()

    if not args.once and not acquire_lock():
        print('chat_sync: уже запущен, выход', flush=True)
        return

    print('chat_sync: старт (interval={}s)'.format(args.interval), flush=True)
    if args.once:
        moved = sync_once()
        print(f'chat_sync: готово, перенесено {moved} строк', flush=True)
        return
    while True:
        try:
            sync_once()
        except Exception as exc:  # одна ошибка не должна ронять службу
            print(f'chat_sync: ошибка {exc!r}', flush=True)
        time.sleep(max(1, args.interval))


if __name__ == '__main__':
    main()
