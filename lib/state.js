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

const isRecord = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value);

function emptyState() {
  return {
    version: SCHEMA_VERSION,
    seen: [],
    pending: [],
    seeded: false,
    /** роль -> id сессии, в которую доставляли последний раз */
    targets: {},
    /** история доставок для счёта «кругов» между ролями */
    deliveries: [],
    /** пара ролей -> timestamp, до которого доставка приостановлена после эскалации */
    frozen: {},
  };
}

export class StateStore {
  /**
   * @param {{ file: string, logger?: any, maxSeen?: number }} options
   */
  constructor(options) {
    this.file = options.file;
    this.logger = options.logger ?? console;
    this.maxSeen = options.maxSeen ?? MAX_SEEN;
    this.data = emptyState();
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
        targets: isRecord(parsed?.targets) ? { ...parsed.targets } : {},
        deliveries: Array.isArray(parsed?.deliveries)
          ? parsed.deliveries.filter((entry) => entry && typeof entry.at === 'number')
          : [],
        frozen: isRecord(parsed?.frozen) ? { ...parsed.frozen } : {},
      };
    } catch (error) {
      this.logger.warn?.('[loop-chat] состояние повреждено, начинаем с пустого', String(error));
      this.data = emptyState();
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
      fromRole: item.fromRole ?? '',
      text: item.text,
      ts: item.ts,
      raw: item.raw === true,
      attempts: 0,
      firstAt: Date.now(),
    });
    void this.flush();
    return true;
  }

  /**
   * Последняя сессия, в которую доставляли роль.
   *
   * Нужна, чтобы переименование треда не сбивало адресацию: пока сессия жива,
   * пишем в неё по id, а по заголовку ищем только когда она ушла в архив.
   */
  targetFor(role) {
    return this.data.targets?.[role];
  }

  setTarget(role, sessionId) {
    if (!sessionId) return;
    if (!this.data.targets) this.data.targets = {};
    if (this.data.targets[role] === sessionId) return;
    this.data.targets[role] = sessionId;
    void this.flush();
  }

  /** История доставок: по ней считаются «круги» между парой ролей. */
  recordDelivery(fromRole, toRole, at = Date.now()) {
    if (!this.data.deliveries) this.data.deliveries = [];
    this.data.deliveries.push({ from: fromRole, to: toRole, at });
    const cutoff = at - 24 * 60 * 60 * 1000;
    if (this.data.deliveries.length > 1000 || this.data.deliveries[0]?.at < cutoff) {
      this.data.deliveries = this.data.deliveries
        .filter((entry) => entry.at >= cutoff)
        .slice(-1000);
    }
  }

  /** Сколько раз пара «A -> B» доставлялась начиная с момента `since`. */
  countDeliveries(fromRole, toRole, since) {
    return (this.data.deliveries ?? []).filter(
      (entry) => entry.from === fromRole && entry.to === toRole && entry.at >= since,
    ).length;
  }

  /** Сколько всего доставок роли-адресату начиная с `since`. */
  countIncoming(toRole, since) {
    return (this.data.deliveries ?? []).filter((entry) => entry.to === toRole && entry.at >= since)
      .length;
  }

  /**
   * Сбросить счётчики и привязки, сохранив `seen`.
   *
   * Нужно при первом боевом запуске: в состоянии могут остаться доставки,
   * сделанные в dry-run (история), и лимит кругов сработал бы сразу.
   */
  resetRuntime() {
    const cleared = {
      deliveries: (this.data.deliveries ?? []).length,
      targets: Object.keys(this.data.targets ?? {}).length,
      frozen: Object.keys(this.data.frozen ?? {}).length,
    };
    this.data.deliveries = [];
    this.data.targets = {};
    this.data.frozen = {};
    void this.flush();
    return cleared;
  }

  /** Заморозка пары после эскалации: дальше разбирается координатор. */
  freeze(pair, until) {
    if (!this.data.frozen) this.data.frozen = {};
    this.data.frozen[pair] = until;
    void this.flush();
  }

  frozenUntil(pair) {
    return this.data.frozen?.[pair] ?? 0;
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

  /**
   * Атомарная запись с ретраями.
   *
   * На Windows файл состояния может быть на мгновение занят (индексатор,
   * антивирус, второй экземпляр DSH) — тогда `rename` падает с EPERM. Одна
   * неудача не должна терять прогресс, поэтому пробуем несколько раз и в
   * крайнем случае пишем напрямую.
   */
  async writeNow() {
    const payload = JSON.stringify(this.data, null, 2);
    await mkdir(path.dirname(this.file), { recursive: true });
    const temp = `${this.file}.${process.pid}.tmp`;
    await writeFile(temp, payload, 'utf8');
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await rename(temp, this.file);
        return;
      } catch (error) {
        if (attempt === 3) {
          await writeFile(this.file, payload, 'utf8');
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 150 * attempt));
      }
    }
  }
}
