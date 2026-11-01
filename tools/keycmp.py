#!/usr/bin/env python3
"""Безопасное сравнение секретов: «совпадают / не совпадают», без вывода значений.

Зачем: в XDiary роль BackEnd сравнивала `Jwt:SigningKey` и вписала в чат
**фрагменты** ключей (хвост вида …XXXX=), чтобы показать расхождение.
Желание понятное (диагностика), но чат публичный. Инструкция «не вставляй
ключи» не помогает, если сравнить иначе нечем.

Этот инструмент делает безопасный путь короче небезопасного:

    python tools/keycmp.py --a-env JWT_A --b-env JWT_B
    python tools/keycmp.py --a-file a.txt --b-file b.txt
    python tools/keycmp.py --a "..." --b-file .env      # ищет строку KEY=VALUE
    python tools/keycmp.py --a-env JWT_A --b-file .env --b-key Jwt__SigningKey

Печатает только: совпадают/не совпадают, длину и первые 8 символов sha256
(отпечаток — чтобы можно было сказать «ключи различаются», не раскрывая их).

Коды возврата: 0 — совпадают, 2 — различаются, 1 — ошибка ввода.
"""
import argparse
import hashlib
import os
import sys


def digest(value):
    h = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return h


def read_file(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def from_env_value_file(path, key):
    """Достать значение ключа из .env-подобного файла (KEY=VALUE, кавычки снимаем)."""
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip("\"'")
    return None


def pick(args, side):
    env_name = getattr(args, side + "_env")
    file_path = getattr(args, side + "_file")
    value = getattr(args, side)
    key = getattr(args, side + "_key")

    if value is not None:
        return value
    if env_name is not None:
        v = os.environ.get(env_name)
        if v is None:
            sys.exit("keycmp: переменная окружения %s не задана" % env_name)
        return v
    if file_path is not None:
        raw = read_file(file_path)
        if key:
            v = from_env_value_file(file_path, key)
            if v is None:
                sys.exit("keycmp: в %s нет ключа %s" % (file_path, key))
            return v
        return raw.strip()
    sys.exit("keycmp: для %s нужно одно из --%s, --%s-env, --%s-file" % (side, side, side, side))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Сравнить два секрета, не печатая значения")
    for side in ("a", "b"):
        ap.add_argument("--" + side, default=None, help="значение %s (осторожно: попадёт в историю шелла)" % side)
        ap.add_argument("--%s-env" % side, default=None, help="взять %s из переменной окружения" % side)
        ap.add_argument("--%s-file" % side, default=None, help="файл со значением %s" % side)
        ap.add_argument("--%s-key" % side, default=None, help="если файл — .env: имя ключа")
    args = ap.parse_args()

    a = pick(args, "a")
    b = pick(args, "b")

    if args.a is not None or args.b is not None:
        print("keycmp: значение, переданное аргументом, остаётся в истории шелла — "
              "лучше --a-env/--a-file", file=sys.stderr)

    same = a == b
    print("совпадают" if same else "НЕ совпадают")
    print("  a: len=%d sha256:%s" % (len(a), digest(a)))
    print("  b: len=%d sha256:%s" % (len(b), digest(b)))
    if not same:
        print("\nЧто делать при расхождении: не вставлять ключи в чат — создать задачу "
              "«ключи различаются» и ротировать нужный через координатора.")
    return 0 if same else 2


if __name__ == "__main__":
    sys.exit(main())
