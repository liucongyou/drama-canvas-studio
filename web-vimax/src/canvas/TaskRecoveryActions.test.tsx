import {describe, expect, it} from 'vitest';

import {primaryRecoveryAction} from '../taskOutcomePresentation';

describe('task recovery action contract', () => {
  it('returns no recovery action for a running task', () => {
    expect(primaryRecoveryAction({
      status: 'running',
      phase: '生成中',
    })).toBeNull();
  });

  it('keeps result fetching free of retry actions', () => {
    expect(primaryRecoveryAction({
      status: 'succeeded',
      delivery_status: 'materializing',
      outcome: {
        phase: 'fetching',
        regenerate: 'blocked',
      },
    })).toBeNull();
  });

  it('offers local recovery before spending another provider call', () => {
    expect(primaryRecoveryAction({
      status: 'succeeded',
      delivery_status: 'unrecoverable',
      error: '结果无法恢复',
    })).toBe('recover-local-result');
  });
});
