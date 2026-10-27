export type ToastTone = 'success' | 'error' | 'info';

export type ToastMessage = {
  id: string;
  message: string;
  tone: ToastTone;
};

const TOAST_EVENT = 'yingjie-toast';

/** 当前订阅者数量。0 表示 ToastHost 还没挂载（首屏崩溃、宿主自己崩了）。 */
let listeners = 0;
/** 没有订阅者时先把提示存下来，等 ToastHost 挂载后回放，否则「首屏出错」这条最关键的提示会静默丢失。 */
let pending: ToastMessage[] = [];

function dispatch(toast: ToastMessage) {
  window.dispatchEvent(new CustomEvent<ToastMessage>(TOAST_EVENT, {detail: toast}));
}

/**
 * 弹一个操作提示。
 * @param id 可选稳定 id：重复调用同 id 时 ToastHost 按 id 去重（后弹覆盖先弹），
 *  适合"连接断开/恢复"这类会被 SSE 抖动反复触发的提示，避免堆叠重复弹窗。
 *  不传时生成随机 id（每次都是新提示）。
 */
export function notify(message: string, tone: ToastTone = 'success', id: string = '') {
  const stableId = id || `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const toast: ToastMessage = {id: stableId, message, tone};
  if (!listeners) {
    // 只在无宿主时排队；同样 id 只留最后一条，避免队列被循环报错灌满
    pending = [...pending.filter((item) => item.id !== stableId), toast].slice(-8);
    return;
  }
  dispatch(toast);
}

export function subscribeToToasts(listener: (toast: ToastMessage) => void) {
  const onToast = (event: Event) => listener((event as CustomEvent<ToastMessage>).detail);
  window.addEventListener(TOAST_EVENT, onToast);
  listeners += 1;

  // 回放挂载前积压的提示（紧接着 dispatch，React 会合并到同一批更新里）
  if (pending.length) {
    const replay = pending;
    pending = [];
    replay.forEach(dispatch);
  }

  return () => {
    listeners = Math.max(0, listeners - 1);
    window.removeEventListener(TOAST_EVENT, onToast);
  };
}
