/**
 * Ни один файл репозитория не должен начинаться с BOM.
 *
 * Причина: pnpm/npm читают `package.json` строгим JSON.parse, который BOM не
 * принимает — установка плагина падает с «Unexpected token". PowerShell и часть
 * редакторов на Windows дописывают BOM молча, поэтому проверка живёт в тестах.
 */
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';

const ROOT = path.resolve(import.meta.dirname, '..');
const SKIP_DIRS = new Set(['.git', 'node_modules', '.obsidian']);
const EXTENSIONS = ['.js', '.mjs', '.json', '.yml', '.yaml', '.md', '.py', '.cmd', '.ps1'];

async function walk(dir) {
  const found = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      found.push(...(await walk(path.join(dir, entry.name))));
    } else if (EXTENSIONS.some((ext) => entry.name.endsWith(ext))) {
      found.push(path.join(dir, entry.name));
    }
  }
  return found;
}

test('в файлах репозитория нет BOM', async () => {
  const offenders = [];
  for (const file of await walk(ROOT)) {
    const bytes = await readFile(file);
    if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
      offenders.push(path.relative(ROOT, file));
    }
  }
  assert.deepEqual(offenders, [], `BOM найден: ${offenders.join(', ')}`);
});

test('package.json читается строгим JSON.parse', async () => {
  const raw = await readFile(path.join(ROOT, 'package.json'), 'utf8');
  assert.doesNotThrow(() => JSON.parse(raw));
});
