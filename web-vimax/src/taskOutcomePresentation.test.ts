import {describe, expect, it} from 'vitest';
import {taskOutcomePresentation} from './taskOutcomePresentation';

describe('task outcome presentation', () => {
  it('keeps a disconnected historical paid submission pending', () => {
    const result = taskOutcomePresentation({
      status: 'failed',
      phase: '任务异常',
      error: '[WinError 10053] 你的主机中的软件中止了一个已建立的连接。',
    });
    expect(result.outcomeUncertain).toBe(true);
    expect(result.statusLabel).toBe('结果待确认');
    expect(result.canRegenerate).toBe(true);
  });

  it('keeps the original paid result labeled pending', () => {
    const result = taskOutcomePresentation({
      status: 'interrupted',
      phase: '结果待确认',
      error: '供应商已受理，但没有返回任务号',
    });
    expect(result.outcomePending).toBe(true);
    expect(result.statusLabel).toBe('结果待确认');
    expect(result.canRegenerate).toBe(true);
  });

  it('allows local validation failures to be fixed and retried', () => {
    const result = taskOutcomePresentation({
      status: 'failed',
      error: '参考素材缺失 1 项：音频｜柜员',
      error_kind: 'local_validation',
    });
    expect(result.localBlocked).toBe(true);
    expect(result.outcomeUncertain).toBe(false);
    expect(result.statusLabel).toBe('待补素材');
    expect(result.canRegenerate).toBe(true);
  });

  it('does not offer retry for a running task', () => {
    const result = taskOutcomePresentation({
      status: 'running',
      phase: '生成中',
    });
    expect(result.statusLabel).toBe('生成中');
    expect(result.canRegenerate).toBe(false);
  });

  it('uses the backend outcome instead of error text', () => {
    const result = taskOutcomePresentation({
      status: 'failed',
      error: '供应商暂时不可用',
      outcome: {
        phase: 'unknown',
        regenerate: 'confirm',
        reason: 'provider_outcome_unknown',
        requiresConfirmation: true,
        providerAccepted: false,
      },
    });
    expect(result.statusLabel).toBe('结果待确认');
    expect(result.canRegenerate).toBe(true);
  });

  it('shows a stale failed row as generating while the provider task is live', () => {
    const result = taskOutcomePresentation({
      status: 'failed',
      phase: '结果待确认',
      error: '执行租约已过期，但供应商任务号仍可恢复',
      outcome: {
        phase: 'running',
        regenerate: 'blocked',
        reason: 'provider_task_running',
        requiresConfirmation: false,
        providerAccepted: true,
      },
      delivery_status: 'pending',
    });
    expect(result.outcomeUncertain).toBe(false);
    expect(result.outcomePending).toBe(true);
    expect(result.statusLabel).toBe('生成中');
    expect(result.canRegenerate).toBe(false);
  });

  it('shows an interrupted recovery row as generating while the provider task is live', () => {
    const result = taskOutcomePresentation({
      status: 'interrupted',
      phase: '结果待确认',
      error: '供应商任务仍在执行；系统会继续查询原任务',
      outcome: {
        phase: 'running',
        regenerate: 'blocked',
        reason: 'provider_task_running',
        requiresConfirmation: false,
        providerAccepted: true,
      },
      delivery_status: 'pending',
    });
    expect(result.outcomeUncertain).toBe(false);
    expect(result.outcomePending).toBe(true);
    expect(result.statusLabel).toBe('生成中');
    expect(result.canRegenerate).toBe(false);
  });

  it('blocks regeneration when the backend marks it blocked', () => {
    const result = taskOutcomePresentation({
      status: 'running',
      outcome: {
        phase: 'running',
        regenerate: 'blocked',
        requiresConfirmation: false,
      },
    });
    expect(result.canRegenerate).toBe(false);
  });

  it('does not call a succeeded task ready before result delivery', () => {
    const result = taskOutcomePresentation({
      status: 'succeeded',
      delivery_status: 'materializing',
      outcome: {
        phase: 'fetching',
        regenerate: 'blocked',
        reason: 'result_fetching',
      },
    });
    expect(result.statusLabel).toBe('正在取回结果');
    expect(result.outcomePending).toBe(true);
    expect(result.canRegenerate).toBe(false);
  });

  it('shows an interrupted accepted task as fetching instead of failed', () => {
    const result = taskOutcomePresentation({
      status: 'interrupted',
      phase: '供应商任务已提交',
      delivery_status: 'pending',
      outcome: {
        phase: 'fetching',
        regenerate: 'blocked',
        reason: 'result_fetching',
        providerAccepted: true,
      },
    });
    expect(result.statusLabel).toBe('正在取回结果');
    expect(result.outcomePending).toBe(true);
    expect(result.canRegenerate).toBe(false);
  });

  it('does not hide a failed result delivery', () => {
    const result = taskOutcomePresentation({
      status: 'succeeded',
      delivery_status: 'failed',
      outcome: {
        phase: 'failed',
        regenerate: 'confirm',
        reason: 'result_delivery_failed',
      },
    });
    expect(result.statusLabel).toBe('结果保存失败');
    expect(result.canRegenerate).toBe(true);
  });

  it('shows an expired unconfirmed task as needing provider review', () => {
    const result = taskOutcomePresentation({
      status: 'failed',
      phase: '无法确认且已过期',
      error: '供应商结果无法确认，任务已超过 24 小时',
      delivery_status: 'failed',
      outcome: {
        phase: 'failed',
        regenerate: 'confirm',
        reason: 'provider_unconfirmed_expired',
        requiresConfirmation: true,
      },
    });
    expect(result.outcomeUncertain).toBe(true);
    expect(result.outcomePending).toBe(true);
    expect(result.statusLabel).toBe('结果无法确认');
    expect(result.canRegenerate).toBe(true);
  });
});
