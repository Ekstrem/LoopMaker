import assert from 'node:assert/strict';
import test from 'node:test';

import { messageKey, parseChat, parseLine, stripBom } from '../lib/parse.js';

test('разбирает строку формата петли', () => {
  const parsed = parseLine('- [2026-11-01 10:00] → @FE: посмотри контракт');
  assert.deepEqual(parsed, {
    ts: '2026-11-01 10:00',
    to: 'FE',
    recipients: ['FE'],
    text: 'посмотри контракт',
  });
});

test('понимает ASCII-стрелку и лишние пробелы', () => {
  assert.deepEqual(parseLine('-  [2026-11-01 10:00]  ->  @tl :  ответ '), {
    ts: '2026-11-01 10:00',
    to: 'tl',
    recipients: ['tl'],
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
test('несколько адресатов через + разбираются все', () => {
  const parsed = parseLine('- [2026-11-01 10:00] → @DO + @TE: оба проверьте');
  assert.deepEqual(parsed?.recipients, ['DO', 'TE']);
  assert.equal(parsed?.to, 'DO + @TE');
  assert.equal(parsed?.text, 'оба проверьте');

  const three = parseLine('- [2026-11-01 10:00] → @TE + @BE + @FE: три');
  assert.deepEqual(three?.recipients, ['TE', 'BE', 'FE']);
});

test('строка без квадратных скобок тоже разбирается', () => {
  const parsed = parseLine('- 2026-11-01 17:19 → @TL: BE-023 готов');
  assert.equal(parsed?.ts, '2026-11-01 17:19');
  assert.deepEqual(parsed?.recipients, ['TL']);
  assert.equal(parsed?.text, 'BE-023 готов');
});

test('маркдаун-буллеты не считаются сообщениями', () => {
  for (const line of [
    '- uses: tailscale/github-action@v2',
    '- `dev` (VPS-2): cron каждые 15 мин → `update.sh`',
    '- [ ] чеклист без адресата',
  ]) {
    assert.equal(parseLine(line), undefined, line);
  }
});
