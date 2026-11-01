/**
 * Отправка новых сообщений чата в Telegram.
 *
 * Роль этого модуля — быть **отправителем**: бот только принимает (никакого
 * `getUpdates` в цикле), поэтому он не конфликтует с другими поллерами и
 * переживает перезапуски DSH.
 *
 * Chat id узнаётся один раз: при первом запуске модуль делает один запрос
 * `getUpdates` и берёт чат, откуда боту написали. Дальше он только отправляет.
 */

const API_BASE = 'https://api.telegram.org';
const CHUNK = 3800;

export class TelegramNotifier {
  /**
   * @param {object} deps
   * @param {string} deps.token токен бота
   * @param {string} [deps.chatId] известный chat id (иначе — автоопределение)
   * @param {object} deps.logger
   * @param {number} [deps.everyMinutes] как часто отдавать накопленное
   * @param {number} [deps.maxBuffer] отправить раньше, если накопилось столько
   * @param {string} [deps.apiBase]
   * @param {Function} [deps.fetchImpl]
   */
  constructor(deps) {
    this.token = deps.token;
    this.chatId = deps.chatId ? String(deps.chatId) : '';
    this.logger = deps.logger;
    this.everyMinutes = Number(deps.everyMinutes) > 0 ? Number(deps.everyMinutes) : 2;
    this.maxBuffer = Number(deps.maxBuffer) > 0 ? Number(deps.maxBuffer) : 15;
    this.apiBase = deps.apiBase ?? API_BASE;
    this.fetchImpl = deps.fetchImpl ?? globalThis.fetch;
    this.buffer = [];
    this.keys = new Set();
    this.lastSentAt = 0;
    this.discovering = false;
  }

  /** Один запрос, чтобы узнать, в какой чат писать. */
  async discover() {
    if (this.chatId || this.discovering) return this.chatId;
    this.discovering = true;
    try {
      const result = await this.call('getUpdates', { offset: -1, limit: 1, timeout: 0 });
      const chat = result?.result?.[0]?.message?.chat;
      if (chat?.id) {
        this.chatId = String(chat.id);
        this.logger.info?.(`[loop-chat] telegram: пишу в чат ${this.chatId}`);
      } else {
        this.logger.warn?.('[loop-chat] telegram: напишите боту /start — иначе некуда присылать');
      }
    } catch (error) {
      this.logger.warn?.('[loop-chat] telegram: не удалось определить чат', String(error));
    } finally {
      this.discovering = false;
    }
    return this.chatId;
  }

  /** Положить сообщения в буфер; отправка — по расписанию или по переполнению. */
  push(items) {
    let added = 0;
    for (const item of items ?? []) {
      if (this.keys.has(item.key)) continue;
      this.keys.add(item.key);
      this.buffer.push(item);
      added += 1;
    }
    if (this.buffer.length > 500) {
      this.buffer = this.buffer.slice(-500);
    }
    return added;
  }

  /** Отправить накопленное, если пришло время (или `force`). */
  async flush({ force = false } = {}) {
    if (this.buffer.length === 0) return 0;
    const interval = this.everyMinutes * 60 * 1000;
    const waitMore = !force && this.buffer.length < this.maxBuffer;
    if (waitMore && Date.now() - this.lastSentAt < interval) return 0;

    if (!this.chatId) await this.discover();
    if (!this.chatId) return 0;

    const items = this.buffer;
    this.buffer = [];
    const text = formatDigest(items);
    try {
      await this.send(text);
      this.lastSentAt = Date.now();
      this.logger.info?.(`[loop-chat] telegram: отправлено ${items.length} сообщ.`);
      return items.length;
    } catch (error) {
      // Не теряем сообщения: вернём в буфер (в начало) и попробуем позже.
      this.buffer = [...items, ...this.buffer];
      this.logger.error?.('[loop-chat] telegram: отправка не удалась', String(error));
      return 0;
    }
  }

  async send(text) {
    for (const part of chunked(text)) {
      await this.call('sendMessage', {
        chat_id: this.chatId,
        text: part,
        disable_web_page_preview: 'true',
      });
    }
  }

  async call(method, params) {
    const body = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) body.set(key, String(value));
    }
    const response = await this.fetchImpl(`${this.apiBase}/bot${this.token}/${method}`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body,
    });
    const data = await response.json().catch(() => undefined);
    if (!response.ok || data?.ok === false) {
      throw new Error(`${method}: HTTP ${response.status} ${data?.description ?? ''}`.trim());
    }
    return data;
  }
}

/** Telegram режет сообщения длиннее 4096 — делим по строкам. */
export function chunked(text, limit = CHUNK) {
  if (text.length <= limit) return [text];
  const parts = [];
  let current = '';
  for (const line of text.split('\n')) {
    if (current.length + line.length + 1 > limit) {
      if (current.trim()) parts.push(current.replace(/\n$/, ''));
      current = '';
    }
    current += `${line}\n`;
  }
  if (current.trim()) parts.push(current.replace(/\n$/, ''));
  return parts;
}

/** Заголовок + строки «[время] Автор → @Кому: текст». */
export function formatDigest(items, { title = 'Чат ролей' } = {}) {
  const head = `${title}: ${items.length} нов.`;
  const lines = items.map(
    (item) => `[${item.ts}] ${item.from} → @${item.to}: ${item.text}`,
  );
  return [head, '', ...lines].join('\n');
}
