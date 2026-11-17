import { execFile } from 'node:child_process';
import { copyFile, readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

// Чат-файлы переросли 1 МБ (ar.md ~1.13 МБ, tl.md ~1.24 МБ) — дефолтный maxBuffer
// execFile (1 МБ) давал RangeError на `git show`. 16 МБ с запасом.
const run = (file, args) => new Promise((resolve, reject) => {
  execFile(file, args, {
    maxBuffer: 64 * 1024 * 1024,
    encoding: 'utf8',
    // Таймаут: зависший git (сеть) не должен вешать цикл опроса навсегда.
    timeout: 30 * 1000,
    // Прокси из песочницы DSH не всегда наследуется — задаём явно, чтобы git
    // (fetch/push по HTTPS) шёл через локальный прокси, а не рвал соединение.
    env: {
      ...process.env,
      HTTP_PROXY: process.env.HTTP_PROXY || 'http://127.0.0.1:7897',
      HTTPS_PROXY: process.env.HTTPS_PROXY || 'http://127.0.0.1:7897',
      ALL_PROXY: process.env.ALL_PROXY || 'http://127.0.0.1:7897',
    },
  }, (error, stdout) => {
    if (error) reject(error);
    else resolve({ stdout });
  });
});

// Лёгкий предохранитель перед коммитом (репозиторий публичный, чат коммитится в git).
// Только ЖЁСТКИЕ форматы настоящих токенов: обсуждение секретов в чате («подложил
// `Password=` → гейт крас») ложным позитивом блокировало коммит ar.md. Полный разбор —
// `check_secrets`; здесь — только очевидное, что роли могли случайно вставить.
const SECRET_RE = /(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})/i;

/**
 * Читает outbox-чат из git (`origin/main`) и коммитит в него же.
 *
 * Причина (PDR-047, 2026-11-12): локальный чекаут отстаёт от `origin` (было 102
 * коммита), поэтому мост читал устаревший чат и «не видел» новые сообщения.
 * Источник правды для чтения — `origin`; для записи — единственный писатель (этот
 * бот) коммитит только `chat/*.md`, не трогая чужое незакоммиченное.
 */
export class GitChat {
  /**
   * @param {object} cfg
   * @param {string} cfg.repo абсолютный путь к репозиторию (XDiary)
   * @param {string} [cfg.chatRel] путь чата относительно корня репо
   * @param {{warn: Function}} [cfg.logger]
   */
  constructor({ repo, chatRel = '5-tech-backlog/chat', logger }) {
    this.repo = repo;
    this.chatRel = chatRel;
    this.logger = logger;
  }

  async fetch() {
    try {
      await run('git', ['-C', this.repo, 'fetch', 'origin', 'main']);
    } catch (error) {
      this.logger?.warn?.('[loop-chat] git fetch не удался', String(error?.message ?? error));
    }
  }

  /** Имена файлов чата (только `.md`, без пути) из origin/main. */
  async list() {
    const { stdout } = await run('git', [
      '-C', this.repo, 'ls-tree', '-r', 'origin/main', '--name-only', '--', this.chatRel,
    ]);
    return stdout
      .split('\n')
      .map((s) => s.trim())
      .filter((s) => s.toLowerCase().endsWith('.md'))
      .map((p) => p.split('/').pop());
  }

  /** Содержимое файла чата из origin/main. */
  async read(file) {
    const { stdout } = await run('git', ['-C', this.repo, 'show', `origin/main:${this.chatRel}/${file}`]);
    return stdout;
  }

  /**
   * Закоммитить локально изменённые `chat/*.md` в origin (единственный писатель).
   *
   * Безопасность: работаем в ИЗОЛИРОВАННОМ worktree от `origin/main`, копируем туда
   * только chat-файлы и коммитим только `chatRel` — чужое незакоммиченное в общем
   * дереве не трогаем. `[skip ci]` — чат не должен будить CI-прогоны.
   *
   * @returns {Promise<boolean>} true, если коммит+пуш прошёл
   */
  async commitChat() {
    // Сначала локальный статус (без сети): нет изменений — сеть не трогаем.
    // Паттерн «подключился → сделал → отключился»: фетч только когда есть что
    // коммитить, а не каждые 5 секунд (GitHub рвал соединения от частоты).
    let changed = false;
    try {
      const { stdout } = await run('git', ['-C', this.repo, 'status', '--porcelain', '--', this.chatRel]);
      changed = stdout.trim().length > 0;
    } catch {
      return false;
    }
    if (!changed) return false;

    await this.fetch();

    const src = path.join(this.repo, ...this.chatRel.split('/'));
    for (const name of await readdir(src)) {
      if (!name.toLowerCase().endsWith('.md')) continue;
      const body = await readFile(path.join(src, name), 'utf8');
      if (SECRET_RE.test(body)) {
        this.logger?.warn?.('[loop-chat] секрет в чате — коммит пропущен', name);
        return false;
      }
    }
    // Защита от потери строк (дефект, сдан TL 2026-11-13): локальный чекаут часто
    // отстаёт от origin — если просто перезаписать, потеряем строки origin. Поэтому
    // коммитим ОБЪЕДИНЕНИЕ: origin целиком + только новые локальные строки.
    for (const name of await readdir(src)) {
      if (!name.toLowerCase().endsWith('.md')) continue;
      const localPath = path.join(src, name);
      const localLines = (await readFile(localPath, 'utf8')).replace(/\r\n/g, '\n').split('\n');
      let originLines = null;
      try {
        const { stdout } = await run('git', ['-C', this.repo, 'show',
          `origin/main:${this.chatRel}/${name}`]);
        originLines = stdout.replace(/\r\n/g, '\n').split('\n');
      } catch {
        continue; // файла нет в origin — новый outbox, потери быть не может
      }
      // Объединение = origin + локальные, БЕЗ дублей и ПО ПОРЯДКУ метки времени.
      // Раньше бот дописывал новые строки в конец — это порождало дубли и нарушение
      // порядка (красный `chat_lint --strict-times`, блокер check-chat на всех PR).
      const tsOf = (line) => {
        const m = /^-?\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]/.exec(line);
        return m ? m[1] : '';
      };
      // Маркеры конфликта — остатки merge, их бот обязан вычистить, а не закоммитить
      // (check_nul делает их rc=1 и блокирует все PR; инцидент be.md 2026-11-17).
      const conflictRe = /^(<<<<<<<|=======|>>>>>>>)\b/;
      const seen = new Set();
      const merged = [];
      for (const line of originLines.concat(localLines)) {
        if (!line || seen.has(line)) continue;
        if (conflictRe.test(line.trim())) continue;
        seen.add(line);
        merged.push(line);
      }
      merged.sort((a, b) => tsOf(a).localeCompare(tsOf(b)));
      const localJoined = localLines.filter(Boolean).join('\n');
      const mergedJoined = merged.join('\n');
      if (mergedJoined !== localJoined) {
        await writeFile(localPath, `${mergedJoined}\n`, 'utf8');
        this.logger?.info?.(`[loop-chat] ${name}: объединение с origin (dedup+sort, ${merged.length} строк)`);
      }
    }
    const wt = path.join(os.tmpdir(), `dsh-chat-${process.pid}-${Date.now()}`);
    await run('git', ['-C', this.repo, 'worktree', 'add', '--detach', wt, 'origin/main']);
    let pushed = false;
    let alreadyCommitted = false;
    try {
      const dst = path.join(wt, ...this.chatRel.split('/'));
      const copyAll = async () => {
        for (const name of await readdir(src)) {
          if (name.toLowerCase().endsWith('.md')) {
            await copyFile(path.join(src, name), path.join(dst, name));
          }
        }
      };
      // `run` бросает на ненулевой exit, поэтому отдельной проверки returncode не нужно.
      const commit = async (msg) => {
        await copyAll();
        await run('git', ['-C', wt, 'add', '--', this.chatRel]);
        await run('git', ['-C', wt, '-c', 'user.name=Evgenei', 'commit', '-m', msg]);
      };
      try {
        await commit('chat(bot): sync [skip ci]');
      } catch {
        alreadyCommitted = true; // «nothing to commit» — изменение уже отправлено ранее
      }
      if (!alreadyCommitted) {
        for (let i = 0; i < 5; i += 1) {
          try {
            await run('git', ['-C', wt, 'push', 'origin', 'HEAD:main']);
            pushed = true;
            break;
          } catch {
            await run('git', ['-C', wt, 'fetch', 'origin', 'main']);
            await run('git', ['-C', wt, 'reset', '--hard', 'origin/main']);
            await commit('chat(bot): sync (retry) [skip ci]');
          }
        }
      }
      return pushed;
    } finally {
      await run('git', ['-C', this.repo, 'worktree', 'remove', '--force', wt]);
      if (pushed || alreadyCommitted) {
        // Догнать общее дерево до origin: иначе HEAD отстаёт, статус вечно «M»,
        // и бот повторно гоняет уже отправленное (worktree checkout — дорого).
        try {
          await this.fetch();
          await run('git', ['-C', this.repo, 'merge', '--ff-only', 'origin/main']);
        } catch {
          /* конфликт с чужим WIP — не лезем, роли догонят через git_sync */
        }
      }
    }
  }
}
