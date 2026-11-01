/**
 * Мост «outbox-чат → сессии ролей».
 *
 * Один проход: прочитать файлы чата, найти новые строки, поставить адресованные
 * в очередь и попробовать доставить. Логика намеренно не знает ни про DSH, ни
 * про файловую систему: `resolveSession` и `send` приходят снаружи, поэтому
 * маршрутизацию можно проверить тестом без поднятого Harness.
 */
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { messageKey, parseChat, stripBom } from './parse.js';
import { render } from './config.js';

const SKIP = new Set(['readme.md', '_dialogue.md']);

const truncate = (text, limit = 120) =>
  text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;

export class ChatBridge {
  /**
   * @param {object} deps
   * @param {object} deps.options результат `normalizeOptions`
   * @param {{info: Function, warn: Function, error: Function, debug?: Function}} deps.logger
   * @param {import('./state.js').StateStore} deps.state
   * @param {(role: string) => Promise<string | undefined>} deps.resolveSession
   * @param {(sessionId: string, text: string) => Promise<void>} deps.send
   * @param {() => number} [deps.now]
   * @param {{ readdir: Function, readFile: Function }} [deps.fs]
   */
  constructor(deps) {
    this.options = deps.options;
    this.logger = deps.logger;
    this.state = deps.state;
    this.resolveSession = deps.resolveSession;
    this.send = deps.send;
    this.now = deps.now ?? (() => Date.now());
    this.fs = deps.fs ?? { readdir, readFile };
    this.timer = undefined;
    this.stopped = true;
    this.running = false;
  }

  async start() {
    await this.state.load();
    this.stopped = false;
    await this.safePoll();
    this.schedule();
    return this;
  }

  stop() {
    this.stopped = true;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
  }

  schedule() {
    if (this.stopped) return;
    this.timer = setTimeout(async () => {
      await this.safePoll();
      this.schedule();
    }, this.options.pollMs);
    // Не держим процесс живым только из-за опроса.
    this.timer?.unref?.();
  }

  /** Ошибка одного прохода не должна убивать мост. */
  async safePoll() {
    if (this.running) return { skipped: true };
    this.running = true;
    try {
      return await this.poll();
    } catch (error) {
      this.logger.error?.('[loop-chat] проход упал', String(error?.stack ?? error));
      return { failed: true };
    } finally {
      this.running = false;
    }
  }

  async poll() {
    const stats = { scanned: 0, enqueued: 0, delivered: 0, pending: 0, seeded: false };
    const files = await this.listFiles();

    for (const file of files) {
      const key = file.replace(/\.md$/i, '').toLowerCase();
      const role = this.options.roles[key];
      if (!role) continue;
      stats.scanned += 1;
      stats.enqueued += await this.scanFile(file, key, role);
    }

    // Первый проход целиком по всем файлам: только после него история считается
    // просмотренной, иначе последние файлы «проснули» бы роли старыми строками.
    if (!this.state.isSeeded()) {
      this.state.markSeeded();
      stats.seeded = true;
    }

    const pendingStats = await this.retryPending();
    stats.delivered = pendingStats.delivered;
    stats.pending = pendingStats.left;
    // Один сброс на проход: иначе первое чтение большого чата писало бы файл
    // состояния на каждую строку.
    await this.state.flush();
    return stats;
  }

  async listFiles() {
    try {
      const entries = await this.fs.readdir(this.options.chatDir);
      return entries
        .filter((name) => typeof name === 'string' && name.toLowerCase().endsWith('.md'))
        .filter((name) => !SKIP.has(name.toLowerCase()))
        .sort();
    } catch (error) {
      this.logger.warn?.(`[loop-chat] папка чата недоступна: ${this.options.chatDir}`, String(error));
      return [];
    }
  }

  async scanFile(file, key, role) {
    const full = path.join(this.options.chatDir, file);
    let text;
    try {
      text = stripBom(await this.fs.readFile(full, 'utf8'));
    } catch (error) {
      this.logger.warn?.(`[loop-chat] не прочитан ${full}`, String(error));
      return 0;
    }

    let enqueued = 0;
    for (const message of parseChat(text)) {
      const keyOfMessage = messageKey(key, message);
      if (this.state.isSeen(keyOfMessage)) continue;

      // При первом запуске (или на пустом состоянии) историю не доставляем:
      // иначе установка плагина разбудила бы все треды разом.
      if (this.options.history === 'skip' && !this.state.isSeeded()) {
        this.state.remember(keyOfMessage);
        continue;
      }

      const targets = this.targetsFor(message.to, key);
      if (targets.length === 0) {
        this.state.remember(keyOfMessage);
        continue;
      }

      for (const target of targets) {
        const added = this.state.enqueue({
          key: keyOfMessage,
          target,
          file: key,
          from: role.title,
          text: message.text,
          ts: message.ts,
        });
        if (added) enqueued += 1;
      }
      this.state.remember(keyOfMessage);
    }
    return enqueued;
  }

  /** Кому адресована строка: `@FE` -> `fe`, `@ALL` -> все роли, кроме автора. */
  targetsFor(tag, senderRole) {
    const wanted = String(tag ?? '').toUpperCase();
    if (!wanted) return [];
    if (wanted === 'ALL') {
      if (!this.options.broadcast) return [];
      return Object.keys(this.options.roles).filter((role) => role !== senderRole);
    }
    const role = this.options.tags.get(wanted);
    if (!role || role === senderRole) return [];
    return [role];
  }

  async retryPending() {
    const queue = this.state.pendingList();
    let delivered = 0;
    for (const item of queue) {
      if (this.now() - (item.firstAt ?? 0) > this.options.pendingTtlMs) {
        this.logger.warn?.(
          `[loop-chat] сообщение для ${item.target} устарело за ${Math.round(this.options.pendingTtlMs / 60000)} мин: ${truncate(item.text, 80)}`,
        );
        this.state.drop(item.key, item.target);
        continue;
      }
      if ((item.attempts ?? 0) >= this.options.maxAttempts) {
        this.logger.warn?.(
          `[loop-chat] сообщение для ${item.target} не доставлено за ${item.attempts} попыток: ${truncate(item.text, 80)}`,
        );
        this.state.drop(item.key, item.target);
        continue;
      }
      if (await this.deliver(item)) delivered += 1;
    }
    return { delivered, left: this.state.pendingList().length };
  }

  async deliver(item) {
    let sessionId;
    try {
      sessionId = await this.resolveSession(item.target);
    } catch (error) {
      this.state.attempted(item);
      this.logger.warn?.(`[loop-chat] не удалось найти сессию роли ${item.target}`, String(error));
      return false;
    }

    if (!sessionId) {
      const attempts = this.state.attempted(item);
      if (attempts === 1 || attempts % 10 === 0) {
        const wanted = this.options.sessions?.[item.target]
          ?? String(this.options.titleTemplate)
            .replace(/\{project\}/g, this.options.project ?? '')
            .replace(/\{role\}/g, this.options.roles?.[item.target]?.title ?? item.target);
        this.logger.warn?.(
          `[loop-chat] сессия «${wanted}» не найдена (попытка ${attempts}); сообщение ждёт в очереди`,
        );
      }
      return false;
    }

    const text = render(this.options.messageTemplate, {
      from: item.from,
      to: this.options.roles?.[item.target]?.title ?? item.target,
      code: String(item.target).toUpperCase(),
      text: item.text,
      ts: item.ts,
      file: item.file,
    });

    if (this.options.dryRun) {
      this.logger.info?.(`[loop-chat][dry-run] ${item.target} (${sessionId}): ${truncate(text)}`);
      this.state.drop(item.key, item.target);
      return true;
    }

    try {
      await this.send(sessionId, text);
    } catch (error) {
      this.state.attempted(item);
      this.logger.error?.(`[loop-chat] доставка в ${item.target} не удалась`, String(error));
      return false;
    }

    this.state.drop(item.key, item.target);
    this.logger.info?.(`[loop-chat] → ${item.target}: ${truncate(text)}`);
    return true;
  }
}
