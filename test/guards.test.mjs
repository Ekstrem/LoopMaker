import assert from 'node:assert/strict';
import test from 'node:test';

import { chooseTarget } from '../lib/sessions.js';
import { normalizeOptions } from '../lib/config.js';
import { makeStage } from './helpers.mjs';

const options = normalizeOptions({
  project: 'XDairy',
  chatDir: '/tmp/chat',
  roles: { be: 'BackEnd', fe: 'FrontEnd', tl: 'TechLead', do: 'DevOps' },
});

/* ---------------- ретаргет по живым сессиям ---------------- */

test('живая сессия из памяти не переискивается по заголовку', () => {
  const rows = [{ id: 'live-1', title: 'XDairy | FrontEnd (v2)' }];
  const chosen = chooseTarget('fe', rows, 'live-1', options);
  assert.equal(chosen.id, 'live-1');
  assert.equal(chosen.moved, false);
});

test('сессия ушла в архив -> берём свежую с тем же именем и помечаем переезд', () => {
  const rows = [
    { id: 'new-1', title: 'XDairy | FrontEnd', updatedAt: 100 },
    { id: 'new-2', title: 'XDairy | FrontEnd', updatedAt: 50 },
  ];
  const chosen = chooseTarget('fe', rows, 'old-archived', options);
  assert.equal(chosen.id, 'new-1');
  assert.equal(chosen.moved, true);
  assert.equal(chosen.previous, 'old-archived');
});

test('нет живой сессии -> undefined (архив не адресат)', () => {
  assert.equal(chooseTarget('fe', [], 'old-archived', options), undefined);
  assert.equal(chooseTarget('fe', [{ id: 'x', title: 'XDairy | Arch' }], undefined, options), undefined);
});

/* ---------------- батчинг ---------------- */

test('пачка сообщений одному адресату уходит одной репликой', async (t) => {
  const stage = await makeStage({ sessions: { fe: 'sess-fe' } });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat(
    'be.md',
    '- [2026-11-01 10:00] → @FE: первое\n- [2026-11-01 10:01] → @FE: второе\n',
  );
  const stats = await stage.bridge.poll();

  assert.equal(stage.sent.length, 1, 'две строки — один ход');
  assert.equal(stats.delivered, 1);
  assert.match(stage.sent[0].text, /^сообщения из чата петли \(2, последнее от BackEnd\):/);
  assert.match(stage.sent[0].text, /- \[2026-11-01 10:00\] BackEnd: первое/);
  assert.match(stage.sent[0].text, /- \[2026-11-01 10:01\] BackEnd: второе/);
});

test('дебаунс держит пачку, пока она пополняется', async (t) => {
  const base = Date.now();
  let clock = base;
  const stage = await makeStage({
    config: { batchWindowMs: 10000 },
    sessions: { fe: 'sess-fe' },
    now: () => clock,
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: подожди окна\n');
  await stage.bridge.poll();
  assert.deepEqual(stage.sent, [], 'раньше окна не отправляем');

  await stage.appendChat('be.md', '- [2026-11-01 10:01] → @FE: и это тоже\n');
  clock = base + 11000;
  await stage.bridge.poll();

  assert.equal(stage.sent.length, 1);
  assert.match(stage.sent[0].text, /\(2, последнее от BackEnd\)/);
});

/* ---------------- пороги и эскалация ---------------- */

test('лимит кругов пары: третье письмо уходит координатору, а не адресату', async (t) => {
  const stage = await makeStage({
    config: { maxHops: 2, hopWindowMs: 600000 },
    sessions: { fe: 'sess-fe', tl: 'sess-tl' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();

  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: круг 1\n');
  await stage.bridge.poll();
  await stage.appendChat('be.md', '- [2026-11-01 10:01] → @FE: круг 2\n');
  await stage.bridge.poll();
  assert.equal(stage.sent.length, 2, 'два письма дошли до FE');

  await stage.appendChat('be.md', '- [2026-11-01 10:02] → @FE: круг 3\n');
  const stats = await stage.bridge.poll();
  assert.equal(stats.escalated, 1);
  assert.equal(stage.sent.length, 2, 'третье письмо адресату не ушло');

  // Эскалация лежит в очереди на координатора и уходит следующим проходом.
  await stage.bridge.poll();
  const escalation = stage.sent.at(-1);
  assert.equal(escalation.sessionId, 'sess-tl');
  assert.match(escalation.text, /переписка BackEnd ↔ FrontEnd/);
  assert.match(escalation.text, /2 кругов за 10 мин/);
  // Указатель, а не пересказ: файлы названы, текста сообщений в уведомлении нет.
  assert.match(escalation.text, /be\.md, fe\.md/);
  assert.doesNotMatch(escalation.text, /круг 3/);
});

test('после эскалации пара заморожена и не подливает сообщения', async (t) => {
  const stage = await makeStage({
    config: { maxHops: 1, freezeMs: 600000 },
    sessions: { fe: 'sess-fe', tl: 'sess-tl' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: раз\n');
  await stage.bridge.poll();
  await stage.appendChat('be.md', '- [2026-11-01 10:01] → @FE: два\n');
  await stage.bridge.poll(); // уходит эскалация
  await stage.bridge.poll(); // эскалация доставлена координатору

  const before = stage.sent.length;
  await stage.appendChat('be.md', '- [2026-11-01 10:02] → @FE: три\n');
  await stage.bridge.poll();

  assert.equal(stage.sent.length, before, 'замороженная пара молчит');
  assert.equal(stage.bridge.state.pendingList().length, 1, 'сообщение ждёт разбора');
});

test('бюджет реплик на роль за час тоже включает эскалацию', async (t) => {
  const stage = await makeStage({
    config: { maxPerHour: 1, maxHops: 10 },
    sessions: { fe: 'sess-fe', tl: 'sess-tl', do: 'sess-do' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: от бэка\n');
  await stage.bridge.poll();
  assert.equal(stage.sent.length, 1);

  await stage.writeChat('do.md', '- [2026-11-01 10:01] → @FE: от девопса\n');
  const stats = await stage.bridge.poll();
  assert.equal(stats.escalated, 1);
  assert.equal(stage.sent.length, 1, 'второе письмо роли не доставлено');

  await stage.bridge.poll();
  assert.equal(stage.sent.at(-1).sessionId, 'sess-tl');
  assert.match(stage.sent.at(-1).text, /1 реплик за час/);
});

test('raw-сообщение (эскалация) не оборачивается шаблоном', async (t) => {
  const stage = await makeStage({ sessions: { tl: 'sess-tl' } });
  t.after(stage.cleanup);
  await stage.bridge.start();

  stage.bridge.state.enqueue({
    key: 'escalation|fe|test',
    target: 'tl',
    file: 'loopmaker',
    from: 'LoopMaker',
    fromRole: 'loopmaker',
    text: 'LoopMaker: разберись с перепиской',
    raw: true,
    ts: '2026-11-01 10:00',
  });
  await stage.bridge.poll();

  assert.equal(stage.sent.length, 1);
  assert.equal(stage.sent[0].text, 'LoopMaker: разберись с перепиской');
});

test('координатор не лимитируется: эскалация не эскалирует саму себя', async (t) => {
  const stage = await makeStage({
    config: { maxHops: 1, maxPerHour: 1 },
    sessions: { tl: 'sess-tl', fe: 'sess-fe' },
  });
  t.after(stage.cleanup);
  await stage.bridge.start();

  // Три эскалации подряд в адрес координатора.
  for (let index = 0; index < 3; index += 1) {
    stage.bridge.state.enqueue({
      key: `escalation|fe|${index}`,
      target: 'tl',
      file: 'loopmaker',
      from: 'LoopMaker',
      fromRole: 'loopmaker',
      text: `разбор ${index}`,
      raw: true,
      ts: `2026-11-01 10:0${index}`,
    });
  }
  const stats = await stage.bridge.poll();

  // Три эскалации склеиваются в одну реплику координатору (батчинг), и ни одна
  // из них не эскалируется дальше.
  assert.equal(stage.sent.length, 1, 'доставка координатору состоялась');
  assert.equal(stats.escalated, 0, 'повторной эскалации нет');
  assert.equal(stage.sent[0].sessionId, 'sess-tl');
  assert.match(stage.sent[0].text, /разбор 0/);
  assert.match(stage.sent[0].text, /разбор 2/);
});
/* ---------------- уведомление без копии текста ---------------- */

test('notifyStyle=pointer: обычная доставка без текста сообщения', async (t) => {
  const stage = await makeStage({
    config: { notifyStyle: 'pointer' },
    sessions: { fe: 'sess-fe' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: секретный текст\n');
  await stage.bridge.poll();

  assert.equal(stage.sent.length, 1);
  assert.equal(
    stage.sent[0].text,
    'в чате be.md есть сообщение от BackEnd (2026-11-01 10:00) — посмотри файл',
  );
  assert.doesNotMatch(stage.sent[0].text, /секретный текст/);
});

test('notifyStyle=pointer: пачка тоже без текста, но со счётом', async (t) => {
  const stage = await makeStage({
    config: { notifyStyle: 'pointer' },
    sessions: { fe: 'sess-fe' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat(
    'be.md',
    '- [2026-11-01 10:00] → @FE: раз\n- [2026-11-01 10:05] → @FE: два\n',
  );
  await stage.bridge.poll();

  assert.equal(stage.sent.length, 1);
  assert.equal(
    stage.sent[0].text,
    'в чате be.md есть сообщения от BackEnd — 2 шт. (последнее 2026-11-01 10:05) — посмотри файл',
  );
});
test('resetRuntime чистит счётчики, но помнит просмотренное', async (t) => {
  const stage = await makeStage({ sessions: { fe: 'sess-fe' } });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @FE: раз\n');
  await stage.bridge.poll();
  await stage.bridge.state.setTarget('fe', 'sess-fe');

  const cleared = stage.bridge.state.resetRuntime();
  assert.equal(cleared.deliveries, 1);
  assert.equal(cleared.targets, 1);
  assert.deepEqual(stage.bridge.state.data.deliveries, []);
  assert.equal(stage.bridge.state.targetFor('fe'), undefined);
  assert.ok(stage.bridge.state.data.seen.length > 0, 'seen сохраняется');
});
test('пары с координатором не уходят в эскалацию: он раздаёт задачи, а не звенит', async (t) => {
  const stage = await makeStage({
    config: { maxHops: 2, hopWindowMs: 600000 },
    sessions: { fe: 'sess-fe', tl: 'sess-tl' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  // TechLead пишет FE четыре раза подряд — это нормальная координация.
  for (let index = 0; index < 4; index += 1) {
    await stage.appendChat('tl.md', `- [2026-11-01 10:0${index}] → @FE: задача ${index}\n`);
    await stage.bridge.poll();
  }

  assert.equal(stage.sent.length, 4, 'все четыре доставки прошли');
  assert.equal(stage.bridge.state.pendingList().length, 0);
  assert.equal(Object.keys(stage.bridge.state.data.frozen ?? {}).length, 0, 'ничего не заморожено');
});

test('пинг-понг между исполнителями по-прежнему тормозится', async (t) => {
  const stage = await makeStage({
    config: { maxHops: 2, hopWindowMs: 600000 },
    sessions: { fe: 'sess-fe', be: 'sess-be', tl: 'sess-tl' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  for (let index = 0; index < 3; index += 1) {
    await stage.appendChat('be.md', `- [2026-11-01 10:0${index}] → @FE: вопрос ${index}\n`);
    await stage.bridge.poll();
  }

  assert.equal(stage.sent.length, 2, 'две доставки, третья — эскалация');
  assert.equal(stage.bridge.state.pendingList().length, 1, 'эскалация ждёт координатора');
  assert.ok(Object.keys(stage.bridge.state.data.frozen ?? {}).length > 0, 'пара заморожена');
});
test('одна строка с двумя адресатами доставляется обоим', async (t) => {
  const stage = await makeStage({
    config: { roles: { be: 'BackEnd', do: 'DevOps', te: 'TestEngineer' } },
    sessions: { do: 'sess-do', te: 'sess-te' },
  });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat('be.md', '- [2026-11-01 10:00] → @DO + @TE: проверьте оба\n');
  await stage.bridge.poll();

  assert.equal(stage.sent.length, 2, 'по сообщению каждому адресату');
  assert.deepEqual(stage.sent.map((row) => row.sessionId).sort(), ['sess-do', 'sess-te']);
});
test('служебный _lint.md доставляет замечание автору', async (t) => {
  const stage = await makeStage({ sessions: { be: 'sess-be' } });
  t.after(stage.cleanup);

  await stage.bridge.start();
  await stage.writeChat(
    '_lint.md',
    '- [2026-11-03 18:00] → @BE: строка не распознана, перепиши\n',
  );
  await stage.bridge.poll();

  assert.equal(stage.sent.length, 1);
  assert.equal(stage.sent[0].sessionId, 'sess-be');
  assert.match(stage.sent[0].text, /строка не распознана/);
});
