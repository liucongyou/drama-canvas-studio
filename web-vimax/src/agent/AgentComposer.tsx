import {useCallback, useEffect, useRef, type DragEvent, type KeyboardEvent, type ReactNode} from 'react';
import {Send, Square} from 'lucide-react';

type AgentComposerProps = {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  running?: boolean;
  disabled?: boolean;
  toolbar?: ReactNode;
  chips?: ReactNode;
  onUploadFiles?: (files: File[]) => void;
  placeholder?: string;
};

export function shouldSubmitFromKey(event: Pick<KeyboardEvent<HTMLTextAreaElement>, 'key' | 'ctrlKey' | 'metaKey' | 'nativeEvent'>, composing: boolean): boolean {
  const native = event.nativeEvent as KeyboardEvent<HTMLTextAreaElement>['nativeEvent'] & {isComposing?: boolean; keyCode?: number};
  return event.key === 'Enter'
    && Boolean(event.ctrlKey || event.metaKey)
    && !composing
    && !native.isComposing
    && native.keyCode !== 229;
}

export function AgentComposer({
  value,
  onChange,
  onSubmit,
  onStop,
  running = false,
  disabled = false,
  toolbar,
  chips,
  onUploadFiles,
  placeholder = '输入剧本、修改要求或创作目标，Ctrl+Enter 发送',
}: AgentComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composingRef = useRef(false);
  const submitLockRef = useRef(false);

  const autoGrow = useCallback(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = Math.max(64, Math.min(160, element.scrollHeight)) + 'px';
  }, []);

  useEffect(() => {
    autoGrow();
  }, [autoGrow, value]);

  const submit = useCallback(() => {
    if (submitLockRef.current || disabled || !value.trim()) return;
    submitLockRef.current = true;
    try {
      onSubmit();
    } finally {
      window.setTimeout(() => { submitLockRef.current = false; }, 120);
    }
  }, [disabled, onSubmit, value]);

  const drop = (event: DragEvent<HTMLDivElement>) => {
    if (!onUploadFiles || !event.dataTransfer.files.length) return;
    event.preventDefault();
    onUploadFiles(Array.from(event.dataTransfer.files));
  };

  return (
    <div className="agent-composer">
      <div
        className="agent-input-box"
        onDragOver={(event) => {
          if (onUploadFiles) event.preventDefault();
        }}
        onDrop={drop}
      >
        {chips && <div className="agent-inline-chips">{chips}</div>}
        <textarea
          ref={textareaRef}
          value={value}
          rows={2}
          autoFocus
          placeholder={placeholder}
          disabled={disabled && !running}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => { composingRef.current = false; }}
          onChange={(event) => {
            onChange(event.target.value);
            window.requestAnimationFrame(autoGrow);
          }}
          onPaste={(event) => {
            if (!onUploadFiles) return;
            const files = Array.from(event.clipboardData.files || []);
            if (files.length) {
              event.preventDefault();
              onUploadFiles(files);
            }
          }}
          onKeyDown={(event) => {
            if (!shouldSubmitFromKey(event, composingRef.current)) return;
            event.preventDefault();
            submit();
          }}
        />
        <div className="agent-tools">
          {toolbar}
          {running && onStop ? (
            <button type="button" className="agent-send is-stop" title="停止" aria-label="停止" onClick={onStop}>
              <Square size={13} />
            </button>
          ) : (
            <button type="button" className="agent-send" title="发送" aria-label="发送" onClick={submit} disabled={disabled || !value.trim()}>
              <Send size={14} />
            </button>
          )}
        </div>
      </div>
      <div className="agent-foot">内容由 AI 生成，请检查后使用</div>
    </div>
  );
}
