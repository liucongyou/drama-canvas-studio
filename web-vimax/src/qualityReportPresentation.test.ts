import {describe, expect, it} from 'vitest';
import type {QualityReport} from './api';
import {
  qualityFailureDetails,
  qualityFailureSummary,
} from './qualityReportPresentation';

function report(
  dimensions: QualityReport['dimensions'],
  passed = false,
): QualityReport {
  return {
    available: true,
    overall_score: passed ? 100 : 0,
    passed,
    gate_threshold: 80,
    shot_count: 8,
    segment_count: 8,
    levels: {fail: passed ? 0 : 2},
    dimensions,
    summary: '',
    top_issues: [],
    revision_suggestions: [],
  };
}

describe('quality report presentation', () => {
  it('names each failed segment and keeps the concrete mismatch reason', () => {
    const failures = qualityFailureDetails(report({
      '段3·挂接': {
        score: 0,
        weight: 1,
        passed: false,
        message: '段3 · 念一遍',
        detail: '画面引用不一致；音色挂接不一致',
      },
      '段1·挂接': {
        score: 100,
        weight: 1,
        passed: true,
        message: '段1 · 买断喇叭',
      },
      '段5·挂接': {
        score: 0,
        weight: 1,
        passed: false,
        message: '段5 · 账本在哪',
      },
    }));

    expect(failures).toHaveLength(2);
    expect(failures[0]).toMatchObject({
      segmentNumber: 3,
      segmentLabel: '段3 · 念一遍',
      title: '念一遍',
      detail: '画面引用不一致；音色挂接不一致',
    });
    expect(failures[1].segmentLabel).toBe('段5 · 账本在哪');
  });

  it('does not render a failure list for a passing or unavailable report', () => {
    expect(qualityFailureDetails(report({
      '段1·挂接': {
        score: 100,
        weight: 1,
        passed: true,
        message: '段1 · 买断喇叭',
      },
    }, true))).toEqual([]);
    expect(qualityFailureDetails(null)).toEqual([]);
  });

  it('builds a bounded summary for toast and compact labels', () => {
    const failures = qualityFailureDetails(report({
      '段1·挂接': {
        score: 0,
        weight: 1,
        passed: false,
        message: '段1 · 买断喇叭',
      },
      '段2·挂接': {
        score: 0,
        weight: 1,
        passed: false,
        message: '段2 · 别蹚浑水',
      },
      '段3·挂接': {
        score: 0,
        weight: 1,
        passed: false,
        message: '段3 · 念一遍',
      },
      '段4·挂接': {
        score: 0,
        weight: 1,
        passed: false,
        message: '段4 · 改制',
      },
    }));
    expect(qualityFailureSummary(failures, 2))
      .toBe('段1 · 买断喇叭、段2 · 别蹚浑水 等 4 段');
  });
});
