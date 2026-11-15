export type TaskOutcomeLike = {
  status: string;
  phase?: string;
  error?: string;
  error_kind?: string;
  delivery_status?: string;
  outcome?: {
    phase?: string;
    regenerate?: string;
    reason?: string;
    requiresConfirmation?: boolean;
    providerAccepted?: boolean;
  };
};

export type TaskOutcomePresentation = {
  localBlocked: boolean;
  outcomeUncertain: boolean;
  outcomePending: boolean;
  statusLabel: string;
  canRegenerate: boolean;
};

export type RecoveryAction =
  | 'retry'
  | 'switch-model'
  | 'check-provider'
  | 'recover-local-result'
  | 'mark-failed';

const LOCAL_VALIDATION_RE =
  /参考素材未就绪|参考素材缺失|引用节点不存在|提示词为空|当前模型最多/;
const UNCERTAIN_RE =
  /WinError 10053|连接.*中止|提交.*未确认|结果待确认/;

export function taskOutcomePresentation(
  task: TaskOutcomeLike,
): TaskOutcomePresentation {
  const errorText = String(task.error || '');
  const backendOutcome = task.outcome;
  const backendRegenerate = backendOutcome?.regenerate;
  const backendPhase = backendOutcome?.phase;
  const deliveryStatus = String(task.delivery_status || '').toLowerCase();
  const unconfirmedExpired =
    task.phase === '无法确认且已过期'
    || backendOutcome?.reason === 'provider_unconfirmed_expired';
  const localBlocked =
    backendOutcome?.reason === 'local_validation'
    || task.error_kind === 'local_validation'
    || LOCAL_VALIDATION_RE.test(errorText);
  const interruptedUncertain =
    task.status === 'interrupted'
    && (
      task.phase === '结果待确认'
      || /没有返回任务号/.test(errorText)
    );
  const failedUncertain =
    task.status === 'failed'
    && !localBlocked
    && UNCERTAIN_RE.test(errorText);
  const liveProviderTask =
    (task.status === 'failed' || task.status === 'interrupted')
    && (
      backendOutcome?.reason === 'provider_task_running'
      || /供应商任务仍在执行|执行租约已过期/.test(errorText)
    )
    && backendPhase === 'running'
    && backendRegenerate === 'blocked'
    && !localBlocked;
  const fetchingProviderResult =
    task.status === 'interrupted'
    && backendPhase === 'fetching'
    && backendRegenerate === 'blocked'
    && !localBlocked;
  const outcomeUncertain = !liveProviderTask && (
    unconfirmedExpired
    || backendPhase === 'unknown'
    || interruptedUncertain
    || failedUncertain
  );
  const outcomePending =
    unconfirmedExpired
    || liveProviderTask
    || fetchingProviderResult
    || interruptedUncertain
    || deliveryStatus === 'pending'
    || deliveryStatus === 'materializing'
    || backendPhase === 'fetching'
    || (
      task.status === 'interrupted'
      && task.outcome?.phase === 'unknown'
    );
  const terminal = task.status === 'failed' || task.status === 'interrupted';
  const statusLabel = task.status === 'succeeded'
    ? deliveryStatus === 'pending' || deliveryStatus === 'materializing'
      ? '正在取回结果'
      : deliveryStatus === 'failed'
        ? '结果保存失败'
        : '已完成'
    : task.status === 'running'
      ? '生成中'
      : task.status === 'queued'
        ? '生成中'
        : task.status === 'submitting'
          ? '提交中'
        : unconfirmedExpired
          ? '结果无法确认'
        : liveProviderTask
          ? '生成中'
          : fetchingProviderResult
            ? '正在取回结果'
          : localBlocked
            ? '待补素材'
            : backendPhase === 'unknown' || outcomeUncertain
              ? '结果待确认'
              : task.status === 'interrupted'
                ? '已中断'
                : '失败';
  return {
    localBlocked,
    outcomeUncertain,
    outcomePending,
    statusLabel,
    canRegenerate: backendRegenerate
      ? backendRegenerate !== 'blocked'
      : terminal,
  };
}

export function primaryRecoveryAction(
  task: TaskOutcomeLike,
): RecoveryAction | null {
  const errorText = String(task.error || '');
  const deliveryStatus = String(task.delivery_status || '').toLowerCase();
  const backendOutcome = task.outcome;
  if (backendOutcome?.regenerate === 'blocked') return null;
  if (task.status === 'succeeded') {
    if (deliveryStatus === 'unrecoverable' || deliveryStatus === 'failed') {
      return 'recover-local-result';
    }
    return null;
  }
  if (['queued', 'submitting', 'running'].includes(task.status)) return null;
  if (
    task.error_kind === 'local_validation'
    || LOCAL_VALIDATION_RE.test(errorText)
  ) {
    return 'retry';
  }
  if (/欠费|余额不足|额度不足|AccountOverdue|API Key 无效/i.test(errorText)) {
    return 'check-provider';
  }
  if (
    deliveryStatus === 'unrecoverable'
    || /媒体源缺失|本地文件.*不存在|本地生成文件.*不存在/.test(errorText)
  ) {
    return 'recover-local-result';
  }
  if (
    task.status === 'interrupted'
    && (
      task.phase === '结果待确认'
      || backendOutcome?.reason === 'provider_outcome_unknown'
    )
  ) {
    return 'mark-failed';
  }
  if (/当前模型|模型不支持|切换模型/.test(errorText)) {
    return 'switch-model';
  }
  return 'retry';
}
