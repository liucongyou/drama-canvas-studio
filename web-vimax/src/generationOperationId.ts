/**
 * 生成提交的幂等键管理。
 *
 * 为什么单独成模块：这是防重复扣费的最后一道前端防线。后端按
 * `client_operation_id` 回查已建任务，命中就把原任务交接回来；前端若在重试时
 * 换一个新的 UUID，后端只会当成另一笔新任务。
 *
 * 典型踩坑场景：请求已经打到服务端并建好了任务、开始扣预算，但响应在网络上丢了
 * （超时、代理中断、页面切后台被调度挂起）。用户看到「失败」点重试，如果拿到的是
 * 新 operationId，就会第二次扣费。所以键必须跟着「节点 + 当前提示词与参数」走，
 * 只有请求确认落地之后才允许丢弃。
 */

export type OperationIdRecord = {
  operationId: string;
  signature: string;
};

/**
 * 判断「两次提交是不是同一个意图」。
 *
 * 提示词或参数变了就是新意图，必须换键——否则用户改了提示词重生成，后端会把
 * 上一次的旧任务原样返回，界面看起来像「点了没反应」。
 */
export function generationIntentSignature(input: {
  kind?: string;
  prompt?: string;
  params?: unknown;
}): string {
  let params = '';
  try {
    params = JSON.stringify(input.params || {});
  } catch {
    // 参数里出现循环引用或 BigInt 时不能让整条提交链路崩掉；
    // 退化成「无法比较」，宁可换新键也不要卡死。
    params = String(Date.now());
  }
  return `${input.kind || ''}|${input.prompt || ''}|${params}`;
}

export function newOperationId(): string {
  return (
    globalThis.crypto?.randomUUID?.()
    || `canvas-operation-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  );
}

/** 取该节点可用的幂等键：意图未变则复用，意图变了才换新。 */
export function claimGenerationOperationId(
  store: Map<string, OperationIdRecord>,
  nodeId: string,
  signature: string,
  create: () => string = newOperationId,
): string {
  const pending = store.get(nodeId);
  if (pending && pending.signature === signature) return pending.operationId;
  const operationId = create();
  store.set(nodeId, {operationId, signature});
  return operationId;
}

/** 请求确实落地后归还键，让用户下一次「重新生成」能拿到新任务。 */
export function releaseGenerationOperationId(
  store: Map<string, OperationIdRecord>,
  nodeId: string,
): void {
  store.delete(nodeId);
}

/**
 * Whether a failed generation attempt is known to have created no reusable
 * task and may therefore use a fresh idempotency key next time.
 *
 * Keep the key for transport failures, timeouts and provider-uncertain errors:
 * the server may already have accepted and charged the original request.
 * Release it only for a definitive client/provider rejection, where replaying
 * the same key would trap every retry on the old failed task.
 */
export function shouldReleaseOperationIdAfterError(
  error: unknown,
): boolean {
  const value = error as {
    status?: unknown;
    statusCode?: unknown;
    message?: unknown;
  } | null;
  const status = Number(value?.status ?? value?.statusCode ?? 0);
  const message = String(value?.message ?? error ?? '');
  if (!Number.isFinite(status) || status <= 0) return false;
  if (status < 400 || status >= 500) return false;
  if (status === 408 || status === 429) return false;
  if (
    message.includes('提交结果未确认')
    || message.includes('可能已被供应商受理')
  ) {
    return false;
  }
  return true;
}
