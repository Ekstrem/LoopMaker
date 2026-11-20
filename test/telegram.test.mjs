import assert from 'node:assert/strict';
import test from 'node:test';

import { TelegramNotifier, chunked, formatDigest, escapeMarkdownV2 } from '../lib/telegram.js';

const silent = { debug() {}, info() {}, warn() {}, error() {} };

function fakeFetch(handler) {
  return async (url, options) => {
    const method = String(url).split('/').pop();
    const params = Object.fromEntries(options.body.entries());
    return handler(method, params);
  };
}

const ok = (result = true) => ({ ok: true, json: async () => ({ ok: true, result }) });

test('дайджест: заголовок и строки', () => {
  const text = formatDigest([
    { ts: '2026-11-01 10:00', from: 'BackEnd', to: 'FE', text: 'готово' },
    { ts: '2026-11-01 10:01', from: 'FrontEnd', to: 'TL', text: 'принял' },
  ]);
  assert.equal(
    text,
    'Чат ролей: 2 нов.\n\n[2026-11-01 10:00] BackEnd → @FE: готово\n[2026-11-01 10:01] FrontEnd → @TL: принял',
  );
});

test('длинный дайджест режется по строкам', () => {
  const items = Array.from({ length: 200 }, (_, index) => ({
    ts: '2026-11-01 10:00', from: 'BackEnd', to: 'FE', text: `строка ${index} `.repeat(4),
  }));
  const parts = chunked(formatDigest(items), 1000);
  assert.ok(parts.length > 1);
  assert.ok(parts.every((part) => part.length <= 1000));
  assert.equal(parts.join('\n').split('\n').filter((line) => line.startsWith('[2026')).length, 200);
});

test('push дедуплицирует по ключу', () => {
  const notifier = new TelegramNotifier({ token: 't', chatId: '1', logger: silent, fetchImpl: fakeFetch(() => ok()) });
  const item = { key: 'k1', ts: 't', from: 'A', to: 'B', text: 'x' };
  assert.equal(notifier.push([item]), 1);
  assert.equal(notifier.push([item]), 0);
  assert.equal(notifier.buffer.length, 1);
});

test('flush отправляет накопленное и очищает буфер', async () => {
  const sent = [];
  const fetchImpl = fakeFetch((method, params) => { sent.push({ method, params }); return ok(); });
  const notifier = new TelegramNotifier({ token: 't', chatId: '42', logger: silent, fetchImpl });
  notifier.push([{ key: 'k1', ts: '09:00', from: 'BackEnd', to: 'FE', text: 'раз' }]);
  notifier.push([{ key: 'k2', ts: '09:01', from: 'FE', to: 'TL', text: 'два' }]);

  const sentCount = await notifier.flush({ force: true });
  assert.equal(sentCount, 2);
  assert.equal(notifier.buffer.length, 0);
  assert.equal(sent[0].method, 'sendMessage');
  assert.equal(sent[0].params.chat_id, '42');
  assert.match(sent[0].params.text, /BackEnd → @FE: раз/);
});

test('при ошибке отправки сообщения возвращаются в буфер', async () => {
  const fetchImpl = fakeFetch(() => ({ ok: false, status: 502, json: async () => ({ ok: false, description: 'bad gateway' }) }));
  const notifier = new TelegramNotifier({ token: 't', chatId: '42', logger: silent, fetchImpl });
  notifier.push([{ key: 'k1', ts: '09:00', from: 'A', to: 'B', text: 'x' }]);
  const sent = await notifier.flush({ force: true });
  assert.equal(sent, 0);
  assert.equal(notifier.buffer.length, 1, 'не потеряли сообщение');
});

test('chat id определяется по первому сообщению боту', async () => {
  const fetchImpl = fakeFetch((method) =>
    method === 'getUpdates'
      ? { ok: true, json: async () => ({ ok: true, result: [{ message: { chat: { id: 987 } } }] }) }
      : { ok: true, json: async () => ({ ok: true, result: true }) },
  );
  const notifier = new TelegramNotifier({ token: 't', logger: silent, fetchImpl });
  await notifier.discover();
  assert.equal(notifier.chatId, '987');
});

test('без чата отправка не падает и сообщения остаются', async () => {
  const fetchImpl = fakeFetch(() => ({ ok: true, json: async () => ({ ok: true, result: [] }) }));
  const notifier = new TelegramNotifier({ token: 't', logger: silent, fetchImpl });
  notifier.push([{ key: 'k1', ts: '09:00', from: 'A', to: 'B', text: 'x' }]);
  assert.equal(await notifier.flush({ force: true }), 0);
  assert.equal(notifier.buffer.length, 1);
});

test('escapeMarkdownV2: экранирует спецсимволы, но сохраняет код в бэк-тиках', () => {
  assert.equal(escapeMarkdownV2('a_b*c'), 'a\\_b\\*c');
  assert.equal(escapeMarkdownV2('[2026-11-20]'), '\\[2026\\-11\\-20\\]');
  // код в бэк-тиках не экранируется — MarkdownV2 рендерит его как код
  assert.equal(escapeMarkdownV2('запусти `git push --force`'), 'запусти `git push --force`');
  assert.equal(escapeMarkdownV2('```\ngit push\n```'), '```\ngit push\n```');
});

test('send: уходит parse_mode MarkdownV2', async () => {
  const sent = [];
  const fetchImpl = fakeFetch((method, params) => {
    sent.push({ method, params });
    return { ok: true, json: async () => ({ ok: true, result: true }) };
  });
  const notifier = new TelegramNotifier({ token: 't', chatId: '9', logger: silent, fetchImpl });
  await notifier.send('a_b');
  assert.equal(sent[0].method, 'sendMessage');
  assert.equal(sent[0].params.parse_mode, 'MarkdownV2');
  assert.equal(sent[0].params.text, 'a\\_b');
});
