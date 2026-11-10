import {describe, expect, it} from 'vitest';
import {shouldSubmitFromKey} from './AgentComposer';

describe('AgentComposer keyboard handling', () => {
  it('submits Ctrl+Enter outside composition', () => {
    expect(shouldSubmitFromKey({
      key: 'Enter',
      ctrlKey: true,
      metaKey: false,
      nativeEvent: {isComposing: false, keyCode: 13},
    } as never, false)).toBe(true);
  });

  it('does not submit during Chinese IME composition', () => {
    expect(shouldSubmitFromKey({
      key: 'Enter',
      ctrlKey: true,
      metaKey: false,
      nativeEvent: {isComposing: true, keyCode: 229},
    } as never, true)).toBe(false);
  });
});
