export type PromptScrollSnapshot = {
  scrollTop: number;
  atBottom: boolean;
};

type ScrollableEditor = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

export function capturePromptScroll(
  editor: ScrollableEditor,
): PromptScrollSnapshot {
  const maxScrollTop = Math.max(0, editor.scrollHeight - editor.clientHeight);
  return {
    scrollTop: Math.max(0, editor.scrollTop),
    atBottom: maxScrollTop - editor.scrollTop <= 2,
  };
}

export function restorePromptScroll(
  editor: ScrollableEditor,
  snapshot: PromptScrollSnapshot,
): void {
  const maxScrollTop = Math.max(0, editor.scrollHeight - editor.clientHeight);
  editor.scrollTop = snapshot.atBottom
    ? maxScrollTop
    : Math.min(maxScrollTop, Math.max(0, snapshot.scrollTop));
}
