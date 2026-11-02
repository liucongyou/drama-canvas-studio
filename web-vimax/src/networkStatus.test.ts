import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

const notifyMock = vi.hoisted(() => vi.fn());
vi.mock('./toast', () => ({notify: notifyMock}));

import {installNetworkStatusToasts} from './networkStatus';

/**
 * 这个工程没有 jsdom 测试环境（所有用例都跑在 node 下），
 * 所以用一个 EventTarget 充当 window，只提供 addEventListener / dispatchEvent，
 * 足够覆盖网络状态的进出。
 */
function installEnv(online: boolean) {
  const win = new EventTarget();
  vi.stubGlobal('window', win as unknown as Window & typeof globalThis);
  vi.stubGlobal('navigator', {onLine: online});
  return win;
}

function fire(win: EventTarget, type: 'online' | 'offline') {
  win.dispatchEvent(new Event(type));
}

describe('network status toasts', () => {
  let dispose: (() => void) | null = null;

  beforeEach(() => notifyMock.mockClear());
  afterEach(() => {
    dispose?.();
    dispose = null;
    vi.unstubAllGlobals();
  });

  it('tells the user once when the connection drops, then again when it returns', () => {
    const win = installEnv(true);
    dispose = installNetworkStatusToasts();

    vi.stubGlobal('navigator', {onLine: false});
    fire(win, 'offline');
    // 抖动会重复派发，不能在界面上堆出好几条一样的提示。
    fire(win, 'offline');

    expect(notifyMock).toHaveBeenCalledTimes(1);
    expect(notifyMock.mock.calls[0][0]).toContain('网络已断开');
    expect(notifyMock.mock.calls[0][1]).toBe('error');

    vi.stubGlobal('navigator', {onLine: true});
    fire(win, 'online');

    expect(notifyMock).toHaveBeenCalledTimes(2);
    expect(notifyMock.mock.calls[1][0]).toContain('网络已恢复');
    expect(notifyMock.mock.calls[1][1]).toBe('success');
  });

  it('calls the recovery hook so the page can refresh its data', () => {
    const win = installEnv(true);
    const onRecover = vi.fn();
    dispose = installNetworkStatusToasts(onRecover);

    vi.stubGlobal('navigator', {onLine: false});
    fire(win, 'offline');
    expect(onRecover).not.toHaveBeenCalled();

    vi.stubGlobal('navigator', {onLine: true});
    fire(win, 'online');
    expect(onRecover).toHaveBeenCalledTimes(1);
  });

  it('does not announce a recovery that never happened', () => {
    const win = installEnv(true);
    dispose = installNetworkStatusToasts();

    fire(win, 'online');
    expect(notifyMock).not.toHaveBeenCalled();
  });

  it('remembers a page that loaded while already offline', () => {
    const win = installEnv(false);
    dispose = installNetworkStatusToasts();

    fire(win, 'offline');
    expect(notifyMock).not.toHaveBeenCalled();

    vi.stubGlobal('navigator', {onLine: true});
    fire(win, 'online');
    expect(notifyMock).toHaveBeenCalledTimes(1);
    expect(notifyMock.mock.calls[0][0]).toContain('网络已恢复');
  });

  it('stops listening after dispose', () => {
    const win = installEnv(true);
    const stop = installNetworkStatusToasts();
    stop();

    vi.stubGlobal('navigator', {onLine: false});
    fire(win, 'offline');
    expect(notifyMock).not.toHaveBeenCalled();
  });
});
