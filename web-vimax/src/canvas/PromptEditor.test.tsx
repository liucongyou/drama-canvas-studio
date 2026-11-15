import {afterEach, describe, expect, it, vi} from 'vitest';

import {
  PROMPT_COMMIT_DELAY_MS,
  createPromptCommitController,
} from './PromptEditor';

afterEach(() => {
  vi.useRealTimers();
});

describe('prompt commit controller', () => {
  it('commits once after the configured idle window', () => {
    vi.useFakeTimers();
    const commit = vi.fn();
    const controller = createPromptCommitController(commit);

    controller.schedule();
    vi.advanceTimersByTime(PROMPT_COMMIT_DELAY_MS - 1);
    expect(commit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(commit).toHaveBeenCalledOnce();
  });

  it('coalesces rapid typing into one trailing commit', () => {
    vi.useFakeTimers();
    const commit = vi.fn();
    const controller = createPromptCommitController(commit);

    controller.schedule();
    vi.advanceTimersByTime(300);
    controller.schedule();
    vi.advanceTimersByTime(599);
    expect(commit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(commit).toHaveBeenCalledOnce();
  });

  it('flushes immediately on blur and cancels the pending timer', () => {
    vi.useFakeTimers();
    const commit = vi.fn();
    const controller = createPromptCommitController(commit);

    controller.schedule();
    controller.flush();
    vi.advanceTimersByTime(PROMPT_COMMIT_DELAY_MS);

    expect(commit).toHaveBeenCalledOnce();
  });
});
