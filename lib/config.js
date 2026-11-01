/**
 * Конфигурация моста: значения по умолчанию, нормализация ролей и шаблонов.
 *
 * Всё, что приходит из `cordis.patch.yml`, — «сырое»: YAML не гарантирует ни
 * типов, ни регистра. Нормализуем один раз здесь, чтобы остальной код работал
 * с предсказуемыми структурами.
 */
import os from 'node:os';
import path from 'node:path';

/** Если в конфиге не перечислены роли — это набор петли XDiary. */
export const DEFAULT_ROLES = {
  be: 'BackEnd',
  fe: 'FrontEnd',
  do: 'DevOps',
  te: 'TestEngineer',
  ar: 'Architect',
  tl: 'TechLead',
  po: 'PO',
};

/** Файл состояния: `$DSH_HOME/loopmaker-chat-state.json` или `~/.dsh/...`. */
export function defaultStateFile() {
  const home =
    typeof process.env.DSH_HOME === 'string' && process.env.DSH_HOME.trim()
      ? process.env.DSH_HOME.trim()
      : path.join(os.homedir(), '.dsh');
  return path.join(home, 'loopmaker-chat-state.json');
}

/**
 * Роли из конфига: `{ fe: 'FrontEnd' }` или `{ fe: { title, tag } }`.
 * Ключ — имя файла чата без `.md`; `tag` — то, что стоит после `@`.
 */
export function normalizeRoles(raw) {
  const source =
    raw && typeof raw === 'object' && Object.keys(raw).length > 0 ? raw : DEFAULT_ROLES;
  const roles = {};
  for (const [file, value] of Object.entries(source)) {
    const key = String(file).toLowerCase().replace(/\.md$/, '').trim();
    if (!key) continue;
    if (typeof value === 'string') {
      const title = value.trim() || key;
      roles[key] = { title, tag: key.toUpperCase() };
    } else if (value && typeof value === 'object') {
      const title = String(value.title ?? key).trim() || key;
      roles[key] = { title, tag: String(value.tag ?? key).toUpperCase().trim() };
    }
  }
  return roles;
}

/** Тег -> ключ роли (`FE` -> `fe`). */
export function tagIndex(roles) {
  const index = new Map();
  for (const [key, role] of Object.entries(roles)) {
    index.set(role.tag.toUpperCase(), key);
  }
  return index;
}

/** `{project}`/`{role}` и прочие подстановки; неизвестные плейсхолдеры остаются. */
export function render(template, vars) {
  return String(template ?? '').replace(/\{(\w+)\}/g, (whole, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name] ?? '') : whole,
  );
}

/**
 * Привести сырой конфиг плагина к виду, с которым работает мост.
 *
 * @param {object} config — то, что пришло из `cordis.patch.yml`
 * @param {string} [baseDir] — относительно чего раскрывать `chatDir`
 */
export function normalizeOptions(config = {}, baseDir = process.cwd()) {
  const roles = normalizeRoles(config.roles);
  const chatDirRaw = String(config.chatDir ?? '').trim();
  const chatDir = chatDirRaw
    ? path.isAbsolute(chatDirRaw)
      ? path.normalize(chatDirRaw)
      : path.resolve(baseDir, chatDirRaw)
    : '';

  const asNumber = (value, fallback, { allowZero = false } = {}) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    if (parsed > 0) return parsed;
    return allowZero && parsed === 0 ? 0 : fallback;
  };

  const history = String(config.history ?? 'skip').trim().toLowerCase();

  return {
    enabled: config.enabled !== false,
    chatDir,
    project: String(config.project ?? '').trim(),
    roles,
    tags: tagIndex(roles),
    sessions: config.sessions && typeof config.sessions === 'object' ? config.sessions : {},
    titleTemplate: String(config.titleTemplate ?? '{project} | {role}'),
    messageTemplate: String(config.messageTemplate ?? 'сообщение от {from}: «{text}»'),
    history: history === 'deliver' ? 'deliver' : 'skip',
    pollMs: asNumber(config.pollMs, 5000),
    broadcast: config.broadcast === true,
    dryRun: config.dryRun === true,
    stateFile: String(config.stateFile ?? '').trim() || defaultStateFile(),
    pendingTtlMs: asNumber(config.pendingTtlMs, 30 * 60 * 1000),
    maxAttempts: asNumber(config.maxAttempts, 20),

    /* ---- тормоза ---- */
    // Пачка копится, пока последнее сообщение для адресата моложе окна:
    // всплеск из пяти строк уходит одной репликой, а не пятью ходами.
    batchWindowMs: asNumber(config.batchWindowMs, 0, { allowZero: true }),
    batchHeader: String(config.batchHeader ?? 'сообщения из чата петли ({count}, последнее от {from}):'),
    // Сколько раз одна пара «A -> B» может доставить за окно; дальше — эскалация.
    maxHops: asNumber(config.maxHops, 4),
    hopWindowMs: asNumber(config.hopWindowMs, 30 * 60 * 1000),
    // Потолок входящих реплик на роль за час.
    maxPerHour: asNumber(config.maxPerHour, 40),
    // Кто разбирает переписку после порога (по умолчанию координатор — TechLead).
    escalateTo: String(config.escalateTo ?? 'tl').trim() || 'tl',
    escalationTemplate: String(
      config.escalationTemplate ??
        'LoopMaker: переписка {from} ↔ {to} превысила лимит ({reason}). ' +
          'Разберись, пожалуйста: техническое — реши сам, бизнесовое — передай PO, ' +
          'архитектурное — AR. Последние сообщения:\n{messages}',
    ),
    // Сколько пара остаётся замороженной после эскалации.
    freezeMs: asNumber(config.freezeMs, 60 * 60 * 1000),
  };
}
