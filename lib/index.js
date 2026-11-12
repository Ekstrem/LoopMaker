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
import { appendFileSync, readFileSync } from 'node:fs';
import path from 'node:path';

import Schema from '@deepseek-ai/schemastery';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { normalizeOptions } from './config.js';
import { StateStore } from './state.js';
import { ChatBridge } from './bridge.js';
import { GitChat } from './git.js';
import { MergeService } from './merge.js';
import { TelegramNotifier } from './telegram.js';
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
  /** Порог похожести заголовка (0..1): прощает опечатки, но не «XDairy» вместо роли. */
  minTitleSimilarity: Schema.number().default(0.85),
  /** Что видит адресат: `{from}`, `{to}`, `{text}`, `{ts}`, `{file}`. */
  messageTemplate: Schema.string().default('сообщение от {from}: «{text}»'),
  /** `copy` — с текстом сообщения; `pointer` — только «в чате файл есть сообщение от X». */
  notifyStyle: Schema.string().default('copy'),
  pointerTemplate: Schema.string().default('в чате {files} есть сообщение от {from} ({ts}) — посмотри файл'),
  pointerBatchTemplate: Schema.string().default('в чате {files} есть сообщения от {from} — {count} шт. (последнее {ts}) — посмотри файл'),
  /** `skip` — не будить сессии историей на первом запуске, `deliver` — доставить всё. */
  history: Schema.string().default('skip'),
  /**
   * Как будить сессию: `live-only` — только открытый тред (агент создан хостом,
   * у него полный набор инструментов); `resume` — поднимать с диска (агент
   * собирается из контекста плагина и теряет инструменты — не использовать).
   */
  wake: Schema.string().default('live-only'),
  pollMs: Schema.number().default(5000),
  /** `@ALL` рассылать всем ролям, кроме автора. */
  broadcast: Schema.boolean().default(false),
  /** Только логировать доставку, не отправляя. */
  dryRun: Schema.boolean().default(false),
  /** Файл состояния (seen/pending). По умолчанию `$DSH_HOME/loopmaker-chat-state.json`. */
  stateFile: Schema.string().default(''),
  /** Дублировать лог моста в файл (удобно для отладки запуска). */
  logFile: Schema.string().default(''),
  pendingTtlMs: Schema.number().default(7200000),
  /** Разово сбросить счётчики кругов/привязки при старте (seen сохраняется). */
  freshStart: Schema.boolean().default(false),

  /* ---------------- Telegram-уведомления о новых сообщениях ---------------- */
  /** Присылать новые сообщения чата в Telegram. */
  /**
   * Тег человека в чате: `→ @EX`. Такого адресата нет среди сессий, поэтому
   * сообщение уходит в его Telegram-топик (см. `userTopic*`), а не в тред.
   */
  userTag: Schema.string().default(''),
  /** Chat id супергруппы, где живёт личный топик. */
  userTopicChatId: Schema.string().default(''),
  /** `message_thread_id` топика; `auto` — взять из привязок dsh-telegram. */
  userTopicThreadId: Schema.string().default('auto'),
  /** Переменная окружения с токеном бота, который пишет в топик. */
  userTopicTokenEnv: Schema.string().default('TELEGRAM_BOT_TOKEN'),
  /** Файл привязок dsh-telegram (для `userTopicThreadId: auto`). */
  userTopicBindingsFile: Schema.string().default(''),
  tgEnabled: Schema.boolean().default(false),
  /** Имя переменной окружения с токеном бота (из ~/.dsh/.env). */
  tgTokenEnv: Schema.string().default('LOOPMAKER_TG_TOKEN'),
  /** Chat id получателя; пусто — определится по первому сообщению боту (/start). */
  tgChatId: Schema.string().default(''),
  /** Как часто отдавать накопленное, минут. */
  tgEveryMinutes: Schema.number().default(2),
  /** Отправить раньше, если накопилось столько сообщений. */
  tgMaxBuffer: Schema.number().default(15),
  /** Заголовок сообщения. */
  tgTitle: Schema.string().default('Чат ролей'),
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
    'LoopMaker: в чате есть переписка {from} ↔ {to} — превышен лимит ({reason}). ' +
      'Смотри {files} (последнее сообщение {lastTs}). ' +
      'Разберись, пожалуйста: техническое — реши сам, бизнесовое — передай PO, ' +
      'архитектурное — AR.',
  ),
  /** Сколько пара заморожена после эскалации. */
  freezeMs: Schema.number().default(3600000),
});

/**
 * Публичная точка входа. Всё тело обёрнуто: ошибка плагина не должна мешать
 * DSH загрузиться — мост вспомогательный, ядро важнее.
 */
export function apply(ctx, config = {}) {
  try {
    return applyBridge(ctx, config);
  } catch (error) {
    console.error('[loop-chat] плагин не запустился:', error?.stack ?? error);
    return undefined;
  }
}

function applyBridge(ctx, config = {}) {
  const logger = makeLogger(ctx, String(config.logFile ?? '').trim());
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
    const agent = await ensureAgent(pluginCtx, ctx, sessionId, logger, options.wake);
    if (!agent) {
      // Штатно для live-only: тред закрыт, сообщение ждёт в очереди.
      const error = new Error(`сессия ${sessionId} не открыта`);
      error.code = 'NOT_LIVE';
      throw error;
    }
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
    await state.load();
    if (options.freshStart) {
      const cleared = state.resetRuntime();
      logger.info(
        `[loop-chat] freshStart: сброшено доставок ${cleared.deliveries}, привязок ${cleared.targets}, заморозок ${cleared.frozen}`,
      );
    }

    const notifier = createNotifier(ctx, logger, notifierOptions(ctx, options));
    const flushTimer = notifier
      ? setInterval(() => {
          // Пока чат не известен — пробуем узнать: пользователь мог написать /start.
          if (!notifier.chatId) void notifier.discover();
          void notifier.flush();
        }, 30000)
      : undefined;
    flushTimer?.unref?.();
    if (notifier) void notifier.discover();

    // Человек в чате (`→ @EX`): сессии у него нет — пишем в его Telegram-топик.
    const userRole = options.userTag ? options.userTag.toLowerCase() : '';
    const userSender = userRole ? createUserSender(logger, options) : undefined;
    if (userRole && !userSender) {
      logger.warn(`[loop-chat] адресат @${options.userTag} настроен, но топик не готов — сообщения будут ждать`);
    }

    const repoRoot = path.resolve(path.dirname(path.dirname(options.chatDir)));
    const chatRel = path.relative(repoRoot, options.chatDir).split(path.sep).join('/');
    const gitChat = new GitChat({ repo: repoRoot, chatRel, logger });

    let githubToken = options.githubToken;
    if (!githubToken && options.githubTokenFile) {
      try {
        const lines = readFileSync(options.githubTokenFile, 'utf8')
          .split(/\r?\n/)
          .map((s) => s.trim())
          .filter(Boolean);
        githubToken = lines[lines.length - 1] || '';
      } catch (error) {
        logger.warn('[loop-merge] токен не прочитан из файла', String(error?.message ?? error));
      }
    }
    const mergeSvc = options.mergeEnabled
      ? new MergeService({
          owner: options.githubOwner,
          repo: options.githubRepo,
          token: githubToken,
          requiredChecks: options.mergeRequiredChecks,
          logger,
        })
      : undefined;

    const bridge = new ChatBridge({
      options,
      logger,
      state,
      resolveSession,
      send,
      git: gitChat,
      merge: mergeSvc,
      userRole: userRole || undefined,
      onUserMessage: userSender,
      onMessages: notifier
        ? (items) => {
            notifier.push(items);
            void notifier.flush();
          }
        : undefined,
    });
    await bridge.start();

    // Карта ролей в лог: сразу видно, кто куда попал, без тестовых сообщений.
    setTimeout(() => {
      void reportMapping();
    }, 700)?.unref?.();

    return () => {
      bridge.stop();
      if (flushTimer) clearInterval(flushTimer);
      if (notifier) void notifier.flush({ force: true });
    };
  });

  /**
   * Отправка в личный топик человека (Telegram форум-группы).
   *
   * Тред берётся из привязок `dsh-telegram` (там личный топик отмечен
   * `kind: 'user'`) — так не нужно дублировать конфиг при пересоздании топика.
   */
  function createUserSender(logger, options) {
    const chatId = String(options.userTopicChatId ?? '').trim();
    if (!chatId) {
      logger.warn('[loop-chat] userTag задан, но userTopicChatId пуст');
      return undefined;
    }
    const envName = String(options.userTopicTokenEnv ?? 'TELEGRAM_BOT_TOKEN');
    const token = String(process.env[envName] ?? '').trim();
    if (!token) {
      logger.warn(`[loop-chat] нет токена ${envName} — личные сообщения не уйдут`);
      return undefined;
    }

    let cachedThread = options.userTopicThreadId === 'auto' ? '' : String(options.userTopicThreadId);
    let cachedAt = 0;

    const resolveThread = async () => {
      if (options.userTopicThreadId !== 'auto') return String(options.userTopicThreadId);
      if (cachedThread && Date.now() - cachedAt < 60000) return cachedThread;
      const file = String(options.userTopicBindingsFile ?? '').trim();
      if (!file) return '';
      try {
        const fs = await import('node:fs/promises');
        const raw = await fs.readFile(file, 'utf8');
        const data = JSON.parse(raw.replace(/^\uFEFF/, ''));
        const found = Object.values(data?.bindings ?? {}).find(
          (entry) => entry?.kind === 'user' && String(entry.chatId) === String(chatId),
        );
        cachedThread = found?.threadId ? String(found.threadId) : '';
        cachedAt = Date.now();
        return cachedThread;
      } catch {
        return '';
      }
    };

    return async (text) => {
      const threadId = await resolveThread();
      const body = {
        chat_id: chatId,
        text: String(text).slice(0, 3900),
        disable_web_page_preview: true,
      };
      if (threadId) body.message_thread_id = Number(threadId);
      const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      });
      const data = await response.json().catch(() => ({}));
      if (!data?.ok) throw new Error(data?.description ?? `Telegram HTTP ${response.status}`);
    };
  }

  /** Настройки уведомлений: токен берём из окружения (DSH грузит ~/.dsh/.env). */
  function notifierOptions(ctx, options) {
    if (!options.tgEnabled) return undefined;
    const envName = options.tgTokenEnv;
    const token = String(process.env[envName] ?? '').trim();
    if (!token) {
      logger.warn(
        `[loop-chat] telegram выключен: нет токена в переменной ${envName} (добавьте его в ~/.dsh/.env)`,
      );
      return undefined;
    }
    return {
      token,
      chatId: options.tgChatId,
      everyMinutes: options.tgEveryMinutes,
      maxBuffer: options.tgMaxBuffer,
      title: options.tgTitle,
    };
  }

  function createNotifier(ctx, logger, config) {
    if (!config) return undefined;
    logger.info(
      `[loop-chat] telegram включён: ${config.chatId ? `чат ${config.chatId}` : 'чат определится после /start'}, раз в ${config.everyMinutes} мин`,
    );
    return new TelegramNotifier({ ...config, logger });
  }

  async function reportMapping() {
    try {
      const rows = await liveSessions();
      if (rows.length === 0) {
        logger.warn('[loop-chat] сессии не прочитаны (sessionPersistence недоступен?)');
        return;
      }
      const parts = [];
      for (const role of Object.keys(options.roles)) {
        const chosen = chooseTarget(role, rows, state.targetFor(role), options);
        if (chosen) {
          state.setTarget(role, chosen.id);
          parts.push(`${role} → ${chosen.title ?? chosen.id}`);
        } else {
          parts.push(`${role} → НЕ НАЙДЕНА («${wantedTitle(role, options)}»)`);
        }
      }
      logger.info(`[loop-chat] роли: ${parts.join('; ')}`);
    } catch (error) {
      logger.warn('[loop-chat] карта ролей не построена', String(error));
    }
  }
}

/**
 * Живая сессия — из памяти.
 *
 * Поднимать тред с диска (`agents.resume`) нельзя: агент собирается из контекста
 * плагина, а инструменты (`read`, `glob`, `pwsh`, …) монтирует хост на контекст
 * сессии. Такой агент получает только глобальные инструменты и схлопывает
 * рабочий реестр треда (подробности — PLUGIN.md, «Почему не resume»).
 */
async function ensureAgent(pluginCtx, ctx, sessionId, logger, wake = 'live-only') {
  const live = pluginCtx.agents.get(sessionId);
  if (live) return live;

  if (wake !== 'resume') {
    logger.debug?.(`[loop-chat] сессия ${sessionId} закрыта — жду, пока её откроют`);
    return undefined;
  }

  logger.warn(
    `[loop-chat] resume ${sessionId}: агент получит только глобальные инструменты, ` +
      'реестр сессии может сломаться (wake: resume)',
  );

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
  return agent;
}

function archivedSessionIds(ctx) {
  try {
    return ctx.get('workspaceRegistry')?.archivedSessionIds ?? [];
  } catch {
    return [];
  }
}

/**
 * Логгер Harness плюс необязательная запись в файл.
 *
 * У DSH нет отдельного лог-файла, а для отладки моста нужно видеть, что именно
 * он делал после перезапуска приложения. Поэтому `logFile` дублирует строки в
 * файл (append), не заменяя обычный лог.
 */
function makeLogger(ctx, logFile) {
  let base;
  try {
    base = typeof ctx.logger === 'function' ? ctx.logger('dsh-loopmaker') : ctx.logger;
  } catch {
    base = undefined;
  }
  const writeFileLine = (levelName, args) => {
    if (!logFile) return;
    const text = args
      .map((arg) => (typeof arg === 'string' ? arg : safeStringify(arg)))
      .join(' ');
    try {
      appendFileSync(logFile, `${new Date().toISOString()} ${levelName} ${text}\n`, 'utf8');
    } catch {
      /* файл лога не критичен: мост должен работать и без него */
    }
  };
  const level = (levelName, fallback) => {
    const method = base?.[levelName];
    const write = (...args) => writeFileLine(levelName, args);
    if (typeof method === 'function') {
      return (...args) => {
        write(...args);
        return method(...args);
      };
    }
    return (...args) => {
      write(...args);
      return fallback('[loop-chat]', ...args);
    };
  };
  return {
    debug: level('debug', console.debug ?? console.log),
    info: level('info', console.log),
    warn: level('warn', console.warn),
    error: level('error', console.error),
  };
}

function safeStringify(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
