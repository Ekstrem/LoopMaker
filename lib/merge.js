/**
 * Мержер зелёных PR (PDR-047, 2026-11-12).
 *
 * Native merge queue на `Ekstrem/XDiary` недоступен (rulesets API возвращает 422
 * «Invalid rule 'merge_queue'»), поэтому авто-мерж делает сам плагин: раз в poll
 * смотрит открытые PR в `main`, и если все required-чеки зелёные — мержит squash'ем.
 *
 * Чеки статические (зона, статусы, секреты, бюджет доков), комбинированного теста
 * не требуют; конфликт при мерже означает нарушение зон (редко) — такой PR остаётся
 * открытым и ждёт фикса роли.
 */
const API = 'https://api.github.com';

export class MergeService {
  /**
   * @param {object} cfg
   * @param {string} cfg.owner владелец репозитория
   * @param {string} cfg.repo имя репозитория
   * @param {string} [cfg.token] GitHub PAT (repo scope, права на merge)
   * @param {string[]} [cfg.requiredChecks] контексты, которые должны быть зелёными
   * @param {{info: Function, warn: Function}} [cfg.logger]
   */
  constructor({ owner, repo, token, requiredChecks = [], logger }) {
    this.owner = owner;
    this.repo = repo;
    this.token = token;
    this.requiredChecks = requiredChecks;
    this.logger = logger;
  }

  get enabled() {
    return Boolean(this.token && this.owner && this.repo && this.requiredChecks.length > 0);
  }

  async _api(path, method = 'GET', body) {
    const res = await fetch(`${API}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'dsh-loopmaker',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`${method} ${path} -> ${res.status}: ${text.slice(0, 160)}`);
    }
    return res.json();
  }

  async listOpenPRs() {
    return this._api(`/repos/${this.owner}/${this.repo}/pulls?state=open&base=main&per_page=50`);
  }

  async checkRuns(sha) {
    const data = await this._api(
      `/repos/${this.owner}/${this.repo}/commits/${sha}/check-runs?per_page=100`,
    );
    return data.check_runs ?? [];
  }

  async mergePR(number) {
    return this._api(`/repos/${this.owner}/${this.repo}/pulls/${number}/merge`, 'PUT', {
      merge_method: 'squash',
    });
  }

  /**
   * Один проход: открытые PR в main → зелёные ли required-чеки → мерж.
   * @returns {Promise<{merged: number[], waiting: string[]}>}
   */
  async mergeGreenPrs() {
    if (!this.enabled) return { skipped: true, merged: [], waiting: [] };
    let prs;
    try {
      prs = await this.listOpenPRs();
    } catch (error) {
      this.logger?.warn?.('[loop-merge] список PR не получен', String(error?.message ?? error));
      return { merged: [], waiting: [] };
    }

    const merged = [];
    const waiting = [];
    for (const pr of prs) {
      let runs;
      try {
        runs = await this.checkRuns(pr.head.sha);
      } catch (error) {
        waiting.push(`#${pr.number}: чек недоступен`);
        continue;
      }
      // по каждому имени берём самый свежий прогон (могут быть и push, и pull_request)
      const latest = new Map();
      for (const r of runs) {
        const prev = latest.get(r.name);
        if (!prev || String(r.started_at ?? '') > String(prev.started_at ?? '')) {
          latest.set(r.name, r);
        }
      }
      const ok = (name) => latest.get(name)?.conclusion === 'success';
      const allGreen = this.requiredChecks.every(ok);
      if (!allGreen) {
        const pending = this.requiredChecks.filter((n) => !ok(n));
        waiting.push(`#${pr.number}: ждёт [${pending.join(', ')}]`);
        continue;
      }
      try {
        await this.mergePR(pr.number);
        merged.push(pr.number);
        this.logger?.info?.(`[loop-merge] PR #${pr.number} смержен (squash)`);
      } catch (error) {
        waiting.push(`#${pr.number}: ${String(error?.message ?? error).slice(0, 80)}`);
      }
    }
    return { merged, waiting };
  }
}
