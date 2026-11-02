/**
 * Мост «outbox-чат → сессии ролей».
 *
 * Один проход: прочитать файлы чата, найти новые строки, поставить адресованные
 * в очередь и попробовать доставить. Логика намеренно не знает ни про DSH, ни
 * про файловую систему: `resolveSession` и `send` приходят снаружи, поэтому
 * маршрутизацию можно проверить тестом без поднятого Harness.
 */
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { messageKey, parseChat, stripBom } from './parse.js';
import { render } from './config.js';

const SKIP = new Set(['readme.md', '_dialogue.md']);

const truncate = (text, limit = 120) =>
  text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;

export class ChatBridge {
  /**
   * @param {object} deps
   * @param {object} deps.options результат `normalizeOptions`
   * @param {{info: Function, warn: Function, error: Function, debug?: Function}} deps.logger
   * @param {import('./state.js').StateStore} deps.state
   * @param {(role: string) => Promise<string | undefined>} deps.resolveSession
   * @param {(sessionId: string, text: string) => Promise<void>} deps.send
   * @param {() => number} [deps.now]
   * @param {{ readdir: Function, readFile: Function }} [deps.fs]
   */
  constructor(deps) {
    this.options = deps.options;
    this.logger = deps.logger;
    this.state = deps.state;
    this.resolveSession = deps.resolveSession;
    this.send = deps.send;
    this.now = deps.now ?? (() => Date.now());
    this.fs = deps.fs ?? { readdir, readFile };
    /** Куда отдавать новые сообщения (например, в Telegram-уведомления). */
    this.onMessages = deps.onMessages;
    /** Адресат-человек (`→ @EX`): у него нет сессии, сообщение идёт в его топик. */
    this.userRole = deps.userRole;
    this.onUserMessage = deps.onUserMessage;
    this.newMessages = [];
    this.timer = undefined;
    this.stopped = true;
    this.running = false;
    /** История просмотрена в этом запуске процесса (см. poll). */
    this.seededThisBoot = false;
    /** Когда последний раз сообщали, что тред закрыт (чтобы не спамить в лог). */
    this.notLiveLoggedAt = new Map();
    /** Когда последний раз писали про паузу пары. */
    this.frozenLoggedAt = new Map();
  }

  async start() {
    await this.state.load();
    this.stopped = false;
    await this.safePoll();
    this.schedule();
    return this;
  }

  stop() {
    this.stopped = true;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
  }

  schedule() {
    if (this.stopped) return;
    this.timer = setTimeout(async () => {
      await this.safePoll();
      this.schedule();
    }, this.options.pollMs);
    // Не держим процесс живым только из-за опроса.
    this.timer?.unref?.();
  }

  /** Ошибка одного прохода не должна убивать мост. */
  async safePoll() {
    if (this.running) return { skipped: true };
    this.running = true;
    try {
      return await this.poll();
    } catch (error) {
      this.logger.error?.('[loop-chat] проход упал', String(error?.stack ?? error));
      return { failed: true };
    } finally {
      this.running = false;
    }
  }

  async poll() {
    const stats = { scanned: 0, enqueued: 0, delivered: 0, escalated: 0, pending: 0, seeded: false };
    const files = await this.listFiles();

    for (const file of files) {
      const key = file.replace(/\.md$/i, '').toLowerCase();
      // Файлы на `_` — служебные: их пишут не роли, а инструменты (например,
      // линтер чата кладёт в `_lint.md` замечания авторам). Автор — LoopMaker,
      // поэтому адресат не совпадает с «собой» и уведомление доставляется.
      const system = key.startsWith('_');
      const role = system ? { title: 'LoopMaker' } : this.options.roles[key];
      if (!role) {
        // Новый outbox без записи в `roles` раньше пропадал молча: файл есть,
        // сообщения в нём есть, а в логе — тишина. Пишем предупреждение один раз
        // за жизнь процесса, чтобы это было видно.
        if (!this.warnedUnknownFiles) this.warnedUnknownFiles = new Set();
        const id = file.toLowerCase();
        if (!this.warnedUnknownFiles.has(id)) {
          this.warnedUnknownFiles.add(id);
          this.logger.warn?.(
            '[loop-chat] файл без роли — пропускаю',
            `${file}: нет в roles; добавьте в cordis.patch.yml (roles) и перезапустите DSH`,
          );
        }
        continue;
      }
      stats.scanned += 1;
      stats.enqueued += await this.scanFile(file, key, role);
    }

    // Первый проход целиком по всем файлам: только после него история считается
    // просмотренной, иначе последние файлы «проснули» бы роли старыми строками.
    if (!this.seededThisBoot) {
      this.seededThisBoot = true;
      stats.seeded = true;
    }
    if (!this.state.isSeeded()) this.state.markSeeded();

    if (this.newMessages.length > 0 && typeof this.onMessages === 'function') {
      const batch = this.newMessages;
      this.newMessages = [];
      try {
        this.onMessages(batch);
      } catch (error) {
        this.logger.warn?.('[loop-chat] уведомление не отправлено', String(error));
      }
    }

    const pendingStats = await this.retryPending();
    stats.delivered = pendingStats.delivered;
    stats.escalated = pendingStats.escalated;
    stats.pending = pendingStats.left;
    // Один сброс на проход: иначе первое чтение большого чата писало бы файл
    // состояния на каждую строку.
    await this.state.flush();
    return stats;
  }

  async listFiles() {
    try {
      const entries = await this.fs.readdir(this.options.chatDir);
      return entries
        .filter((name) => typeof name === 'string' && name.toLowerCase().endsWith('.md'))
        .filter((name) => !SKIP.has(name.toLowerCase()))
        .sort();
    } catch (error) {
      this.logger.warn?.(`[loop-chat] папка чата недоступна: ${this.options.chatDir}`, String(error));
      return [];
    }
  }

  async scanFile(file, key, role) {
    const full = path.join(this.options.chatDir, file);
    let text;
    try {
      text = stripBom(await this.fs.readFile(full, 'utf8'));
    } catch (error) {
      this.logger.warn?.(`[loop-chat] не прочитан ${full}`, String(error));
      return 0;
    }

    let enqueued = 0;
    for (const message of parseChat(text)) {
      const keyOfMessage = messageKey(key, message);
      if (this.state.isSeen(keyOfMessage)) continue;

      // История в файле — это «что уже было», а не «что случилось»: при
      // каждом старте DSH всё существующее помечается просмотренным.
      // Иначе перезапуск приложения будил бы роли всей перепиской заново.
      if (this.options.history === 'skip' && !this.seededThisBoot) {
        this.state.remember(keyOfMessage);
        continue;
      }

      const targets = this.targetsFor(message.recipients ?? [message.to], key);
      if (targets.length === 0) {
        this.state.remember(keyOfMessage);
        continue;
      }

      // Уведомления получают все новые строки, а не только адресованные роли.
      this.newMessages.push({
        key: keyOfMessage,
        ts: message.ts,
        from: role.title,
        to: (message.recipients ?? [message.to]).join(' + ').toUpperCase(),
        text: message.text,
      });

      for (const target of targets) {
        const added = this.state.enqueue({
          key: keyOfMessage,
          target,
          file: key,
          from: role.title,
          fromRole: key,
          text: message.text,
          ts: message.ts,
        });
        if (added) enqueued += 1;
      }
      this.state.remember(keyOfMessage);
    }
    return enqueued;
  }

  /**
   * Кому адресована строка.
   *
   * `recipients` — список адресатов из строки (`@DO + @TE` -> два). Раньше здесь
   * был один тег, и мультиадресатные строки терялись целиком.
   */
  targetsFor(recipients, senderRole) {
    const list = Array.isArray(recipients) ? recipients : [recipients];
    const targets = new Set();
    for (const tag of list) {
      for (const target of this.singleTargets(tag, senderRole)) targets.add(target);
    }
    return [...targets];
  }

  /** Один тег -> роли (`@ALL` раскрывается, только если включён broadcast). */
  singleTargets(tag, senderRole) {
    const wanted = String(tag ?? '').toUpperCase();
    if (!wanted) return [];
    if (wanted === 'ALL') {
      if (!this.options.broadcast) return [];
      return Object.keys(this.options.roles).filter((role) => role !== senderRole);
    }
    const role = this.options.tags.get(wanted);
    if (!role || role === senderRole) return [];
    return [role];
  }

  /**
   * Разослать очередь. Доставка идёт **пачкой на адресата**, а не по строке:
   * роль, которой написали пять раз за проход, просыпается один раз.
   */
  async retryPending() {
    const now = this.now();
    const byTarget = new Map();

    for (const item of this.state.pendingList()) {
      if (now - (item.firstAt ?? 0) > this.options.pendingTtlMs) {
        this.logger.warn?.(
          `[loop-chat] сообщение для ${item.target} устарело за ${Math.round(this.options.pendingTtlMs / 60000)} мин: ${truncate(item.text, 80)}`,
        );
        this.state.drop(item.key, item.target);
        continue;
      }
      if ((item.attempts ?? 0) >= this.options.maxAttempts) {
        this.logger.warn?.(
          `[loop-chat] сообщение для ${item.target} не доставлено за ${item.attempts} попыток: ${truncate(item.text, 80)}`,
        );
        this.state.drop(item.key, item.target);
        continue;
      }
      const list = byTarget.get(item.target) ?? [];
      list.push(item);
      byTarget.set(item.target, list);
    }

    let delivered = 0;
    let escalated = 0;

    for (const [target, items] of byTarget) {
      // Дебаунс: ждём, пока пачка перестанет пополняться.
      if (this.options.batchWindowMs > 0) {
        const freshest = Math.max(...items.map((item) => item.firstAt ?? 0));
        if (now - freshest < this.options.batchWindowMs) continue;
      }

      // Пара уже отправлена координатору — не подливаем масла, пока идёт разбор.
      if (this.isFrozen(items, target, now)) continue;

      // Координатора лимитировать нельзя: его сообщения и есть развязка,
      // иначе эскалация эскалировала бы саму себя.
      const reason = target === this.options.escalateTo ? undefined : this.overLimit(items, target, now);
      if (reason) {
        await this.escalate(target, items, reason);
        escalated += 1;
        continue;
      }

      if (await this.deliverBatch(target, items)) delivered += 1;
    }

    return { delivered, escalated, left: this.state.pendingList().length };
  }

  pairKey(fromRole, target) {
    return `${fromRole || 'unknown'}->${target}`;
  }

  isFrozen(items, target, now) {
    for (const item of items) {
      const pair = this.pairKey(item.fromRole, target);
      const until = this.state.frozenUntil(pair);
      if (until > now) {
        // Пишем редко: иначе каждые 5 секунд одна и та же строка.
        if (now - (this.frozenLoggedAt.get(pair) ?? 0) > 60000) {
          this.frozenLoggedAt.set(pair, now);
          this.logger.info?.(
            `[loop-chat] пара ${pair} на паузе до ${new Date(until).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })} (эскалация координатору)`,
          );
        }
        return true;
      }
    }
    return false;
  }

  /**
   * Пора ли звать третью сторону.
   *
   * Два независимых порога:
   * - `maxHops` — сколько раз одна пара «A -> B» доставила за `hopWindowMs`
   *   (это и есть счёт «кругов» переписки);
   * - `maxPerHour` — общий потолок входящих реплик роли.
   *
   * Пары с участием координатора из счёта кругов исключены: координатор по роли
   * раздаёт задачи всем, и «4 сообщения за 30 минут» для него — норма, а не
   * пинг-понг. Иначе тормоз глушит именно того, кто ведёт работу.
   */
  overLimit(items, target, now) {
    const coordinator = this.options.escalateTo;
    const hopSince = now - this.options.hopWindowMs;

    for (const item of items) {
      const from = item.fromRole || item.from;
      if (from === coordinator || target === coordinator) continue;
      const count = this.state.countDeliveries(from, target, hopSince);
      if (count >= this.options.maxHops) {
        return { kind: 'hops', count, from, fromRole: from };
      }
    }

    const hourSince = now - 60 * 60 * 1000;
    const incoming = this.state.countIncoming(target, hourSince);
    if (incoming >= this.options.maxPerHour) {
      return {
        kind: 'budget',
        count: incoming,
        from: items[0].from,
        fromRole: items[0].fromRole,
      };
    }
    return undefined;
  }

  /**
   * Передать переписку координатору (по умолчанию TechLead) и заморозить пару.
   *
   * Исходные сообщения из очереди снимаются: дальше их судьбу решает человек —
   * техническое решает сам координатор, бизнесовое уходит PO, архитектурное — AR.
   */
  async escalate(target, items, reason) {
    const escalateRole = this.options.escalateTo;
    const toTitle = this.options.roles?.[target]?.title ?? target;
    const fromTitle = items[0].from;
    const reasonText =
      reason.kind === 'hops'
        ? `${reason.count} кругов за ${Math.round(this.options.hopWindowMs / 60000)} мин`
        : `${reason.count} реплик за час`;

    // Указатель, а не пересказ: координатор читает чат сам — там источник правды,
    // и текст не расходится с файлом. `{messages}` остаётся доступным для тех,
    // кому всё же нужен дамп переписки в самом уведомлении.
    const files = [...new Set([...items.map((item) => item.file), target])]
      .map((file) => `${file}.md`)
      .join(', ');
    const messages = items.map((item) => `- [${item.ts}] ${item.from}: ${item.text}`).join('\n');
    const text = render(this.options.escalationTemplate, {
      from: fromTitle,
      to: toTitle,
      fromRole: String(reason.fromRole ?? '').toUpperCase(),
      toRole: String(target).toUpperCase(),
      files,
      reason: reasonText,
      count: items.length,
      lastTs: items[items.length - 1].ts,
      messages,
    });

    this.state.enqueue({
      key: `escalation|${target}|${reason.kind}|${this.now()}`,
      target: escalateRole,
      file: this.options.roles?.[escalateRole] ? escalateRole : 'loopmaker',
      from: 'LoopMaker',
      fromRole: 'loopmaker',
      text,
      raw: true,
      ts: new Date(this.now()).toISOString().slice(0, 16).replace('T', ' '),
    });

    for (const item of items) {
      this.state.drop(item.key, item.target);
      this.state.freeze(
        this.pairKey(item.fromRole, target),
        this.now() + this.options.freezeMs,
      );
    }

    this.logger.warn?.(
      `[loop-chat] ${fromTitle} ↔ ${toTitle}: ${reasonText} — передано координатору (${escalateRole}), пара заморожена на ${Math.round(this.options.freezeMs / 60000)} мин`,
    );
    return true;
  }

  /** Текст для человека: с самим сообщением, а не указателем на файл. */
  buildUserText(items) {
    return items
      .map((item) => {
        const from = item.fromRole ? String(item.fromRole).toUpperCase() : String(item.from ?? '?');
        return `[${item.ts}] ${from}: ${item.text}`;
      })
      .join('\n');
  }

  async deliverBatch(target, items) {
    // Адресат-человек (`→ @EX`): у него нет сессии — сообщение идёт в его топик.
    if (this.userRole && target === this.userRole && typeof this.onUserMessage === 'function') {
      const text = this.buildUserText(items);
      if (this.options.dryRun) {
        this.logger.info?.(`[loop-chat][dry-run] → человеку (${target}): ${truncate(text)}`);
        this.closeItems(target, items);
        return true;
      }
      try {
        await this.onUserMessage(text, items);
        this.closeItems(target, items);
        this.logger.info?.(`[loop-chat] → ${target} (личный топик): ${truncate(text)}`);
        return true;
      } catch (error) {
        for (const item of items) this.state.attempted(item);
        this.logger.error?.('[loop-chat] личный топик недоступен', String(error));
        return false;
      }
    }

    let sessionId;
    try {
      sessionId = await this.resolveSession(target);
    } catch (error) {
      for (const item of items) this.state.attempted(item);
      this.logger.warn?.(`[loop-chat] не удалось найти сессию роли ${target}`, String(error));
      return false;
    }

    if (!sessionId) {
      const attempts = this.state.attempted(items[0]);
      for (const item of items.slice(1)) this.state.attempted(item);
      if (attempts === 1 || attempts % 10 === 0) {
        this.logger.warn?.(
          `[loop-chat] сессия «${this.wantedTitleOf(target)}» не найдена (попытка ${attempts}); ${items.length} сообщ. ждут в очереди`,
        );
      }
      return false;
    }

    const text = this.buildText(target, items);

    if (this.options.dryRun) {
      this.logger.info?.(`[loop-chat][dry-run] ${target} (${sessionId}): ${truncate(text)}`);
      this.closeItems(target, items);
      return true;
    }

    try {
      await this.send(sessionId, text);
    } catch (error) {
      if (error?.code === 'NOT_LIVE') {
        // Тред закрыт — это ожидание, а не сбой доставки: попытки не жжём,
        // иначе сообщение сгорит по maxAttempts раньше, чем тред откроют.
        const now = this.now();
        if (now - (this.notLiveLoggedAt.get(target) ?? 0) > 60000) {
          this.notLiveLoggedAt.set(target, now);
          this.logger.info?.(
            `[loop-chat] ${target}: тред закрыт, ${items.length} сообщ. ждут открытия сессии`,
          );
        }
        return false;
      }
      for (const item of items) this.state.attempted(item);
      this.logger.error?.(`[loop-chat] доставка в ${target} не удалась`, String(error));
      return false;
    }

    this.closeItems(target, items);
    this.logger.info?.(
      `[loop-chat] → ${target}${items.length > 1 ? ` (${items.length} сообщ.)` : ''}: ${truncate(text)}`,
    );
    return true;
  }

  closeItems(target, items) {
    for (const item of items) {
      this.state.drop(item.key, target);
      this.state.recordDelivery(item.fromRole || item.from, target);
    }
  }

  /**
   * Текст, который увидит адресат.
   *
   * Два режима:
   * - `copy` (по умолчанию) — текст сообщения внутри реплики;
   * - `pointer` — только «в чате <файл> есть сообщение от <кто>», читать файл.
   *   Экономит контекст и не даёт тексту разойтись с файлом-источником.
   */
  buildText(target, items) {
    const to = this.options.roles?.[target]?.title ?? target;
    const last = items[items.length - 1];

    if (this.options.notifyStyle === 'pointer') {
      const template =
        items.length === 1 ? this.options.pointerTemplate : this.options.pointerBatchTemplate;
      return render(template, {
        from: last.from,
        to,
        code: String(target).toUpperCase(),
        count: items.length,
        ts: last.ts,
        file: `${last.file}.md`,
        files: [...new Set(items.map((item) => item.file))].map((file) => `${file}.md`).join(', '),
        text: items.length === 1 ? last.text : '',
      });
    }

    if (items.length === 1) {
      if (last.raw) return last.text;
      return render(this.options.messageTemplate, {
        from: last.from,
        to,
        code: String(target).toUpperCase(),
        text: last.text,
        ts: last.ts,
        file: last.file,
      });
    }
    const header = render(this.options.batchHeader, {
      count: items.length,
      from: last.from,
      to,
      code: String(target).toUpperCase(),
    });
    const lines = items.map((item) => `- [${item.ts}] ${item.from}: ${item.text}`);
    return `${header}\n${lines.join('\n')}`;
  }

  wantedTitleOf(role) {
    if (this.options.sessions?.[role]) return this.options.sessions[role];
    return String(this.options.titleTemplate ?? '{project} | {role}')
      .replace(/\{project\}/g, this.options.project ?? '')
      .replace(/\{role\}/g, this.options.roles?.[role]?.title ?? role)
      .replace(/\{code\}/g, String(role).toUpperCase());
  }
}
