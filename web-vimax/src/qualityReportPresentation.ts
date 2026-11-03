import type {QualityReport} from './api';

export type QualityFailureDetail = {
  dimension: string;
  segmentNumber: number | null;
  segmentLabel: string;
  title: string;
  detail: string;
};

function stripSegmentPrefix(value: string): string {
  return value
    .replace(/^段\s*\d+\s*[·:：\-]?\s*/, '')
    .trim();
}

/**
 * Convert the quality report's compact dimensions into actionable failure
 * rows. The gate response already contains the segment title; losing it in the
 * header summary makes a failed check impossible to act on without reopening
 * every card.
 */
export function qualityFailureDetails(
  report: QualityReport | null | undefined,
): QualityFailureDetail[] {
  if (!report?.available || report.passed) return [];
  return Object.entries(report.dimensions || {})
    .filter(([, dimension]) => dimension && dimension.passed === false)
    .map(([dimensionName, dimension]) => {
      const match = /^段\s*(\d+)\s*·\s*挂接$/.exec(dimensionName);
      const segmentNumber = match ? Number(match[1]) : null;
      const reportedTitle = String(dimension.message || '').trim();
      const title = stripSegmentPrefix(reportedTitle)
        || (segmentNumber === null ? dimensionName : `第 ${segmentNumber} 段`);
      return {
        dimension: dimensionName,
        segmentNumber,
        segmentLabel: segmentNumber === null ? title : `段${segmentNumber} · ${title}`,
        title,
        detail: String(dimension.detail || '').trim(),
      };
    })
    .sort((left, right) => (
      (left.segmentNumber ?? Number.MAX_SAFE_INTEGER)
      - (right.segmentNumber ?? Number.MAX_SAFE_INTEGER)
    ));
}

export function qualityFailureSummary(
  failures: QualityFailureDetail[],
  limit = 3,
): string {
  if (!failures.length) return '';
  const labels = failures.slice(0, limit).map((failure) => failure.segmentLabel);
  const extra = failures.length > limit ? ` 等 ${failures.length} 段` : '';
  return `${labels.join('、')}${extra}`;
}
