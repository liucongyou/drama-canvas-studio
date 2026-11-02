import {notify} from './toast';

/**
 * 网络状态提示。
 *
 * 断网时点「生成」或「保存」只会看到一句「请求失败」，用户很难判断是自己网络的问题、
 * 服务挂了，还是操作本身出错。浏览器本身不提示，所以这里补上：断开时明确说一次，
 * 恢复时也说一次，让用户知道可以继续了。
 *
 * 用固定 id 交给 ToastHost 去重：某些环境下 offline 事件会连着触发多次，
 * 不能让提示堆成一片。
 */
export function installNetworkStatusToasts(onRecover?: () => void): () => void {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return () => {};

  // 刷新页面时可能本来就是断网状态，先记下来，避免恢复时被当成「从未断过」。
  let offline = navigator.onLine === false;

  const handleOffline = () => {
    if (offline) return;
    offline = true;
    notify(
      '网络已断开，正在等待重连。这期间的操作不会丢失，恢复后会自动同步。',
      'error',
      'network-offline',
    );
  };

  const handleOnline = () => {
    if (!offline) return;
    offline = false;
    notify('网络已恢复，正在同步最新状态。', 'success', 'network-online');
    onRecover?.();
  };

  window.addEventListener('offline', handleOffline);
  window.addEventListener('online', handleOnline);
  return () => {
    window.removeEventListener('offline', handleOffline);
    window.removeEventListener('online', handleOnline);
  };
}
