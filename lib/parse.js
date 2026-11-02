/**
 * Разбор строк outbox-чата.
 *
 * Формат один на всю петлю:
 *
 *     - [2026-11-01 10:00] → @FE: посмотри контракт JWT
 *
 * `→` может быть записан по-разному (стрелка, `->`), поэтому принимаем оба
 * варианта: строку пишут руками, и агент, и редактор.
 */

/**
 * Канонический формат строки чата — с квадратными скобками:
 *
 *     - [2026-11-01 10:00] → @FE: текст
 *     - [2026-11-01 10:00] → @DO + @TE: текст
 *
 * Парсер терпим к двум отклонениям, которые встречаются в живых файлах:
 *
 * 1. **Без скобок** — `- 2026-11-01 17:19 → @TL: …` (88 таких строк на 03.11).
 * 2. **Несколько адресатов** через `+` (`@DO + @TE`). Раньше стоял
 *    `@([^\s:@]+)`, который матчил только первого и **молча ронял** всю строку.
 *
 * Обе потери были незаметными: ни ошибки, ни записи в сводке. Разбор:
 * XDiary `5-tech-backlog/chat/TODO-chat-delivery.md`.
 */
const LINE =
  /^-\s*(?:\[([^\]]+)\]|(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}))\s*(?:→|->|=>)\s*@?([^:]+?)\s*:\s*(.+)$/;

/** `DO + @TE` -> ['DO', 'TE'] */
export function splitRecipients(raw) {
  // Разделители адресатов: `+` (канон), а также `/`, `,`, `;` — так писали руками
  // (`@TL/@TE`). Раньше `/` не делился, и адресат `TL/TE` не находился ни в одной
  // сессии: сообщение уходило в никуда без ошибки.
  return String(raw ?? '')
    .split(/[+/,;]/)
    .map((part) => part.trim().replace(/^@/, '').trim())
    .filter(Boolean);
}

/** Убрать BOM: Windows-редакторы и PowerShell сохраняют файлы с ним. */
export function stripBom(text) {
  const value = String(text ?? '');
  return value.charCodeAt(0) === 0xfeff ? value.slice(1) : value;
}

/**
 * @param {string} raw одна строка файла
 * @returns {{ ts: string, to: string, recipients: string[], text: string } | undefined}
 */
export function parseLine(raw) {
  const line = stripBom(raw).trim();
  if (!line.startsWith('-')) return undefined;
  const match = LINE.exec(line);
  if (!match) return undefined;
  const ts = (match[1] ?? match[2] ?? '').trim();
  const to = match[3].trim();
  const text = match[4].trim();
  if (!ts || !to || !text) return undefined;
  const recipients = splitRecipients(to);
  if (recipients.length === 0) return undefined;
  // `to` остаётся исходной строкой адресата (для шаблонов и логов),
  // `recipients` — список, по которому идёт доставка.
  return { ts, to, recipients, text };
}

/** Разобрать весь файл чата: строка -> сообщение, мусор молча пропускается. */
export function parseChat(text) {
  const out = [];
  for (const line of stripBom(text).split(/\r?\n/)) {
    const message = parseLine(line);
    if (message) out.push(message);
  }
  return out;
}

/** Устойчивый к правкам ключ сообщения: файл + время + адресат + хеш текста. */
export function messageKey(file, message) {
  return [String(file).toLowerCase(), message.ts, message.to.toLowerCase(), hash(message.text)].join('|');
}

/** djb2 — короткий хеш, чтобы не тянуть crypto ради ключа состояния. */
export function hash(text) {
  let value = 5381;
  const source = String(text ?? '');
  for (let index = 0; index < source.length; index += 1) {
    value = ((value << 5) + value + source.charCodeAt(index)) | 0;
  }
  return (value >>> 0).toString(16);
}
