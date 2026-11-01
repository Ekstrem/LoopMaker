#!/usr/bin/env python3
"""Telegram-бот: присылает историю чата ролей петли.

Зачем: чат ролей живёт файлами в репозитории (`<chatDir>/*.md`), и чтобы его
читать, нужно открывать проект. Бот отдаёт историю в Telegram — целиком, за
сегодня, только новое или авто-дайджестом.

Почему отдельный токен: long-polling (`getUpdates`) на одном токене может держать
только один процесс. Бот DSH (`dsh-telegram`) уже занимает `TELEGRAM_BOT_TOKEN`,
поэтому здесь нужен **свой** бот (BotFather -> /newbot) и свой токен.

Запуск:
    python tools/chat_bot.py            # слушать команды
    python tools/chat_bot.py --once     # отправить «новое» и выйти (для планировщика)
    python tools/chat_bot.py --check    # показать, что было бы отправлено (без сети)

Токен берётся из --token, переменной окружения из `tokenEnv` конфига,
или из `~/.dsh/.env` (строка `LOOPMAKER_BOT_TOKEN=...`).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "chatbot.config.json")
STATE_FILE = os.path.join(os.path.expanduser("~"), ".dsh", "loopmaker-bot-state.json")
ENV_FILE = os.path.join(os.path.expanduser("~"), ".dsh", ".env")
API = "https://api.telegram.org/bot%s/%s"

# - [2026-11-01 10:00] → @FE: текст
LINE = re.compile(r"^-\s*(?:\[([^\]]+)\]|(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}))\s*(?:→|->|=>)\s*@?([^:]+?)\s*:\s*(.+)$")
SKIP_FILES = {"readme.md", "_dialogue.md"}
MAX_SEEN = 5000
CHUNK = 3800


# ---------------------------------------------------------------- конфигурация

def load_config(path):
    with open(path, encoding="utf-8-sig") as handle:
        config = json.load(handle)
    chat_dir = str(config.get("chatDir", "")).strip()
    if not chat_dir:
        sys.exit("chatbot: в конфиге не задан chatDir")
    config["chatDir"] = os.path.normpath(os.path.expandvars(chat_dir))
    return config


def load_token(config, override=None):
    if override:
        return override.strip()
    env_name = str(config.get("tokenEnv", "LOOPMAKER_BOT_TOKEN")).strip()
    token = (os.environ.get(env_name) or "").strip()
    if token:
        return token
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == env_name:
                    return value.strip().strip('"').strip("'")
    return ""


def load_state():
    default = {"offset": 0, "allowedUsers": [], "seen": [], "digest": {"enabled": False, "minutes": 15, "lastAt": 0}}
    try:
        with open(STATE_FILE, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return default
    default.update({key: data[key] for key in ("offset", "allowedUsers", "seen", "digest") if key in data})
    return default


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    temp = STATE_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    for _ in range(3):
        try:
            os.replace(temp, STATE_FILE)
            return
        except OSError:
            time.sleep(0.15)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------- чтение

def message_key(path, message):
    """Стабильный ключ сообщения: файл + время + адресат + короткий хеш текста."""
    base = "%s|%s|%s" % (os.path.basename(path).lower(), message["ts"], message["to"].lower())
    value = 5381
    for char in message["text"]:
        value = ((value << 5) + value + ord(char)) & 0xFFFFFFFF
    return base + "|" + format(value, "x")


def read_chat(config):
    """Все сообщения из outbox-файлов, по времени."""
    chat_dir = config["chatDir"]
    roles = config.get("roles") or {}
    if not os.path.isdir(chat_dir):
        return []
    messages = []
    for name in sorted(os.listdir(chat_dir)):
        if not name.lower().endswith(".md") or name.lower() in SKIP_FILES:
            continue
        role = name[:-3].lower()
        path = os.path.join(chat_dir, name)
        with open(path, encoding="utf-8-sig") as handle:
            for line in handle:
                match = LINE.match(line.strip())
                if not match:
                    continue
                item = {
                    "ts": (match.group(1) or match.group(2) or "").strip(),
                    "to": match.group(3).strip(),
                    "text": match.group(4).strip(),
                }
                if not item["text"]:
                    continue
                item["role"] = role
                item["from"] = roles.get(role, role.upper())
                item["file"] = name
                item["key"] = message_key(path, item)
                messages.append(item)
    messages.sort(key=lambda item: item["ts"])
    return messages


def unseen(messages, seen):
    known = set(seen)
    return [item for item in messages if item["key"] not in known]


# ------------------------------------------------------------------ сообщения

def render(messages, header):
    lines = [header, ""]
    for item in messages:
        lines.append("[%s] %s → @%s: %s" % (item["ts"], item["from"], item["to"], item["text"]))
    return "\n".join(lines)


def chunked(text, limit=CHUNK):
    """Telegram режет сообщения длиннее 4096: делим по строкам."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            parts.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


def digest_text(messages, config, title="Новое в чате ролей"):
    if not messages:
        return "%s: пусто." % title
    return render(messages, "%s (%d):" % (title, len(messages)))


# ------------------------------------------------------------------ телеграм

class Telegram:
    def __init__(self, token, timeout=30):
        self.token = token
        self.timeout = timeout

    def call(self, method, **params):
        url = API % (self.token, method)
        data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode("utf-8")
        request = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(request, timeout=self.timeout + 10) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_me(self):
        return self.call("getMe").get("result", {})

    def send(self, chat_id, text):
        parts = chunked(text)
        for part in parts:
            self.call("sendMessage", chat_id=chat_id, text=part, disable_web_page_preview="true")
        return len(parts)

    def updates(self, offset, timeout):
        result = self.call("getUpdates", offset=offset, timeout=timeout)
        return result.get("result", [])


# -------------------------------------------------------------------- команды

HELP = """Что я умею:
/история [N] — последние N сообщений (по умолчанию {limit})
/новое — то, что появилось с прошлого раза
/сегодня — сообщения за сегодня
/роли — сколько писал каждый
/дайджест вкл [мин] — присылать новое самому
/дайджест выкл — не присылать
/помощь — эта справка"""


def parse_limit(arg, config):
    try:
        value = int(arg)
    except (TypeError, ValueError):
        value = int(config.get("defaultLimit", 30))
    return max(1, min(value, int(config.get("maxLimit", 200))))


def today_prefix():
    return time.strftime("%Y-%m-%d")


def handle_command(command, argument, config, state):
    """-> (текст ответа | None, что запомнить в state)"""
    limit = parse_limit(argument, config)
    messages = read_chat(config)

    if command in ("start", "help", "помощь", "старт"):
        return HELP.format(limit=config.get("defaultLimit", 30)), None

    if command in ("history", "история", "hist"):
        picked = messages[-limit:]
        if not picked:
            return "В чате пока пусто.", None
        return render(picked, "История чата ролей (последние %d из %d):" % (len(picked), len(messages))), None

    if command in ("new", "новое"):
        fresh = unseen(messages, state.get("seen", []))
        if not fresh:
            return "Нового нет.", None
        return digest_text(fresh[-limit:], config), [item["key"] for item in fresh]

    if command in ("today", "сегодня"):
        prefix = today_prefix()
        picked = [item for item in messages if item["ts"].startswith(prefix)]
        if not picked:
            return "За сегодня сообщений нет.", None
        return render(picked, "Чат ролей за %s (%d):" % (prefix, len(picked))), None

    if command in ("roles", "роли"):
        if not messages:
            return "В чате пока пусто.", None
        counts = {}
        for item in messages:
            counts[item["from"]] = counts.get(item["from"], 0) + 1
        lines = ["Сообщений по ролям (всего %d):" % len(messages), ""]
        for title, count in sorted(counts.items(), key=lambda pair: -pair[1]):
            lines.append("%s: %d" % (title, count))
        return "\n".join(lines), None

    if command in ("digest", "дайджест"):
        words = (argument or "").strip().lower().split()
        if words and words[0] in ("off", "выкл", "стоп"):
            state["digest"] = {"enabled": False, "minutes": state.get("digest", {}).get("minutes", 15), "lastAt": 0}
            return "Дайджест выключен.", None
        minutes = int(config.get("digestMinutes", 15))
        if words and words[0] in ("on", "вкл", "включи") and len(words) > 1:
            try:
                minutes = max(1, int(words[1]))
            except ValueError:
                pass
        state["digest"] = {"enabled": True, "minutes": minutes, "lastAt": int(time.time())}
        return "Дайджест включён: новое каждые %d мин." % minutes, None

    return "Не понял команду. /помощь", None


def split_command(text):
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, ""
    head, _, rest = text[1:].partition(" ")
    return head.split("@")[0].lower(), rest.strip()


# ---------------------------------------------------------------------- режимы

def mode_check(config):
    messages = read_chat(config)
    state = load_state()
    fresh = unseen(messages, state.get("seen", []))
    print("файлов в чате:", len({item["file"] for item in messages}))
    print("сообщений всего:", len(messages))
    print("новых:", len(fresh))
    print("")
    print(render(messages[-int(config.get("defaultLimit", 30)):], "Последние сообщения:"))
    if fresh:
        print("")
        print(digest_text(fresh, config, "Дайджест"))


def mode_once(config, token):
    messages = read_chat(config)
    state = load_state()
    fresh = unseen(messages, state.get("seen", []))
    if not fresh:
        print("нечего отправлять: новых сообщений нет")
        return 0
    chat_id = (state.get("allowedUsers") or [None])[0]
    if not chat_id:
        sys.exit("никому отправлять: сначала напишите боту /start")
    bot = Telegram(token)
    bot.send(chat_id, digest_text(fresh, config))
    state["seen"] = (state.get("seen", []) + [item["key"] for item in fresh])[-MAX_SEEN:]
    state["digest"]["lastAt"] = int(time.time())
    save_state(state)
    print("отправлено сообщений: %d" % len(fresh))
    return 0


def mode_listen(config, token):
    bot = Telegram(token)
    me = bot.get_me()
    state = load_state()
    allowed = [str(value) for value in state.get("allowedUsers", [])]
    print("бот @%s слушает (владельцы: %s)" % (me.get("username", "?"), ", ".join(allowed) or "ещё нет"))

    while True:
        try:
            updates = bot.updates(state.get("offset", 0), int(config.get("pollTimeoutSec", 25)))
        except Exception as error:  # сеть/прокси: пауза и повтор
            print("getUpdates: %s" % error, file=sys.stderr)
            time.sleep(3)
            continue

        for update in updates:
            state["offset"] = update["update_id"] + 1
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            chat_id = chat.get("id")
            user_id = str(sender.get("id", ""))
            text = message.get("text") or ""

            if not chat_id:
                continue

            if not allowed:
                # первый, кто написал, становится владельцем
                allowed = [user_id or str(chat_id)]
                state["allowedUsers"] = allowed
                save_state(state)

            if (user_id or str(chat_id)) not in allowed:
                bot.send(chat_id, "Нет доступа.")
                continue

            command, argument = split_command(text)
            reply, remember = handle_command(command or "", argument, config, state)
            if remember:
                state["seen"] = (state.get("seen", []) + remember)[-MAX_SEEN:]
            if reply:
                bot.send(chat_id, reply)
            save_state(state)

        # авто-дайджест
        digest = state.get("digest") or {}
        if digest.get("enabled") and allowed:
            interval = max(1, int(digest.get("minutes", 15))) * 60
            if time.time() - int(digest.get("lastAt", 0)) >= interval:
                messages = read_chat(config)
                fresh = unseen(messages, state.get("seen", []))
                if fresh:
                    for owner in allowed:
                        bot.send(owner, digest_text(fresh, config))
                    state["seen"] = (state.get("seen", []) + [item["key"] for item in fresh])[-MAX_SEEN:]
                digest["lastAt"] = int(time.time())
                state["digest"] = digest
                save_state(state)


def main():
    parser = argparse.ArgumentParser(description="Telegram-бот истории чата ролей")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--token", default=None, help="токен бота (иначе из env/.env)")
    parser.add_argument("--check", action="store_true", help="показать, что будет отправлено, без сети")
    parser.add_argument("--once", action="store_true", help="отправить новое и выйти")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    config = load_config(args.config)

    if args.check:
        mode_check(config)
        return 0

    token = load_token(config, args.token)
    if not token:
        env_name = config.get("tokenEnv", "LOOPMAKER_BOT_TOKEN")
        sys.exit(
            "нет токена. Создайте бота у @BotFather (/newbot) и положите строку\n"
            "  %s=<токен>\nв %s\n"
            "Отдельный бот нужен потому, что getUpdates на токене может держать только\n"
            "один процесс, а TELEGRAM_BOT_TOKEN уже занят плагином dsh-telegram." % (env_name, ENV_FILE)
        )

    if args.once:
        return mode_once(config, token)

    try:
        mode_listen(config, token)
    except KeyboardInterrupt:
        print("\nостановлен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
