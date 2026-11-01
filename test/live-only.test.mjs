import assert from 'node:assert/strict';
import test from 'node:test';

import { makeStage } from './helpers.mjs';

/**
 * Режим live-only: закрытый тред не поднимаем, сообщение ждёт в очереди.
 *
 * Регрессия, ради которой это написано: `agents.resume` собирает агента из
 * контекста плагина, а инструменты монтирует хост — поднятый тред схлопывал
 * реестр инструментов и роль теряла `read`/`pwsh`/`edit`.
 */
test('закрытый тред: сообщение ждёт, попытки не расходуются', async (t) => {
  const notLive = Object.assign(new Error('сессия не открыта'), { code: 'NOT_LIVE' });
  const stage = await makeStage({
    sessions: { fe: 'sess-fe' },
    send: async () => {
      throw notLive;
    },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: подожду\n');
  const stats = await stage.bridge.poll();

  assert.equal(stats.delivered, 0);
  assert.equal(stage.bridge.state.pendingList().length, 1, 'сообщение осталось в очереди');
  assert.equal(stage.bridge.state.pendingList()[0].attempts, 0, 'попытки не сожжены');
});

test('тред открыли — накопленное доставляется', async (t) => {
  let live = false;
  const delivered = [];
  const stage = await makeStage({
    sessions: { fe: 'sess-fe' },
    send: async (sessionId, text) => {
      if (!live) throw Object.assign(new Error('закрыт'), { code: 'NOT_LIVE' });
      delivered.push({ sessionId, text });
    },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: дождался\n');
  await stage.bridge.poll();
  assert.equal(delivered.length, 0);

  live = true;
  const stats = await stage.bridge.poll();
  assert.equal(stats.delivered, 1);
  assert.equal(delivered[0].sessionId, 'sess-fe');
  assert.equal(stage.bridge.state.pendingList().length, 0);
});
