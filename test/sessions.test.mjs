import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeOptions, normalizeRoles, render, tagIndex } from '../lib/config.js';
import { normalizeTitle, pickSession, wantedTitle } from '../lib/sessions.js';

const options = normalizeOptions({
  project: 'XDairy',
  chatDir: '/tmp/chat',
  roles: { be: 'BackEnd', fe: 'FrontEnd', te: { title: 'TestEngineer', tag: 'QA' } },
});

test('роли нормализуются из строки и объекта', () => {
  const roles = normalizeRoles({ be: 'BackEnd', te: { title: 'TestEngineer', tag: 'qa' } });
  assert.deepEqual(roles.be, { title: 'BackEnd', tag: 'BE' });
  assert.deepEqual(roles.te, { title: 'TestEngineer', tag: 'QA' });
});

test('теги ищутся без учёта регистра, включая кастомные', () => {
  const tags = tagIndex(normalizeRoles({ fe: 'FrontEnd', te: { title: 'Tester', tag: 'QA' } }));
  assert.equal(tags.get('FE'), 'fe');
  assert.equal(tags.get('QA'), 'te');
});

test('шаблон заголовка собирается из project и роли', () => {
  assert.equal(wantedTitle('fe', options), 'XDairy | FrontEnd');
  assert.equal(wantedTitle('te', options), 'XDairy | TestEngineer');
});

test('явное переопределение сильнее шаблона', () => {
  const custom = normalizeOptions({ project: 'X', chatDir: '/tmp', sessions: { fe: 'X | Фронт' } });
  assert.equal(wantedTitle('fe', custom), 'X | Фронт');
});

test('заголовки сравниваются мягко: регистр, ё, кавычки', () => {
  assert.equal(normalizeTitle('  XDairy  |  «BackEnd» '), 'xdairy | backend');
  const rows = [{ id: 'a', title: 'XDairy | BackEnd', updatedAt: 1 }];
  assert.equal(pickSession(rows, 'xdairy | backend')?.id, 'a');
});

test('точное совпадение важнее вхождения', () => {
  const rows = [
    { id: 'long', title: 'XDairy | FrontEnd old', updatedAt: 99 },
    { id: 'exact', title: 'XDairy | FrontEnd', updatedAt: 1 },
  ];
  assert.equal(pickSession(rows, 'XDairy | FrontEnd')?.id, 'exact');
});

test('при равном счёте берётся более свежая сессия', () => {
  const rows = [
    { id: 'old', title: 'XDairy | BackEnd', updatedAt: 10 },
    { id: 'new', title: 'xDairy | backend', updatedAt: 50 },
  ];
  assert.equal(pickSession(rows, 'XDairy | BackEnd')?.id, 'new');
});

test('сессия не найдена -> undefined', () => {
  assert.equal(pickSession([{ id: 'a', title: 'XDairy | Arch' }], 'XDairy | FrontEnd'), undefined);
  assert.equal(pickSession([], 'XDairy | FrontEnd'), undefined);
});

test('render подставляет известные поля и не трогает неизвестные', () => {
  assert.equal(
    render('от {from}: «{text}» ({nope})', { from: 'BackEnd', text: 'привет' }),
    'от BackEnd: «привет» ({nope})',
  );
});
