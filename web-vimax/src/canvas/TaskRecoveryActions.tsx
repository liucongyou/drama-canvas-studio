import {
  CircleAlert,
  Download,
  KeyRound,
  RefreshCw,
  Shuffle,
} from 'lucide-react';

import type {TaskOutcomeLike} from '../taskOutcomePresentation';
import {
  primaryRecoveryAction,
  type RecoveryAction,
} from '../taskOutcomePresentation';

type TaskRecoveryActionsProps = {
  task: TaskOutcomeLike;
  nodeAvailable: boolean;
  onRetry: () => void;
  onSwitchModel?: () => void;
  onCheckProvider?: () => void;
  onRecoverLocalResult?: () => void;
  onMarkFailed?: () => void;
};

const ACTION_PRESENTATION: Record<RecoveryAction, {
  label: string;
  title: string;
  Icon: typeof RefreshCw;
}> = {
  retry: {
    label: '重新生成',
    title: '使用当前提示词重新提交',
    Icon: RefreshCw,
  },
  'switch-model': {
    label: '切换模型',
    title: '选择兼容该任务的模型',
    Icon: Shuffle,
  },
  'check-provider': {
    label: '检查供应商',
    title: '检查供应商余额、额度和 API Key',
    Icon: KeyRound,
  },
  'recover-local-result': {
    label: '恢复本地结果',
    title: '从已有供应商回执恢复，不重新生成',
    Icon: Download,
  },
  'mark-failed': {
    label: '核对并结束',
    title: '核对供应商记录后人工结束原任务',
    Icon: CircleAlert,
  },
};

export function TaskRecoveryActions({
  task,
  nodeAvailable,
  onRetry,
  onSwitchModel,
  onCheckProvider,
  onRecoverLocalResult,
  onMarkFailed,
}: TaskRecoveryActionsProps) {
  const action = primaryRecoveryAction(task);
  if (!action) return null;
  if (
    !nodeAvailable
    && action !== 'check-provider'
    && action !== 'mark-failed'
  ) {
    return null;
  }
  const handlers: Record<RecoveryAction, (() => void) | undefined> = {
    retry: onRetry,
    'switch-model': onSwitchModel,
    'check-provider': onCheckProvider,
    'recover-local-result': onRecoverLocalResult,
    'mark-failed': onMarkFailed,
  };
  const handler = handlers[action];
  if (!handler) return null;
  const presentation = ACTION_PRESENTATION[action];
  const Icon = presentation.Icon;
  return (
    <button
      type="button"
      className={`canvas-task-recovery-action is-${action}`}
      title={presentation.title}
      onClick={handler}
    >
      <Icon size={13} strokeWidth={2} aria-hidden="true" />
      {presentation.label}
    </button>
  );
}
