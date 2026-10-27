/* 运行时错误兜底：错误边界 + Toast 宿主 + 全局异常捕获
 *
 * 背景（为什么需要这一层）：
 *   main.tsx 是裸 createRoot(...).render(<Shell />)，没有任何错误边界。
 *   React 18 里 render 期抛未捕获异常会整棵树卸载 → 用户看到纯白页，只剩 F12 一行红字，
 *   连"重新打开"的入口都没有；promise reject 更是彻底静默（比如某次保存请求 500）。
 *
 * 这里补三层：
 *   ① ErrorBoundary  —— 接住 render 期异常，白屏变可操作的自救面板；
 *   ② ToastHost      —— 之前 notify() 的宿主只写在 App.tsx 里，而 App.tsx 根本没被挂载
 *                       （入口是 main.tsx 的 Shell），所以所有 notify 调用一直是空转；根上补一个；
 *   ③ installGlobalErrorHandlers —— window error / unhandledrejection 兜住 render 之外的异常。
 */
import {Component, useEffect, useState, type ErrorInfo, type ReactNode} from 'react';
import {dialog} from './GDialog';
import {AlertCircle, AlertTriangle, CheckCircle2, Copy, Info, RefreshCw, RotateCcw, X} from 'lucide-react';
import {notify, subscribeToToasts, type ToastMessage} from './toast';

/** 各作用域最近一次错误，供"复制错误详情"使用（刷新后仍在 sessionStorage 里） */
const LAST_ERROR_KEY = 'vimax.lastError';

type Scope = 'root' | 'canvas';

type BoundaryProps = {
  scope?: Scope;
  /** 面板标题里的对象名，如"画布" */
  label?: string;
  children: ReactNode;
};

type BoundaryState = {error: Error | null; stack: string; retries: number};

function firstLine(text: string, max = 90): string {
  const line = String(text || '').split('\n')[0].trim();
  return line.length > max ? line.slice(0, max) + '…' : line || '未知错误';
}

function describe(error: unknown): {message: string; detail: string} {
  const err = error instanceof Error ? error : new Error(String(error ?? ''));
  const detail = [err.name, err.message, err.stack].filter(Boolean).join('\n');
  return {message: firstLine(err.message || String(error ?? ''), 160), detail};
}

function saveLastError(scope: Scope, message: string, detail: string) {
  try {
    sessionStorage.setItem(LAST_ERROR_KEY, JSON.stringify({
      scope, message, detail, at: new Date().toISOString(), href: location.href,
    }));
  } catch { /* 隐私模式下 storage 可能不可写，忽略 */ }
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch { /* 非 https / 无权限时退回 execCommand */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export class ErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = {error: null, stack: '', retries: 0};

  static getDerivedStateFromError(error: Error): Partial<BoundaryState> {
    return {error};
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const {message, detail} = describe(error);
    const stack = `${detail}\n\n--- componentStack ---${info.componentStack || ''}`;
    this.setState({stack});
    saveLastError(this.props.scope || 'root', message, stack);
    // 控制台保留完整信息，方便贴给我排查
    console.error(`[ErrorBoundary:${this.props.scope || 'root'}]`, error, info);
    notify(`界面异常：${message}`, 'error', 'runtime-error');
  }

  private retry = () => {
    if (this.state.retries >= 2) return;
    this.setState((s) => ({error: null, stack: '', retries: s.retries + 1}));
  };

  private reload = () => {
    window.location.reload();
  };

  private copy = async () => {
    const {message, detail} = describe(this.state.error);
    const ok = await copyText(`${message}\n\n${this.state.stack || detail}`);
    if (ok) notify('错误详情已复制', 'success', 'error-copy');
    else notify('复制失败，请按 F12 从控制台手动复制', 'info', 'error-copy');
  };

  private resetLayout = async () => {
    // 最后手段：本地布局缓存（节点位置 / 手动连线 / 编组）损坏时，
    // 解析阶段就会抛错，刷新多少次都是白屏。清掉后回到服务端记录的位置。
    const ok = await dialog.confirm({
      title: '清除本地画布布局缓存？',
      message:
        '仅清除**浏览器本地**的画布布局缓存（节点位置、手动连线、编组）。\n\n'
        + '服务端保存的节点、提示词、引用、备注都不受影响，刷新后会按服务端数据重新排布。\n\n'
        + '⚠ 如果当前布局是你手动调好的，这一份会丢失。',
      confirmText: '清除并刷新',
      tone: 'danger',
    });
    if (!ok) return;
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith('vimax.canvas.layout.') || k.startsWith('vimax.canvas.removedRefs.'))
        .forEach((k) => localStorage.removeItem(k));
    } catch { /* ignore */ }
    window.location.reload();
  };

  render() {
    if (!this.state.error) return this.props.children;

    const scope = this.props.scope || 'root';
    const {message, detail} = describe(this.state.error);
    const body = this.state.stack || detail;
    const exhausted = this.state.retries >= 2;
    const title = scope === 'canvas'
      ? `${this.props.label || '画布'}渲染失败`
      : '界面出了点问题';

    return (
      <div className={scope === 'canvas' ? 'eb-inline' : 'eb-backdrop'}>
        <div className="eb-card" role="alert">
          <div className="eb-head">
            <span className="eb-ico" aria-hidden="true"><AlertTriangle size={17} strokeWidth={2} /></span>
            <div className="eb-head-text">
              <strong>{title}</strong>
              <span>已经拦下来了，没有白屏。数据都在服务端，刷新不会丢。</span>
            </div>
          </div>

          <p className="eb-msg" title={message}>{message}</p>

          <div className="eb-actions">
            <button type="button" className="eb-btn is-primary" onClick={this.reload}>
              <RefreshCw size={13} strokeWidth={2.2} aria-hidden="true" /> 重新加载
            </button>
            <button
              type="button"
              className="eb-btn"
              onClick={this.retry}
              disabled={exhausted}
              title={exhausted ? '已重试 2 次，请重新加载' : '不刷新页面，重新渲染一次'}
            >
              <RotateCcw size={13} strokeWidth={2.2} aria-hidden="true" /> {exhausted ? '已重试 2 次' : '重试渲染'}
            </button>
            <button type="button" className="eb-btn" onClick={() => void this.copy()}>
              <Copy size={13} strokeWidth={2.2} aria-hidden="true" /> 复制详情
            </button>
          </div>

          <details className="eb-detail">
            <summary>技术细节</summary>
            <pre>{body}</pre>
          </details>

          {scope === 'root' && (
            <button type="button" className="eb-link" onClick={this.resetLayout}>
              反复白屏？重置本地布局缓存
            </button>
          )}
        </div>
      </div>
    );
  }
}

/** toast 宿主：挂在根节点上，即使 App 崩了也能弹提示 */
export function ToastHost() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(() => subscribeToToasts((toast) => {
    setToasts((current) => [...current.filter((item) => item.id !== toast.id), toast].slice(-4));
    window.setTimeout(
      () => setToasts((current) => current.filter((item) => item.id !== toast.id)),
      toast.tone === 'error' ? 6000 : 3400,
    );
  }), []);

  if (!toasts.length) return null;

  return (
    <div className="toast-stack" aria-live="polite" aria-label="操作提示">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast-item is-${toast.tone}`}>
          {toast.tone === 'success'
            ? <CheckCircle2 size={16} />
            : toast.tone === 'error' ? <AlertCircle size={16} /> : <Info size={16} />}
          <span>{toast.message}</span>
          <button
            type="button"
            onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}
            aria-label="关闭提示"
          >
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

/**
 * 兜住 render 之外的异常（事件回调、setTimeout、promise、脚本加载）。
 * 只弹一次同 id 的 toast，避免循环报错把屏幕刷满。
 */
export function installGlobalErrorHandlers() {
  const flag = '__vimaxErrorHandlers';
  const w = window as unknown as Record<string, boolean>;
  if (w[flag]) return;
  w[flag] = true;

  const lastAt = new Map<string, number>();
  const report = (prefix: string, raw: string) => {
    const message = firstLine(raw);
    if (!message) return;
    const now = Date.now();
    // 同一条错误 1.5s 内只弹一次（React 严格模式/轮询会导致同一错误连发）
    if ((lastAt.get(message) || 0) > now - 1500) return;
    lastAt.set(message, now);
    saveLastError('root', message, raw);
    notify(`${prefix}：${message}`, 'error', 'runtime-error');
  };

  // 浏览器/库的良性噪声：报出来只会制造恐慌，且不影响功能。必须静默。
  //  - ResizeObserver loop…：布局抖动，React Flow / 富文本里极常见，规范里也建议忽略
  //  - Script error.：跨源脚本无 CORS 头，拿不到任何细节，弹了也无从排查
  //  - Non-Error promise rejection / AbortError：主动取消的请求
  const BENIGN = [
    /ResizeObserver loop (limit exceeded|completed with undelivered notifications)/i,
    /^Script error\.?$/i,
    /aborted|AbortError/i,
    /The user aborted a request/i,
    /Failed to fetch|NetworkError when attempting to fetch/i,
  ];
  const isBenign = (text: string) => BENIGN.some((re) => re.test(text));

  window.addEventListener('error', (event) => {
    const target = event.target as (HTMLElement & {tagName?: string}) | null;
    const tag = target && target !== (window as unknown as EventTarget) ? target.tagName : '';
    // 资源加载失败（img/script/link）交给 index.html 里的 chunk-404 自动强刷，不弹 toast
    if (tag && ['IMG', 'SCRIPT', 'LINK', 'VIDEO', 'AUDIO', 'SOURCE'].includes(tag)) return;
    const err = event.error as Error | undefined;
    const raw = err?.stack || err?.message || event.message || '';
    if (isBenign(raw)) return;
    console.error('[window.error]', event.error || event.message);
    report('运行时错误', raw);
  }, true);

  window.addEventListener('unhandledrejection', (event) => {
    const reason = (event as PromiseRejectionEvent).reason;
    const err = reason instanceof Error ? reason : new Error(String(reason ?? ''));
    if (isBenign(err.message)) return;
    console.error('[unhandledrejection]', reason);
    report('请求失败', err.stack || err.message);
  });
}
