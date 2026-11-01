/**
 * dsh-loopmaker — мост «outbox-чат петли → сессия роли».
 *
 * Зачем: чат ролей лежит файлами, и чтобы роль узнала о сообщении, её тред надо
 * разбудить. Плагин делает это сам: находит в чате строку
 * `- [ts] → @FE: текст`, определяет по заголовку сессию FE и доставляет в неё
 * сообщение «соответствующее от <автор>: «текст»» через `agent.followup`.
 *
 * Монтаж (профиль `desktop`):
 *
 *     dsh plugin --profile desktop add github:Ekstrem/LoopMaker
 *
 * затем `dsh-loopmaker` в `dsh.profile.bundles` и настройки в профильном
 * `cordis.patch.yml` (см. README).
 */
import Schema from '@deepseek-ai/schemastery';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { normalizeOptions } from './config.js';
import { StateStore } from './state.js';
import { ChatBridge } from './bridge.js';
import { chooseTarget, collectSessions, wantedTitle } from './sessions.js';

export const name = 'dsh-loopmaker';

/**
 * Сервисы, без которых мост бессмысленен:
 * - `agents` — оживить сессию (`get`/`resume`) и отдать сообщение (`followup`);
 * - `workspaceRegistry` — знать архивные сессии (в них писать нельзя).
 *
 * `sessionPersistence` и `sessionProjectionCache` резолвятся лениво: без них
 * плагин не падает на старте, а честно пишет в лог, что сессию не найти.
 */
export const inject = ['agents', 'workspaceRegistry'];

export const Config = Schema.object({
  /** Выключить мост, не убирая плагин из профиля. */
  enabled: Schema.boolean().default(true),
  /** Папка outbox-чата. Абсолютный путь надёжнее относительного. */
  chatDir: Schema.string().default(''),
  /** Относительный `chatDir` раскрывается от этой папки. */
  workspaceRoot: Schema.string().default(''),
  /** Префикс заголовков сессий: `XDairy | BackEnd`. */
  project: Schema.string().default(''),
  /** Шаблон заголовка: `{project}`, `{role}`, `{code}`. */
  titleTemplate: Schema.string().default('{project} | {role}'),
  /** Файл чата -> роль (строка) или `{ title, tag }`. */
  roles: Schema.any().default({}),
  /** Явные заголовки сессий по роли, если шаблон не подходит. */
  sessions: Schema.any().default({}),
  /** Что видит адресат: `{from}`, `{to}`, `{text}`, `{ts}`, `{file}`. */
  messageTemplate: Schema.string().default('сообщение от {from}: «{text}»'),
  /** `skip` — не будить сессии историей на первом запуске, `deliver` — доставить всё. */
  history: Schema.string().default('skip'),
  pollMs: Schema.number().default(5000),
  /** `@ALL` рассылать всем ролям, кроме автора. */
  broadcast: Schema.boolean().default(false),
  /** Только логировать доставку, не отправляя. */
  dryRun: Schema.boolean().default(false),
  /** Файл состояния (seen/pending). По умолчанию `$DSH_HOME/loopmaker-chat-state.json`. */
  stateFile: Schema.string().default(''),
  pendingTtlMs: Schema.number().default(7200000),
  maxAttempts: Schema.number().default(20),

  /** Дебаунс: сколько ждать «успокоения» пачки перед доставкой (0 — сразу). */
  batchWindowMs: Schema.number().default(0),
  /** Заголовок склеенной пачки сообщений. */
  batchHeader: Schema.string().default('сообщения из чата петли ({count}, последнее от {from}):'),
  /** Лимит кругов одной пары «A -> B» за окно. */
  maxHops: Schema.number().default(4),
  hopWindowMs: Schema.number().default(1800000),
  /** Потолок входящих реплик роли за час. */
  maxPerHour: Schema.number().default(40),
  /** Кому передаётся переписка после порога (по умолчанию координатор). */
  escalateTo: Schema.string().default('tl'),
  /** Текст эскалации: {from} {to} {reason} {count} {messages}. */
  escalationTemplate: Schema.string().default(
    'LoopMaker: переписка {from} ↔ {to} превысила лимит ({reason}). ' +
      'Разберись, пожалуйста: техническое — реши сам, бизнесовое — передай PO, ' +
      'архитектурное — AR. Последние сообщения:\n{messages}',
  ),
  /** Сколько пара заморожена после эскалации. */
  freezeMs: Schema.number().default(3600000),
});

export function apply(ctx, config = {}) {
  const logger = makeLogger(ctx);
  const workspaceRoot = String(config.workspaceRoot ?? '').trim() || process.cwd();
  const options = normalizeOptions(config, workspaceRoot);

  if (!options.enabled) return;
  if (!options.chatDir) {
    logger.warn('[loop-chat] chatDir не задан — мост выключен (см. cordis.patch.yml)');
    return;
  }

  /**
   * Контекст плагина, а НЕ контекст эффекта: `agents.resume()` привязывает
   * агента к вызвавшему файберу, и агент из короткоживущего эффекта молча
   * теряет сообщения.
   */
  const pluginCtx = ctx;

  let persistence;
  ctx.inject(['sessionPersistence'], (childCtx) => {
    persistence = childCtx.sessionPersistence;
    return () => {
      persistence = undefined;
    };
  });

  let projection;
  ctx.inject(['sessionProjectionCache'], (childCtx) => {
    projection = childCtx.sessionProjectionCache;
    return () => {
      projection = undefined;
    };
  });

  const state = new StateStore({ file: options.stateFile, logger });

  /**
   * Список живых сессий кэшируется внутри одного прохода: доставка пачки и
   * эскалация спрашивают его несколько раз, а меняется он нечасто. Кэш короткий,
   * поэтому архив и переименования подхватываются почти сразу.
   */
  const sessionCache = { at: 0, rows: [] };
  const cacheTtlMs = Math.min(Math.max(options.pollMs, 500), 2000);

  const liveSessions = async () => {
    const now = Date.now();
    if (now - sessionCache.at < cacheTtlMs && sessionCache.rows.length > 0) return sessionCache.rows;
    const rows = await collectSessions({
      persistence,
      cache: projection,
      archivedIds: archivedSessionIds(ctx),
      logger,
    });
    sessionCache.at = now;
    sessionCache.rows = rows;
    return rows;
  };

  /**
   * Куда писать роли.
   *
   * Порядок: помним прошлую сессию по id (переименование треда не сбивает
   * адресацию) → если её больше нет среди живых (архив), ищем по заголовку и
   * сообщаем о переезде. Архивные сессии в список не попадают, поэтому
   * «доставить в архив» невозможно по конструкции.
   */
  const resolveSession = async (role) => {
    const rows = await liveSessions();
    const remembered = state.targetFor(role);
    const chosen = chooseTarget(role, rows, remembered, options);
    if (!chosen) {
      if (remembered) {
        logger.info?.(
          `[loop-chat] роль ${role}: сессия ${remembered} заархивирована, замена «${wantedTitle(role, options)}» ещё не появилась`,
        );
      }
      return undefined;
    }
    if (chosen.moved) {
      logger.info?.(
        `[loop-chat] роль ${role}: таргет переехал ${chosen.previous} → ${chosen.id} (${chosen.title ?? 'без названия'})`,
      );
    }
    state.setTarget(role, chosen.id);
    return chosen.id;
  };

  const send = async (sessionId, text) => {
    const agent = await ensureAgent(pluginCtx, ctx, sessionId, logger);
    agent.followup(
      createUserMessage({
        content: [{ type: 'text', text }],
        source: { kind: 'user' },
      }),
    );
  };

  ctx.effect(async () => {
    logger.info(
      `[loop-chat] чат: ${options.chatDir}; сессии по шаблону «${options.titleTemplate}»` +
        `${options.dryRun ? ' (dry-run)' : ''}`,
    );
    const bridge = new ChatBridge({ options, logger, state, resolveSession, send });
    await bridge.start();
    return () => bridge.stop();
  });
}

/** Живая сессия — из памяти, иначе поднять с диска. */
async function ensureAgent(pluginCtx, ctx, sessionId, logger) {
  const live = pluginCtx.agents.get(sessionId);
  if (live) return live;

  let selection;
  try {
    selection = ctx.get('agentDefaultModel')?.currentSelection?.();
  } catch {
    selection = undefined;
  }

  const handle = await pluginCtx.agents.resume({
    resumeSessionId: sessionId,
    ...(selection ? { agentOptions: { ...selection } } : {}),
  });
  const agent = handle?.agent ?? handle;
  if (!agent) throw new Error(`агент сессии ${sessionId} не поднялся`);
  logger.debug?.(`[loop-chat] сессия ${sessionId} поднята с диска`);
  return agent;
}

function archivedSessionIds(ctx) {
  try {
    return ctx.get('workspaceRegistry')?.archivedSessionIds ?? [];
  } catch {
    return [];
  }
}

/** Привести логгер Harness к четырём методам: фабрика может отсутствовать. */
function makeLogger(ctx) {
  let base;
  try {
    base = typeof ctx.logger === 'function' ? ctx.logger('dsh-loopmaker') : ctx.logger;
  } catch {
    base = undefined;
  }
  const level = (levelName, fallback) => {
    const method = base?.[levelName];
    if (typeof method === 'function') return method.bind(base);
    return (...args) => fallback('[loop-chat]', ...args);
  };
  return {
    debug: level('debug', console.debug ?? console.log),
    info: level('info', console.log),
    warn: level('warn', console.warn),
    error: level('error', console.error),
  };
}
