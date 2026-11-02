import {describe, expect, it} from 'vitest';
import {
  reconcileSelectedProjectId,
  shouldClearEpisodeOnProjectRestore,
} from './projectSelection';

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

  it('selects the first project after refreshing with no current selection', () => {
    expect(reconcileSelectedProjectId('', ['a', 'b'], '')).toBe('a');
  });

  it('does not clear the episode when restoring the same project from a hash', () => {
    expect(shouldClearEpisodeOnProjectRestore('', 'b')).toBe(false);
    expect(shouldClearEpisodeOnProjectRestore('b', 'b')).toBe(false);
    expect(shouldClearEpisodeOnProjectRestore('a', 'b')).toBe(true);
  });
});
