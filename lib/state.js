/**
 * Состояние моста: что уже доставлено и что ещё не дошло.
 *
 * Две задачи, обе про «не будить лишний раз»:
 *
 * 1. `seen` — ключи обработанных сообщений. Перезапуск DSH не должен
 *    переотправлять историю чата в сессии (иначе роли проснутся на весь бэклог).
 * 2. `pending` — недоставленные сообщения. Сессия адресата может быть не
 *    загружена или только что переименована; сообщение ждёт в очереди, а не
 *    теряется молча.
 *
 * Файл пишется атомарно (temp + rename): сбой посреди записи оставляет
 * предыдущее состояние, а не обрезанный JSON.
 */
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';

const SCHEMA_VERSION = 1;
/** Сколько ключей помним. Хватает на месяцы работы чата. */
export const MAX_SEEN = 5000;

export class StateStore {
  /**
   * @param {{ file: string, logger?: any, maxSeen?: number }} options
   */
  constructor(options) {
    this.file = options.file;
    this.logger = options.logger ?? console;
    this.maxSeen = options.maxSeen ?? MAX_SEEN;
    this.data = { version: SCHEMA_VERSION, seen: [], pending: [], seeded: false };
    this.seenSet = new Set();
    this.writeQueue = Promise.resolve();
    this.loaded = false;
  }

  /** Первый проход по чату уже сделан (история не доставляется повторно). */
  isSeeded() {
    return this.data.seeded === true;
  }

  markSeeded() {
    if (this.data.seeded) return;
    this.data.seeded = true;
    void this.flush();
  }

  async load() {
    if (this.loaded) return this.data;
    let raw;
    try {
      raw = await readFile(this.file, 'utf8');
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        this.logger.warn?.('[loop-chat] состояние не прочитано, начинаем с пустого', String(error));
      }
      this.loaded = true;
      return this.data;
    }
    try {
      const parsed = JSON.parse(raw);
      const seen = Array.isArray(parsed?.seen) ? parsed.seen.filter((key) => typeof key === 'string') : [];
      const pending = Array.isArray(parsed?.pending)
        ? parsed.pending.filter((item) => item && typeof item.key === 'string' && typeof item.target === 'string')
        : [];
      this.data = {
        version: SCHEMA_VERSION,
        seen: seen.slice(-this.maxSeen),
        pending,
        seeded: parsed?.seeded === true,
      };
    } catch (error) {
      this.logger.warn?.('[loop-chat] состояние повреждено, начинаем с пустого', String(error));
      this.data = { version: SCHEMA_VERSION, seen: [], pending: [], seeded: false };
    }
    this.seenSet = new Set(this.data.seen);
    this.loaded = true;
    return this.data;
  }

  isSeen(key) {
    return this.seenSet.has(key);
  }

  /** Запомнить сообщение как обработанное (доставлено или осознанно пропущено). */
  remember(key) {
    if (this.seenSet.has(key)) return;
    this.seenSet.add(key);
    this.data.seen.push(key);
    if (this.data.seen.length > this.maxSeen) {
      const trimmed = this.data.seen.splice(0, this.data.seen.length - this.maxSeen);
      for (const old of trimmed) this.seenSet.delete(old);
    }
    // Файл не пишем на каждую строку: мост вызывает flush() в конце прохода.
  }

  /** Поставить в очередь недоставленное; повторный вызов для того же адресата — no-op. */
  enqueue(item) {
    const exists = this.data.pending.some(
      (candidate) => candidate.key === item.key && candidate.target === item.target,
    );
    if (exists) return false;
    this.data.pending.push({
      key: item.key,
      target: item.target,
      file: item.file,
      from: item.from,
      text: item.text,
      ts: item.ts,
      attempts: 0,
      firstAt: Date.now(),
    });
    void this.flush();
    return true;
  }

  /** Отметить попытку доставки: мост ограничивает число попыток и время жизни. */
  attempted(item) {
    const found = this.data.pending.find(
      (candidate) => candidate.key === item.key && candidate.target === item.target,
    );
    if (!found) return 0;
    found.attempts += 1;
    found.lastAt = Date.now();
    void this.flush();
    return found.attempts;
  }

  /** Убрать из очереди (доставлено или устарело). */
  drop(key, target) {
    const before = this.data.pending.length;
    this.data.pending = this.data.pending.filter(
      (candidate) => !(candidate.key === key && candidate.target === target),
    );
    if (this.data.pending.length !== before) void this.flush();
  }

  pendingList() {
    return this.data.pending.map((item) => ({ ...item }));
  }

  /** Сериализуем записи: последняя в очереди выигрывает. */
  flush() {
    this.writeQueue = this.writeQueue.then(() => this.writeNow()).catch((error) => {
      this.logger.error?.('[loop-chat] не удалось сохранить состояние', String(error));
    });
    return this.writeQueue;
  }

  async writeNow() {
    const payload = JSON.stringify(this.data, null, 2);
    await mkdir(path.dirname(this.file), { recursive: true });
    const temp = `${this.file}.${process.pid}.tmp`;
    await writeFile(temp, payload, 'utf8');
    await rename(temp, this.file);
  }
}
