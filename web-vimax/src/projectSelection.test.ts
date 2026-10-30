import {describe, expect, it} from 'vitest';
import {reconcileSelectedProjectId} from './projectSelection';

describe('reconcileSelectedProjectId', () => {
  it('keeps the current project while it still exists', () => {
    expect(reconcileSelectedProjectId('b', ['a', 'b'], 'a')).toBe('b');
  });

  it('moves away from a project deleted by another tab', () => {
    expect(reconcileSelectedProjectId('deleted', ['a', 'b'], '')).toBe('a');
  });

  it('honors a valid hash before falling back to the first project', () => {
    expect(reconcileSelectedProjectId('deleted', ['a', 'b'], 'b')).toBe('b');
  });

  it('clears selection when no projects remain', () => {
    expect(reconcileSelectedProjectId('deleted', [], '')).toBe('');
  });
});
