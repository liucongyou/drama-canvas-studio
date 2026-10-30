export type TaskOutcomeLike = {
  status: string;
  phase?: string;
  error?: string;
  error_kind?: string;
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
  const outcomeUncertain = backendPhase === 'unknown'
    || interruptedUncertain
    || failedUncertain;
  const outcomePending =
    interruptedUncertain
    || (
      task.status === 'interrupted'
      && task.outcome?.phase === 'unknown'
    );
  const terminal = task.status === 'failed' || task.status === 'interrupted';
  const statusLabel = task.status === 'succeeded'
    ? '已完成'
    : task.status === 'running'
      ? '生成中'
      : task.status === 'queued'
        ? '排队中'
        : task.status === 'submitting'
          ? '提交中'
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
