/**
 * Поиск сессии по заголовку.
 *
 * Заголовок треда — единственный «адрес», который есть у роли: сессии
 * называются `XDairy | FrontEnd`, а в чате пишут `@FE`. Поэтому роль сначала
 * превращается в ожидаемый заголовок (шаблон или явное переопределение),
 * а затем сравнивается с тем, что отдаёт проекция сессий.
 *
 * Заголовок не уникален и может быть переименован руками — сравнение мягкое
 * (точное совпадение приоритетнее вхождения), а при нескольких кандидатах
 * побеждает самая свежая сессия.
 */

/** Сравнимая форма заголовка: регистр, ё, кавычки, лишние пробелы. */
export function normalizeTitle(value) {
  return String(value ?? '')
    .replace(/ё/g, 'е')
    .replace(/Ё/g, 'Е')
    .replace(/[«»"'`]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

/**
 * Ожидаемый заголовок сессии для роли.
 * @param {string} role ключ роли (имя файла чата без .md)
 * @param {object} options результат `normalizeOptions`
 */
export function wantedTitle(role, options) {
  const explicit = options.sessions?.[role];
  if (typeof explicit === 'string' && explicit.trim()) return explicit.trim();
  const title = options.roles?.[role]?.title ?? role;
  return String(options.titleTemplate ?? '{project} | {role}')
    .replace(/\{project\}/g, options.project ?? '')
    .replace(/\{role\}/g, title)
    .replace(/\{code\}/g, String(role).toUpperCase())
    .trim();
}

/**
 * @param {Array<{id: string, title?: string, updatedAt?: number}>} rows
 * @param {string} wanted
 * @returns {{ id: string, title?: string, updatedAt?: number } | undefined}
 */
export function pickSession(rows, wanted) {
  const target = normalizeTitle(wanted);
  if (!target) return undefined;
  const scored = [];
  for (const row of rows ?? []) {
    const title = normalizeTitle(row?.title);
    if (!title) continue;
    let score = 0;
    if (title === target) score = 100;
    else if (title.includes(target)) score = 60 + Math.round((target.length / Math.max(title.length, 1)) * 20);
    else if (target.includes(title) && title.length >= 4) score = 30;
    if (score > 0) scored.push({ row, score });
  }
  if (scored.length === 0) return undefined;
  scored.sort((left, right) => right.score - left.score || (right.row.updatedAt ?? 0) - (left.row.updatedAt ?? 0));
  return scored[0].row;
}

/**
 * Куда писать роли с учётом «памяти» моста.
 *
 * Порядок:
 * 1. помним прошлую сессию по id — переименование треда не сбивает адресацию;
 * 2. её нет среди живых (ушла в архив) — ищем по заголовку;
 * 3. ничего не нашли — сообщение останется в очереди.
 *
 * @param {string} role
 * @param {Array<{id: string, title?: string, updatedAt?: number}>} rows только живые сессии
 * @param {string|undefined} remembered id прошлой сессии
 * @param {object} options результат `normalizeOptions`
 * @returns {{ id: string, title?: string, moved: boolean, previous?: string } | undefined}
 */
export function chooseTarget(role, rows, remembered, options) {
  if (remembered && (rows ?? []).some((row) => row.id === remembered)) {
    return { id: remembered, moved: false };
  }
  const found = pickSession(rows, wantedTitle(role, options));
  if (!found) return undefined;
  return {
    id: found.id,
    title: found.title,
    moved: Boolean(remembered && remembered !== found.id),
    previous: remembered,
  };
}

/**
 * Все сессии, которые можно «оживить», с заголовками.
 *
 * Заголовки живут в проекции, а не в заголовке сессии, поэтому сервис проекции
 * необязателен: без него список будет без названий и мост просто никого не
 * найдёт. Архивные и субагентские сессии отбрасываются — писать в них нельзя.
 */
export async function collectSessions({ persistence, cache, archivedIds = [], logger }) {
  if (!persistence || typeof persistence.list !== 'function') {
    throw new Error('сервис sessionPersistence недоступен');
  }
  const listed = await persistence.list();
  const archived = new Set((archivedIds ?? []).map(String));
  const seen = new Set();
  const rows = [];

  for (const entry of listed ?? []) {
    const header = entry?.header ?? entry;
    const id = String(header?.id ?? '');
    if (!id || seen.has(id)) continue;
    seen.add(id);
    if (header?.origin === 'subagent') continue;
    if (archived.has(id)) continue;

    let hints;
    if (cache) {
      try {
        hints =
          (typeof cache.cachedSnapshot === 'function' ? cache.cachedSnapshot(header) : undefined) ??
          (typeof cache.cachedPredecessorTitle === 'function'
            ? cache.cachedPredecessorTitle(header)
            : undefined);
      } catch (error) {
        logger?.debug?.('[loop-chat] проекция сессии не отдала заголовок', String(error));
      }
    }
    const values = hints?.values ?? {};
    const title =
      typeof values.title === 'string' && values.title.trim() ? values.title.trim() : undefined;
    const lastPromptAt =
      typeof values.sessionListMetadata?.lastPromptAt === 'number'
        ? values.sessionListMetadata.lastPromptAt
        : 0;
    const createdAt = header?.createdAt ?? 0;
    rows.push({ id, title, cwd: header?.cwd, createdAt, updatedAt: Math.max(createdAt, lastPromptAt) });
  }

  rows.sort((left, right) => right.updatedAt - left.updatedAt);
  return rows;
}
