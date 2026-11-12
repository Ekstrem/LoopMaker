import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);

/**
 * Читает outbox-чат из git (`origin/main`), а не из локальной папки.
 *
 * Причина (PDR-047, 2026-11-12): локальный чекаут отстаёт от `origin` (было 102
 * коммита), поэтому мост читал устаревший чат и «не видел» новые сообщения —
 * роли получали тишину, а не доставку. Источник правды для чата — `origin`.
 */
export class GitChat {
  /**
   * @param {object} cfg
   * @param {string} cfg.repo абсолютный путь к репозиторию (XDiary)
   * @param {string} [cfg.chatRel] путь чата относительно корня репо
   * @param {{warn: Function}} [cfg.logger]
   */
  constructor({ repo, chatRel = '5-tech-backlog/chat', logger }) {
    this.repo = repo;
    this.chatRel = chatRel;
    this.logger = logger;
  }

  async fetch() {
    try {
      await run('git', ['-C', this.repo, 'fetch', 'origin', 'main']);
    } catch (error) {
      this.logger?.warn?.('[loop-chat] git fetch не удался', String(error?.message ?? error));
    }
  }

  /** Имена файлов чата (только `.md`, без пути) из origin/main. */
  async list() {
    const { stdout } = await run('git', [
      '-C', this.repo, 'ls-tree', '-r', 'origin/main', '--name-only', '--', this.chatRel,
    ]);
    return stdout
      .split('\n')
      .map((s) => s.trim())
      .filter((s) => s.toLowerCase().endsWith('.md'))
      .map((p) => p.split('/').pop());
  }

  /** Содержимое файла чата из origin/main. */
  async read(file) {
    const { stdout } = await run('git', ['-C', this.repo, 'show', `origin/main:${this.chatRel}/${file}`]);
    return stdout;
  }
}
