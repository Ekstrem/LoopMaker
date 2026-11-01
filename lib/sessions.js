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
/** Расстояние Левенштейна: на нём держится прощение опечаток в заголовках. */
function levenshtein(left, right) {
  const rows = left.length + 1;
  const cols = right.length + 1;
  const grid = Array.from({ length: rows }, (_, i) => [i, ...new Array(cols - 1).fill(0)]);
  for (let j = 1; j < cols; j += 1) grid[0][j] = j;
  for (let i = 1; i < rows; i += 1) {
    for (let j = 1; j < cols; j += 1) {
      const cost = left[i - 1] === right[j - 1] ? 0 : 1;
      grid[i][j] = Math.min(grid[i - 1][j] + 1, grid[i][j - 1] + 1, grid[i - 1][j - 1] + cost);
    }
  }
  return grid[rows - 1][cols - 1];
}

/**
 * Сокращение вместо полного слова: «XDairy | Arch» ↔ «XDairy | Architect».
 *
 * Сравниваются сегменты вокруг `|`: все, кроме последнего, должны совпасть,
 * а последний — быть префиксом другого (не короче 3 символов). Это ловит
 * «Arch/Architect», но не путает «BackEnd/FrontEnd».
 */
export function segmentPrefixMatch(left, right) {
  const a = normalizeTitle(left).split('|').map((part) => part.trim());
  const b = normalizeTitle(right).split('|').map((part) => part.trim());
  if (a.length !== b.length || a.length === 0) return false;
  for (let index = 0; index < a.length - 1; index += 1) {
    if (a[index] !== b[index]) return false;
  }
  const [tailA, tailB] = [a[a.length - 1], b[b.length - 1]];
  if (!tailA || !tailB) return false;
  if (tailA === tailB) return true;
  const [short, long] = tailA.length <= tailB.length ? [tailA, tailB] : [tailB, tailA];
  return short.length >= 3 && long.startsWith(short);
}

/**
 * Похожесть заголовков от 0 до 1.
 *
 * Нужна из-за опечаток: сессия «XDairy | TestEngeneer» должна находиться по
 * шаблону «XDairy | TestEngineer», но «XDairy» не должен считаться ею же.
 */
export function titleSimilarity(left, right) {
  const a = normalizeTitle(left);
  const b = normalizeTitle(right);
  if (!a || !b) return 0;
  if (a === b) return 1;
  return 1 - levenshtein(a, b) / Math.max(a.length, b.length);
}

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
/**
 * Подходит ли заголовок под ожидаемый: равенство, вхождение или похожесть.
 * Та же логика, что в `pickSession`, вынесена отдельно — ею проверяется
 * запомненный таргет (архив, переименование, ошибочный прошлый матч).
 */
export function titleMatches(title, wanted, minSimilarity = 0.85) {
  const a = normalizeTitle(title);
  const b = normalizeTitle(wanted);
  if (!a || !b) return false;
  if (a === b || a.includes(b)) return true;
  if (segmentPrefixMatch(a, b)) return true;
  return titleSimilarity(a, b) >= minSimilarity;
}

export function pickSession(rows, wanted, options = {}) {
  const target = normalizeTitle(wanted);
  if (!target) return undefined;
  const minSimilarity = Number(options.minSimilarity) > 0 ? Number(options.minSimilarity) : 0.85;
  const scored = [];
  for (const row of rows ?? []) {
    const title = normalizeTitle(row?.title);
    if (!title) continue;
    let score = 0;
    if (title === target) {
      score = 100;
    } else if (title.includes(target)) {
      // «XDairy | FrontEnd (v2)» — тот же тред с уточнением в названии
      score = 60 + Math.round((target.length / Math.max(title.length, 1)) * 20);
    } else if (segmentPrefixMatch(title, target)) {
      // «XDairy | Arch» для роли Architect
      score = 70;
    } else {
      // Обратного вхождения здесь принципиально нет: иначе «XDairy» подходил бы
      // под любую роль. Опечатка прощается только похожестью.
      const similarity = titleSimilarity(title, target);
      if (similarity >= minSimilarity) score = 40 + Math.round(similarity * 40);
    }
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
  if (remembered) {
    const row = (rows ?? []).find((candidate) => candidate.id === remembered);
    // Помним id, но только пока заголовок соответствует роли: иначе однажды
    // неверно сматченный тред («XDairy» вместо «XDairy | TestEngineer») залип бы
    // навсегда, а переименованный с сохранением имени роли — переживает рестарт.
    if (row && titleMatches(row.title, wantedTitle(role, options), options.minTitleSimilarity)) {
      return { id: remembered, title: row.title, moved: false };
    }
  }
  const found = pickSession(rows, wantedTitle(role, options), {
    minSimilarity: options.minTitleSimilarity,
  });
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
