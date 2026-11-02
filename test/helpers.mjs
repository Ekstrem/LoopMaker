/**
 * Общая сцена для тестов моста: временная папка чата, состояние на диске,
 * подставные сессии и отправка.
 */
import { mkdtemp, mkdir, rm, writeFile, appendFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

import { normalizeOptions } from '../lib/config.js';
import { StateStore } from '../lib/state.js';
import { ChatBridge } from '../lib/bridge.js';

export const silent = { debug() {}, info() {}, warn() {}, error() {} };

export async function makeStage({ config = {}, sessions = {}, send, now, bridgeOptions = {} } = {}) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'loopmaker-test-'));
  const chatDir = path.join(dir, 'chat');
  await mkdir(chatDir, { recursive: true });

  const options = normalizeOptions(
    {
      project: 'XDairy',
      chatDir,
      pollMs: 1000,
      stateFile: path.join(dir, 'state.json'),
      roles: { be: 'BackEnd', fe: 'FrontEnd', tl: 'TechLead', do: 'DevOps' },
      ...config,
    },
    dir,
  );

  const sent = [];
  const sessionMap = { ...sessions };
  const bridge = new ChatBridge({
    options,
    logger: silent,
    state: new StateStore({ file: options.stateFile, logger: silent }),
    resolveSession: async (role) => sessionMap[role],
    send: send ?? (async (sessionId, text) => { sent.push({ sessionId, text }); }),
    now,
    ...bridgeOptions,
  });

  return {
    dir,
    chatDir,
    bridge,
    options,
    sent,
    sessionMap,
    writeChat: (file, text) => writeFile(path.join(chatDir, file), text, 'utf8'),
    appendChat: (file, text) => appendFile(path.join(chatDir, file), text, 'utf8'),
    /** Остановить мост и дождаться записи состояния, иначе rm ловит EBUSY на Windows. */
    cleanup: async () => {
      bridge.stop();
      try {
        await bridge.state.flush();
      } catch {
        /* состояние могло не создаваться */
      }
      await rm(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
    },
  };
}
