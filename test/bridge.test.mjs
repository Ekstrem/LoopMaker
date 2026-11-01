import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, appendFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { normalizeOptions } from '../lib/config.js';
import { StateStore } from '../lib/state.js';
import { ChatBridge } from '../lib/bridge.js';

const silent = { debug() {}, info() {}, warn() {}, error() {} };

/** Сцена: папка чата + состояние + фейковые сессии и отправка. */
async function makeStage({ config = {}, sessions = {}, send } = {}) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'loopmaker-test-'));
  const chatDir = path.join(dir, 'chat');
  await writeFile(path.join(dir, '.keep'), '', 'utf8').catch(() => {});
  const { mkdir } = await import('node:fs/promises');
  await mkdir(chatDir, { recursive: true });

  const options = normalizeOptions(
    {
      project: 'XDairy',
      chatDir,
      pollMs: 1000,
      stateFile: path.join(dir, 'state.json'),
      roles: { be: 'BackEnd', fe: 'FrontEnd', tl: 'TechLead' },
      ...config,
    },
    dir,
  );

  const sent = [];
  const sessionMap = { ...sessions };
  const bridge = new ChatBridge({
    options,
    logger: silent,
    state: new StateStore({ file: options.stateFile, logger: silent }),
    resolveSession: async (role) => sessionMap[role],
    send: send ?? (async (sessionId, text) => { sent.push({ sessionId, text }); }),
  });

  const writeChat = (file, text) => writeFile(path.join(chatDir, file), text, 'utf8');
  const appendChat = (file, text) => appendFile(path.join(chatDir, file), text, 'utf8');

  return {
    dir,
    chatDir,
    bridge,
    options,
    sent,
    sessionMap,
    writeChat,
    appendChat,
    /** Останавливаем мост и ждём запись состояния, иначе rm ловит EBUSY на Windows. */
    cleanup: async () => {
      bridge.stop();
      try {
        await bridge.state.flush();
      } catch {
        /* состояние могло не создаваться */
      }
      await rm(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
    },
  };
}

test('история не доставляется при первом запуске, новые сообщения — доставляются', async (t) => {
  const stage = await makeStage({ sessions: { fe: 'sess-fe' } });
  t.after(stage.cleanup);

  await stage.writeChat('be.md', '# CHAT outbox — Back-end\n- [2026-11-01 09:00] → @FE: старое\n');
  await stage.bridge.start();
  assert.deepEqual(stage.sent, [], 'история не должна будить сессию');

  await stage.appendChat('be.md', '- [2026-11-01 10:00] → @FE: новое сообщение\n');
  const stats = await stage.bridge.poll();

  assert.equal(stage.sent.length, 1);
  assert.equal(stage.sent[0].sessionId, 'sess-fe');
  assert.equal(stage.sent[0].text, 'сообщение от BackEnd: «новое сообщение»');
  assert.equal(stats.delivered, 1);
  stage.bridge.stop();
});

test('повторный проход не отправляет то же сообщение дважды', async (t) => {
  const stage = await makeStage({ sessions: { fe: 'sess-fe' } });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: один раз\n');
  await stage.bridge.poll();
  await stage.bridge.poll();
  await stage.bridge.poll();

  assert.equal(stage.sent.length, 1);
  stage.bridge.stop();
});

test('сообщение самому себе не доставляется', async (t) => {
  const stage = await makeStage({ sessions: { be: 'sess-be', fe: 'sess-fe' } });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @BE: заметка себе\n');
  await stage.bridge.poll();

  assert.deepEqual(stage.sent, []);
  stage.bridge.stop();
});

test('неизвестный адресат игнорируется', async (t) => {
  const stage = await makeStage({ sessions: { fe: 'sess-fe' } });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @XX: в никуда\n');
  await stage.bridge.poll();

  assert.deepEqual(stage.sent, []);
  stage.bridge.stop();
});

test('@ALL по умолчанию не рассылается, с broadcast — всем кроме автора', async (t) => {
  const off = await makeStage({ sessions: { fe: 'sess-fe', tl: 'sess-tl' } });
  t.after(off.cleanup);
  await off.bridge.start();
  await off.writeChat('be.md', '- [2026-11-01 10:00] → @ALL: всем привет\n');
  await off.bridge.poll();
  assert.deepEqual(off.sent, []);
  off.bridge.stop();

  const on = await makeStage({
    config: { broadcast: true },
    sessions: { fe: 'sess-fe', tl: 'sess-tl', be: 'sess-be' },
  });
  t.after(on.cleanup);
  await on.bridge.start();
  await on.writeChat('be.md', '- [2026-11-01 10:00] → @ALL: всем привет\n');
  await on.bridge.poll();
  assert.deepEqual(on.sent.map((item) => item.sessionId).sort(), ['sess-fe', 'sess-tl']);
  on.bridge.stop();
});

test('сессии нет -> сообщение ждёт в очереди и доставляется, когда сессия появилась', async (t) => {
  const stage = await makeStage({ sessions: {} });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: подожди меня\n');
  await stage.bridge.poll();
  assert.deepEqual(stage.sent, []);

  stage.sessionMap.fe = 'sess-fe'; // сессию переименовали / открыли
  const stats = await stage.bridge.poll();

  assert.equal(stats.delivered, 1);
  assert.equal(stage.sent[0].sessionId, 'sess-fe');
  assert.equal(stage.bridge.state.pendingList().length, 0);
  stage.bridge.stop();
});

test('ошибка отправки оставляет сообщение в очереди для повтора', async (t) => {
  let fail = true;
  const stage = await makeStage({
    sessions: { fe: 'sess-fe' },
    send: async (sessionId, text) => {
      if (fail) throw new Error('harness занят');
      stage.sent.push({ sessionId, text });
    },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: дойди\n');
  await stage.bridge.poll();
  assert.equal(stage.sent.length, 0);
  assert.equal(stage.bridge.state.pendingList().length, 1);

  fail = false;
  await stage.bridge.poll();
  assert.equal(stage.sent.length, 1);
  stage.bridge.stop();
});

test('несколько адресатов и шаблон сообщения настраиваются', async (t) => {
  const stage = await makeStage({
    config: { messageTemplate: '[{ts}] {from} → {to}: {text}' },
    sessions: { fe: 'sess-fe', tl: 'sess-tl' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat(
    'be.md',
    '- [2026-11-01 10:00] → @FE: первое\n- [2026-11-01 10:01] → @TL: второе\n',
  );
  await stage.bridge.poll();

  assert.deepEqual(stage.sent.map((item) => item.text), [
    '[2026-11-01 10:00] BackEnd → FrontEnd: первое',
    '[2026-11-01 10:01] BackEnd → TechLead: второе',
  ]);
  stage.bridge.stop();
});

test('битый файл чата не ломает проход', async (t) => {
  const stage = await makeStage({ sessions: { fe: 'sess-fe' } });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', 'мусор без формата\n- [2026-11-01 10:00] → @FE: нормальное\n');
  await stage.bridge.poll();
  assert.equal(stage.sent.length, 1);
  stage.bridge.stop();
});

test('очередь переживает перезапуск: состояние читается с диска', async (t) => {
  const stage = await makeStage({ sessions: {} });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: переживу рестарт\n');
  await stage.bridge.poll();
  stage.bridge.stop();
  await stage.bridge.state.flush();

  // Новый экземпляр моста с тем же файлом состояния.
  const revived = new ChatBridge({
    options: stage.options,
    logger: silent,
    state: new StateStore({ file: stage.options.stateFile, logger: silent }),
    resolveSession: async (role) => (role === 'fe' ? 'sess-fe' : undefined),
    send: async (sessionId, text) => { stage.sent.push({ sessionId, text }); },
  });
  await revived.start();
  await revived.poll();

  assert.equal(stage.sent.length, 1);
  assert.equal(stage.sent[0].text, 'сообщение от BackEnd: «переживу рестарт»');
  revived.stop();
});
