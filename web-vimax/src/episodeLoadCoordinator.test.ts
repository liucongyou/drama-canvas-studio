import {describe, expect, it} from 'vitest';
import {
  episodeLoadDecision,
  episodeSelectionDecision,
  nextEpisodeRequest,
  normalizeEpisodeId,
  shouldPublishEpisodeLoad,
} from './episodeLoadCoordinator';

describe('episodeLoadDecision', () => {
  it('loads a newly selected episode', () => {
    expect(episodeLoadDecision({
      target: 'EP007',
      active: 'EP001',
      current: 'EP007',
    })).toBe('load');
  });

  it('does not reload the already active episode', () => {
    expect(episodeLoadDecision({
      target: 'EP007',
      active: 'EP007',
      current: 'EP007',
    })).toBe('already-active');
  });

  it('ignores an effect run that no longer matches the latest selection', () => {
    expect(episodeLoadDecision({
      target: 'EP002',
      active: 'EP001',
      current: 'EP007',
    })).toBe('ignore');
  });

  it('keeps retrying the selected episode after a failed request', () => {
    expect(episodeLoadDecision({
      target: 'EP007',
      active: 'EP007',
      current: 'EP007',
      pending: '',
    })).toBe('already-active');
    expect(episodeLoadDecision({
      target: 'EP007',
      active: 'EP001',
      current: 'EP007',
    })).toBe('load');
  });
});

describe('episodeSelectionDecision', () => {
  it('lets the initial workspace request own the default episode', () => {
    expect(episodeSelectionDecision({
      explicitSelection: false,
      target: 'EP001',
      active: '',
      current: 'EP001',
    })).toBe('ignore');
  });

  it('loads once the user explicitly selects a different episode', () => {
    expect(episodeSelectionDecision({
      explicitSelection: true,
      target: 'EP003',
      active: 'EP001',
      current: 'EP003',
    })).toBe('load');
  });

  it('does not duplicate the request after the selected episode is active', () => {
    expect(episodeSelectionDecision({
      explicitSelection: true,
      target: 'EP003',
      active: 'EP003',
      current: 'EP003',
    })).toBe('already-active');
  });
});

describe('shouldPublishEpisodeLoad', () => {
  it('publishes only the latest matching request', () => {
    expect(shouldPublishEpisodeLoad({
      requestEpoch: 8,
      latestEpoch: 8,
      target: 'EP007',
      current: 'ep007',
    })).toBe(true);
  });

  it('suppresses an earlier request that finished later', () => {
    expect(shouldPublishEpisodeLoad({
      requestEpoch: 7,
      latestEpoch: 8,
      target: 'EP002',
      current: 'EP007',
    })).toBe(false);
  });

  it('treats an intentional abort as superseded, never as timeout', () => {
    expect(shouldPublishEpisodeLoad({
      requestEpoch: 8,
      latestEpoch: 8,
      target: 'EP007',
      current: 'EP007',
      aborted: true,
    })).toBe(false);
  });

  it('normalizes whitespace and case', () => {
    expect(normalizeEpisodeId(' ep007 ')).toBe('EP007');
    expect(normalizeEpisodeId(null)).toBe('');
  });
});

describe('nextEpisodeRequest', () => {
  it('increments sequence for a different target', () => {
    expect(nextEpisodeRequest({requestEpoch: 3, target: 'EP001'}, 'EP007')).toEqual({
      requestEpoch: 4,
      target: 'EP007',
    });
  });

  it('does not reset the epoch when an old effect re-runs', () => {
    const previous = {requestEpoch: 8, target: 'EP007'};
    expect(nextEpisodeRequest(previous, 'EP002')).toEqual({
      requestEpoch: 9,
      target: 'EP002',
    });
    expect(previous).toEqual({requestEpoch: 8, target: 'EP007'});
  });
});
