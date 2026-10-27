/* ============================================================
   全局对话框 · Promise 化 API（替换 window.prompt/confirm/alert）
   ============================================================

   ★ 用户原话：「导入弹窗还是网页的  所以这些细节都要完善」

   ★ 为什么做成 Promise 而不是受控组件：
     原生 prompt/confirm 是**同步返回**的，所以调用点都写成
       const t = window.prompt('...'); if (!t) return;
     如果改成受控 state + 回调，每个调用点都要拆成两段（开弹窗 / 处理结果），
     16 处调用点会全部变形、极易漏判"取消"。用 Promise + `await` 后，
     调用点和原来几乎一字不差，替换风险最低。

   ★ 为什么用模块级单例（而不是 Context）：
     · 调用方可能是任意深层组件，也可能在事件回调里（不在 render 期）；
     · 同一时刻只应有一个对话框（原生行为也是如此），天然就是单例语义；
     · 不需要给整个应用套 Provider，接入成本为 0。
     React 侧只挂一个 <DialogHost />，它订阅单例的当前请求。

   ★ 并发安全：新请求进来时，先把上一个请求以"取消"结束，
     避免上一个 Promise 永远悬着（原生 prompt 也是模态的）。
   ============================================================ */

import {useCallback, useEffect, useRef, useState} from 'react';
import {AlertTriangle, CheckCircle2, HelpCircle, Info, X, type LucideIcon} from 'lucide-react';
import './gdialog.css';

export type DialogTone = 'default' | 'danger' | 'success';

/**
 * 头部图标类型（lucide 组件）。
 *
 * ★ 用 lucide 自己的 `LucideIcon` 而不是手写 `ComponentType<{size;strokeWidth}>`：
 *   lucide 的图标是 `ForwardRefExoticComponent<Omit<LucideProps,'ref'> & RefAttributes<SVGSVGElement>>`，
 *   手写的窄 props 类型不接受它（缺 ref/svg 属性），会报 TS2322。
 *   直接复用官方类型既准确又不用跟着 lucide 升级改签名。
 */
export type DialogIcon = LucideIcon;

export interface DialogOptions {
  /** 标题（必填）*/
  title: string;
  /** 正文说明，支持换行 */
  message?: string;
  /**
   * 头部图标（lucide 组件）。
   *
   * ★ 为什么需要（2026-10-25 用户截图「新建画布窗口要做精致丑死了」）：
   *   原来头部图标**只由 tone 决定**，而 tone 只有 default/danger/success 三档，
   *   default 落到 `HelpCircle` —— 于是"新建画布项目"的标题左边挂着一个**问号**，
   *   语义上像"帮助/提示"，完全不是"新建"。图标和功能对不上是这类窗口最刺眼的粗糙点。
   *   现在调用方可以传具体图标（新建用 FolderPlus、改名用 Pencil），
   *   不传才回落到 tone 的默认图标（alert 用问号是合理的）。
   */
  icon?: DialogIcon;
  /** 主按钮文案 */
  confirmText?: string;
  /** 取消按钮文案；不传则只有主按钮（alert 语义）*/
  cancelText?: string | null;
  tone?: DialogTone;
  /** prompt 用：输入框默认值。传了就是输入型对话框 */
  defaultValue?: string;
  /** prompt 用：输入框占位 */
  placeholder?: string;
  /** prompt 用：输入框下方补充说明（例如完整路径预览）*/
  inputHint?: string;
  /**
   * prompt 用：几个"一键填入"的快捷名字。
   *
   * ★ 为什么加：单输入框的对话框内容极薄，打开时只有一行预填文字 + 两个按钮，
   *   视觉上"空"就是廉价的来源。给 2~3 个常用名字做快捷片，
   *   既填满了节奏，又真的省一次输入（不用先全选再打字）。
   */
  suggestions?: string[];
  /** 是否显示右上角 X（默认 true）*/
  closable?: boolean;
}

type Mode = 'alert' | 'confirm' | 'prompt';

interface Request extends DialogOptions {
  id: number;
  mode: Mode;
  resolve: (v: string | boolean | null) => void;
}

type Listener = (req: Request | null) => void;

let seq = 0;
let current: Request | null = null;
const listeners = new Set<Listener>();

const emit = () => listeners.forEach((l) => l(current));

const open = (
  mode: Mode,
  opts: DialogOptions | string,
): Promise<string | boolean | null> => {
  const o: DialogOptions = typeof opts === 'string' ? {title: opts} : opts;
  // 上一个请求以"取消"收尾，防止 Promise 悬挂
  if (current) {
    current.resolve(mode === 'prompt' ? null : false);
    current = null;
  }
  return new Promise((resolve) => {
    current = {
      ...o,
      id: ++seq,
      mode,
      resolve: (v) => {
        // 只处理一次：resolve 后立刻从"当前"摘掉
        if (current && current.id === seq) current = null;
        resolve(v);
        emit();
      },
    };
    emit();
  });
};

export const dialog = {
  /** 提示型：只有「知道了」 */
  alert: (opts: DialogOptions | string): Promise<boolean> =>
    open('alert', opts).then((v) => v === true) as Promise<boolean>,
  /** 确认型：返回 true/false */
  confirm: (opts: DialogOptions | string): Promise<boolean> =>
    open('confirm', opts).then((v) => v === true) as Promise<boolean>,
  /** 输入型：返回字符串，取消返回 null */
  prompt: (opts: DialogOptions | string): Promise<string | null> =>
    open('prompt', opts) as Promise<string | null>,
};

const ICONS: Record<string, typeof Info> = {
  default: HelpCircle,
  danger: AlertTriangle,
  success: CheckCircle2,
};

/** 挂在应用根部（与 ToastHost 同级）。同一时刻只渲染一个对话框。 */
export function DialogHost() {
  const [req, setReq] = useState<Request | null>(current);
  const [value, setValue] = useState('');
  const [ready, setReady] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const primaryRef = useRef<HTMLButtonElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const l: Listener = (r) => {
      setReq(r);
      if (r) {
        setValue(r.mode === 'prompt' ? r.defaultValue ?? '' : '');
        setReady(r.mode !== 'prompt' || (r.defaultValue ?? '').length > 0);
      }
    };
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);

  // 打开后聚焦：输入型聚焦输入框并全选，其余聚焦主按钮
  useEffect(() => {
    if (!req) return;
    const t = setTimeout(() => {
      if (req.mode === 'prompt') {
        inputRef.current?.focus();
        inputRef.current?.select();
      } else {
        primaryRef.current?.focus();
      }
    }, 30);
    return () => clearTimeout(t);
  }, [req?.id]);

  const close = useCallback(
    (v: string | boolean | null) => {
      const r = current;
      current = null;
      if (r) r.resolve(v);
      emit();
    },
    [],
  );

  // 键盘：Esc 取消 / Enter 确认（输入型里 Enter 也确认，与原生一致）
  useEffect(() => {
    if (!req) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close(req.mode === 'prompt' ? null : false);
      } else if (e.key === 'Enter') {
        const t = e.target as HTMLElement;
        // textarea 里的 Enter 是换行，不拦截
        if (t && t.tagName === 'TEXTAREA') return;
        e.preventDefault();
        e.stopPropagation();
        if (req.mode === 'prompt') {
          if (value.trim()) close(value);
        } else {
          close(true);
        }
      }
    };
    // 捕获阶段：抢在 React Flow 的全局快捷键之前处理（否则 Delete/Esc 会被画布吃掉）
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [req?.id, value, close]);

  if (!req) return null;

  const tone = req.tone || 'default';
  const Icon = req.icon || ICONS[tone] || HelpCircle;
  const isDanger = tone === 'danger';
  const isPrompt = req.mode === 'prompt';
  const showCancel = req.cancelText !== null && req.mode !== 'alert';
  const confirmText =
    req.confirmText || (req.mode === 'alert' ? '知道了' : isDanger ? '确认删除' : '确定');
  const cancelText = req.cancelText || '取消';
  const canConfirm = !isPrompt || value.trim().length > 0;
  // 快捷名字：过滤掉与当前值相同的（点了没反应等于坏按钮）
  const sug = (req.suggestions || []).filter((s) => s && s !== value.trim());

  return (
    <div
      className="gdialog-mask"
      // 点遮罩 = 取消（原生不支持，但这是用户对自绘弹窗的普遍预期）
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close(isPrompt ? null : false);
      }}
    >
      <div
        className={`gdialog has-icon ${isDanger ? 'is-danger' : ''} ${isPrompt ? 'is-prompt' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="gdialog-title"
        ref={rootRef}
      >
        <div className="gdialog-head">
          <span className="gdialog-icon" aria-hidden="true">
            <Icon size={16} strokeWidth={1.9} />
          </span>
          <div className="gdialog-title-wrap">
            <h2 className="gdialog-title" id="gdialog-title">{req.title}</h2>
          </div>
          {req.closable !== false && (
            <button
              type="button"
              className="gdialog-close"
              aria-label="关闭"
              onClick={() => close(isPrompt ? null : false)}
            >
              <X size={14} strokeWidth={2.2} />
            </button>
          )}
        </div>

        {(req.message || isPrompt) && (
          <div className="gdialog-body">
            {req.message && <p className="gdialog-message">{req.message}</p>}
            {isPrompt && (
              <div className="gdialog-input-wrap">
                <input
                  ref={inputRef}
                  className="gdialog-input"
                  value={value}
                  placeholder={req.placeholder || ''}
                  spellCheck={false}
                  autoComplete="off"
                  aria-label={req.title}
                  onChange={(e) => {
                    setValue(e.target.value);
                    setReady(e.target.value.trim().length > 0);
                  }}
                />
                {/* 字符计数：只在接近/超过建议长度时变色。
                    文本类输入的"精致感"很大一部分来自"我知道还能打多少"。 */}
                <span
                  className={`gdialog-counter ${value.length > 40 ? 'is-over' : ''}`}
                  aria-hidden="true"
                >{value.length}<i>/40</i></span>
              </div>
            )}
            {/* 快捷名字：一行小片，点一下直接填入（省一次全选+打字） */}
            {isPrompt && sug.length > 0 && (
              <div className="gdialog-suggest">
                <span className="gdialog-suggest-label">试试</span>
                {sug.slice(0, 3).map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="gdialog-suggest-chip"
                    onClick={() => {
                      setValue(s);
                      setReady(true);
                      inputRef.current?.focus();
                    }}
                  >{s}</button>
                ))}
              </div>
            )}
            {req.inputHint && <p className="gdialog-input-hint">{req.inputHint}</p>}
          </div>
        )}

        <div className={`gdialog-foot ${isDanger ? 'is-split' : ''}`}>
          {isDanger && showCancel ? (
            <>
              <button type="button" className="gdialog-btn" onClick={() => close(false)}>
                {cancelText}
              </button>
              <div className="gdialog-foot-right">
                <button
                  type="button"
                  ref={primaryRef}
                  className="gdialog-btn is-danger"
                  disabled={!canConfirm}
                  onClick={() => close(true)}
                >
                  {confirmText}
                </button>
              </div>
            </>
          ) : (
            <>
              {showCancel && (
                <button type="button" className="gdialog-btn" onClick={() => close(isPrompt ? null : false)}>
                  {cancelText}
                </button>
              )}
              <button
                type="button"
                ref={primaryRef}
                className="gdialog-btn is-primary"
                disabled={!canConfirm || (isPrompt && !ready)}
                onClick={() => close(isPrompt ? value : true)}
              >
                {confirmText}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
