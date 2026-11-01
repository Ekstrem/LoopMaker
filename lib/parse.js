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

const LINE = /^-\s*\[([^\]]+)\]\s*(?:→|->|=>)\s*@([^\s:@]+)\s*:\s*(.+)$/;

/** Убрать BOM: Windows-редакторы и PowerShell сохраняют файлы с ним. */
export function stripBom(text) {
  const value = String(text ?? '');
  return value.charCodeAt(0) === 0xfeff ? value.slice(1) : value;
}

/**
 * @param {string} raw одна строка файла
 * @returns {{ ts: string, to: string, text: string } | undefined}
 */
export function parseLine(raw) {
  const line = stripBom(raw).trim();
  if (!line.startsWith('-')) return undefined;
  const match = LINE.exec(line);
  if (!match) return undefined;
  const ts = match[1].trim();
  const to = match[2].trim();
  const text = match[3].trim();
  if (!ts || !to || !text) return undefined;
  return { ts, to, text };
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
