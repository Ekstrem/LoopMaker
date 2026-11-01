import assert from 'node:assert/strict';
import test from 'node:test';

import { messageKey, parseChat, parseLine, stripBom } from '../lib/parse.js';

test('разбирает строку формата петли', () => {
  const parsed = parseLine('- [2026-11-01 10:00] → @FE: посмотри контракт');
  assert.deepEqual(parsed, { ts: '2026-11-01 10:00', to: 'FE', text: 'посмотри контракт' });
});

test('понимает ASCII-стрелку и лишние пробелы', () => {
  assert.deepEqual(parseLine('-  [2026-11-01 10:00]  ->  @tl :  ответ '), {
    ts: '2026-11-01 10:00',
    to: 'tl',
    text: 'ответ',
  });
});

test('BOM не мешает первой строке', () => {
  assert.ok(parseLine(`\uFEFF- [2026-11-01 10:00] → @ALL: всем`));
  assert.equal(stripBom('\uFEFFтекст'), 'текст');
});

test('мусор и неполные строки пропускаются', () => {
  for (const line of [
    '',
    'просто текст',
    '- [2026-11-01] без адресата',
    '- [2026-11-01 10:00] → @FE:',
    '| таблица | строка |',
  ]) {
    assert.equal(parseLine(line), undefined, `строка не должна разбираться: ${line}`);
  }
});

test('parseChat читает файл целиком, CRLF и BOM терпит', () => {
  const text = '\uFEFF# CHAT\r\n\r\n- [2026-11-01 10:00] → @TL: первый\r\n- [2026-11-01 10:05] → @DO: второй\r\n';
  assert.deepEqual(parseChat(text).map((message) => message.to), ['TL', 'DO']);
});

test('ключ сообщения различает адресата и файл', () => {
  const message = { ts: '2026-11-01 10:00', to: 'FE', text: 'текст' };
  assert.notEqual(messageKey('be', message), messageKey('fe', message));
  assert.notEqual(messageKey('be', message), messageKey('be', { ...message, to: 'DO' }));
  assert.equal(messageKey('be', message), messageKey('be', { ...message }));
});
