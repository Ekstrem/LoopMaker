#!/usr/bin/env python3
"""Тесты `check_secrets.py` (PDR-20).

Два обязательных случая из PDR-20:
  1) фейковый ключ  -> гейт КРАСНЫЙ (иначе сканер бесполезен);
  2) описание маскирования в прозе -> гейт ЗЕЛЁНЫЙ (иначе гейт блокирует всех
     на документации, как это случилось с `chat/te.md:533`).

Плюс регрессии: JWT, пароль, низкая энтропия, YAML-права `id-token: write`.

Запуск (без внешних зависимостей): python tools/test_check_secrets.py
Код возврата 1 при провале — можно ставить шагом в CI.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCANNER = os.path.join(HERE, "check_secrets.py")


def load():
    spec = importlib.util.spec_from_file_location("check_secrets", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CASES = [
    # (описание, строка, ожидание «найдёт утечку»)
    # --- PDR-20, обязательные два ---
    ("фейковый ключ -> красный", 'apiKey = "S3cr3tV4lu3Xyz123"', True),
    ("описание маскирования -> зелёный",
     "печатает маскированно `{token: bool, tokenLen, clientId: bool, apiBaseLen}`", False),
    # --- регрессии формы ---
    ("JWT -> красный", "x eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abcdefghij", True),
    ("пароль -> красный", "password: hunter2secret", True),
    ("секрет с дефисом -> красный", 'signingKey = "a8Kd93LmQ2xZ7vBn"', True),
    # --- регрессии ложных срабатываний ---
    ("bool как значение -> зелёный", "token: bool", False),
    ("true как значение -> зелёный", "token: true", False),
    ("null как значение -> зелёный", "token: null", False),
    ("низкая энтропия -> зелёный", "token: aaaaaaaa", False),
    ("короткое значение (<8) -> зелёный", "token: ab12cd", False),
    ("YAML-права id-token: write -> зелёный", "permissions: id-token: write", False),
    ("плейсхолдер -> зелёный", 'apiKey = "<your-key-here>"', False),
    ("имя переменной в code-span -> зелёный", "положи в `Otp__Telegram__BotToken=`", False),
    # --- осознанный компромисс энтропии (закреплено @TL 01:30) ---
    # настоящие токены случайны; строка из 36 ОДИНАКОВЫХ символов — не токен.
    # Это НЕ дыра, а выбор: без него эвристика ловила бы `aaaa…` как секрет.
    ("ghp_ + случайные -> красный", "ghp_" + "aB3xK9mQ2pR7tY4uW8vZ6nL1cD5fG0hJ2kM9", True),
    ("ghp_ + одинаковые -> зелёный (осознанно)", "ghp_" + "a" * 36, False),
    ("token: 32 случайных -> красный", "token: " + "aB3xK9mQ2pR7tY4uW8vZ6nL1cD5fG0hJ", True),
    # --- подсказки строки (PDR-20, часть варианта D) ---
    ("описание негативного теста -> зелёный",
     "описание негативного теста: assign token = ab12cd34ef56", False),
    ("«например» НЕ отключает проверку -> красный",
     "например: apiKey = S3cr3tV4lu3Xyz123", True),
    # известное ограничение: стем «пример» матчил бы «наприме́р», поэтому его нет
    ("русское «пример:» не отсекается -> красный (осознанно)",
     "пример: password: hunter2secret", True),
]


def main():
    m = load()
    failures = []
    print("check_secrets: тесты (PDR-20)")
    for name, line, expect in CASES:
        got = bool(m.scan_text(line))
        ok = got == expect
        if not ok:
            failures.append(name)
        print("  [%s] %-38s ожидали=%-5s получили=%s" % ("OK " if ok else "FAIL", name, expect, got))

    # отдельно: та самая строка из инцидента (chat/te.md:533) не должна ловиться
    incident = ("**Дырку закрыл**: добавил `snapshotStorage(page, where)` — печатает "
                "**маскированно** `{token: bool, tokenLen, clientId: bool, clientIdLen, legacy: bool, apiBaseLen}`")
    got = bool(m.scan_text(incident))
    ok = got is False
    if not ok:
        failures.append("инцидент te.md:533")
    print("  [%s] %-38s ожидали=%-5s получили=%s" % ("OK " if ok else "FAIL", "инцидент te.md:533 -> зелёный", False, got))

    print()
    if failures:
        print("ПРОВАЛЕНО: %d -> %s" % (len(failures), ", ".join(failures)))
        return 1
    print("все %d проверок прошли" % (len(CASES) + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
