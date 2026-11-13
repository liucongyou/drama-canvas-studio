import {describe, expect, it} from 'vitest';
import {
  capturePromptScroll,
  restorePromptScroll,
} from './promptScrollAnchor';

describe('prompt scroll anchoring', () => {
  it('keeps a bottom-anchored editor at the bottom after deleting text', () => {
    const editor = {
      scrollTop: 520,
      scrollHeight: 900,
      clientHeight: 380,
    };
    const snapshot = capturePromptScroll(editor);
    editor.scrollHeight = 860;
    editor.scrollTop = 0;

    restorePromptScroll(editor, snapshot);

    expect(editor.scrollTop).toBe(480);
  });

  it('preserves a middle scroll position when the user is not reading the bottom', () => {
    const editor = {
      scrollTop: 260,
      scrollHeight: 900,
      clientHeight: 380,
    };
    const snapshot = capturePromptScroll(editor);
    editor.scrollHeight = 820;
    editor.scrollTop = 0;

    restorePromptScroll(editor, snapshot);

    expect(editor.scrollTop).toBe(260);
  });

  it('clamps safely when the content no longer overflows', () => {
    const editor = {
      scrollTop: 300,
      scrollHeight: 700,
      clientHeight: 380,
    };
    const snapshot = capturePromptScroll(editor);
    editor.scrollHeight = editor.clientHeight;
    editor.scrollTop = 40;

    restorePromptScroll(editor, snapshot);

    expect(editor.scrollTop).toBe(0);
  });
});
