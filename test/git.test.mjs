import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  SECRET_RE,
  CONFLICT_RE,
  CHAT_COMMIT_MSG,
  CHAT_PR_TITLE,
  mergeChatLines,
} from '../lib/git.js';

// --- [skip ci] в чат-PR: регрессия 2026-11-19 -----------------------------
// Бот клал `[skip ci]` в заголовок PR и в сообщение коммита — GitHub пропускал
// workflows, required-чеки оставались «expected», и PR никогда не мержился
// (10 зависших chat-sync PR). Тест держит константы чистыми.

test('заголовок чат-PR не содержит [skip ci]', () => {
  assert.ok(!/\[skip\s*ci\]/i.test(CHAT_PR_TITLE), `CHAT_PR_TITLE=${CHAT_PR_TITLE}`);
});

test('сообщение чат-коммита не содержит [skip ci]', () => {
  assert.ok(!/\[skip\s*ci\]/i.test(CHAT_COMMIT_MSG), `CHAT_COMMIT_MSG=${CHAT_COMMIT_MSG}`);
});

// --- mergeChatLines: дедуп по trimEnd (D-25) ------------------------------
// Ретро-строки бот переписывал с хвостовым пробелом; exact-дедуп их не видел и
// возвращал дублем в хвост (ar.md 13/15-Nov).

test('mergeChatLines дедуплицирует строки, различающиеся хвостовым пробелом', () => {
  const a = '- [2026-11-13 04:27] → @TL: ретро-строка';
  const b = '- [2026-11-13 04:27] → @TL: ретро-строка ';
  const merged = mergeChatLines([a], [b]);
  assert.equal(merged.length, 1, `ожидали 1, получили ${merged.length}: ${JSON.stringify(merged)}`);
});

test('mergeChatLines не дедуплицирует разные строки', () => {
  const merged = mergeChatLines(
    ['- [2026-11-13 04:27] → @TL: один'],
    ['- [2026-11-13 04:28] → @TL: два'],
  );
  assert.equal(merged.length, 2);
});

// --- mergeChatLines: маркеры конфликта (check_nul rc=1) --------------------
// Маркеры «<<<<<<< HEAD» кончаются пробелом, `\b` после «<» не матчился — маркеры
// проходили в main. CONFLICT_RE без `\b` ловит и их.

test('mergeChatLines вычищает маркеры конфликта', () => {
  const merged = mergeChatLines(
    ['- [2026-11-13 04:27] → @TL: до'],
    ['<<<<<<< HEAD', '=======', '>>>>>>> origin/main', '- [2026-11-13 04:29] → @TL: после'],
  );
  assert.deepEqual(merged, [
    '- [2026-11-13 04:27] → @TL: до',
    '- [2026-11-13 04:29] → @TL: после',
  ]);
});

test('CONFLICT_RE ловит маркер с хвостовым пробелом, но не обычную строку', () => {
  assert.ok(CONFLICT_RE.test('<<<<<<< HEAD'));
  assert.ok(CONFLICT_RE.test('======='));
  assert.ok(CONFLICT_RE.test('>>>>>>> origin/main'));
  assert.ok(!CONFLICT_RE.test('- [2026-11-13 04:27] → @TL: обычная'));
});

// --- mergeChatLines: порядок и полнота union ------------------------------

test('mergeChatLines сортирует по метке времени и не теряет строки', () => {
  const origin = [
    '- [2026-11-13 04:27] → @TL: первая',
    '- [2026-11-13 04:30] → @TL: третья',
  ];
  const local = ['- [2026-11-13 04:28] → @TL: вторая'];
  const merged = mergeChatLines(origin, local);
  assert.deepEqual(
    merged.map((l) => l.match(/\[(.*?)\]/)[1]),
    ['2026-11-13 04:27', '2026-11-13 04:28', '2026-11-13 04:30'],
  );
  assert.equal(merged.length, 3);
});

test('mergeChatLines сохраняет строки без метки (хвостом после сортировки)', () => {
  const merged = mergeChatLines([''], ['строка без метки', '- [2026-11-13 04:27] → @TL: с меткой']);
  assert.equal(merged.length, 2);
});

// --- SECRET_RE: только жёсткие форматы, не обсуждение ----------------------

test('SECRET_RE ловит форматы токенов', () => {
  assert.ok(SECRET_RE.test('ghp_' + 'A'.repeat(30)));
  assert.ok(SECRET_RE.test('github_pat_' + 'B'.repeat(22)));
  assert.ok(SECRET_RE.test('AKIA' + 'C'.repeat(16)));
});

test('SECRET_RE не ловит обезличенные формулировки', () => {
  assert.ok(!SECRET_RE.test('токен (значение скрыто)'));
  assert.ok(!SECRET_RE.test('подложил Password= — гейт красный'));
});
