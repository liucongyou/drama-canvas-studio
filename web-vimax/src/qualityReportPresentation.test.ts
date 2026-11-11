import {describe, expect, it} from 'vitest';
import type {QualityReport} from './api';
import {
  qualityFailureDetails,
  qualityFailureSummary,
  qualityCheckPassed,
  qualityStatusPresentation,
} from './qualityReportPresentation';

function report(patch: Partial<QualityReport>): QualityReport {
  return {
    available: true,
    overall_score: 0,
    passed: false,
    gate_threshold: 80,
    shot_count: 0,
    segment_count: 0,
    dimensions: {},
    levels: {fail: 0, warn: 0, info: 0},
    summary: '',
    top_issues: [],
    revision_suggestions: [],
    ...patch,
  };
}

describe('quality gate status presentation', () => {
  it('names each failed segment and keeps the concrete mismatch reason', () => {
    const failures = qualityFailureDetails(report({
      segment_count: 8,
      levels: {fail: 2, warn: 0, info: 0},
      dimensions: {
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

  it('builds a bounded failure summary for the toast', () => {
    const failures = qualityFailureDetails(report({
      segment_count: 4,
      levels: {fail: 4, warn: 0, info: 0},
      dimensions: {
        '段1·挂接': {score: 0, weight: 1, passed: false, message: '段1 · 买断喇叭'},
        '段2·挂接': {score: 0, weight: 1, passed: false, message: '段2 · 别蹚浑水'},
        '段3·挂接': {score: 0, weight: 1, passed: false, message: '段3 · 念一遍'},
        '段4·挂接': {score: 0, weight: 1, passed: false, message: '段4 · 改制'},
      },
    }));
    expect(qualityFailureSummary(failures, 2))
      .toBe('段1 · 买断喇叭、段2 · 别蹚浑水 等 4 段');
  });

  it('does not describe an empty gate as zero failed segments', () => {
    const empty = report({segment_count: 0, passed: false});
    expect(qualityStatusPresentation(empty)).toEqual({
      label: '挂接待生成',
      tone: 'pending',
    });
    expect(qualityCheckPassed(empty)).toBe(true);
  });

  it('keeps real failed segments red', () => {
    const failed = report({
      segment_count: 2,
      levels: {fail: 1, warn: 0, info: 0},
    });
    expect(qualityStatusPresentation(failed)).toEqual({
      label: '挂接 1 段未通过',
      tone: 'fail',
    });
    expect(qualityCheckPassed(failed)).toBe(false);
  });

  it('shows a passed gate as green', () => {
    const passed = report({segment_count: 2, shot_count: 2, passed: true});
    expect(qualityStatusPresentation(passed)).toEqual({
      label: '挂接通过',
      tone: 'pass',
    });
    expect(qualityCheckPassed(passed)).toBe(true);
  });
});
