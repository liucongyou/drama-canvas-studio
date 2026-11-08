import {describe, expect, it} from 'vitest';
import {clampDurationInput} from './durationInput';

describe('duration input clamping', () => {
  it('clamps values above the model maximum', () => {
    expect(clampDurationInput('999', 4, 15, 15)).toBe(15);
  });

  it('clamps values below the model minimum', () => {
    expect(clampDurationInput('-1', 4, 15, 15)).toBe(4);
  });

  it('keeps the last valid value for an empty or malformed input', () => {
    expect(clampDurationInput('', 4, 15, 12)).toBe(12);
    expect(clampDurationInput('abc', 4, 15, 12)).toBe(12);
  });

  it('keeps a valid duration', () => {
    expect(clampDurationInput('12', 4, 15, 15)).toBe(12);
  });
});
