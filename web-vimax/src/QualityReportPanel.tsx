import {useCallback, useEffect, useState} from 'react';
import {AlertTriangle, CheckCircle2, RefreshCw, XCircle} from 'lucide-react';
import {getQualityReport, recomputeQualityReport, type QualityReport} from './api';

// 维度顺序与中文标签（与 utils/quality_assessment.DIMENSION_WEIGHTS 对齐）
const DIMENSION_META: Array<{key: string; label: string}> = [
  {key: 'hook', label: '开场钩子'},
  {key: 'cliffhanger', label: '结尾留钩'},
  {key: 'pacing', label: '节奏分布'},
  {key: 'beats', label: '爆点密度'},
  {key: 'camera_language', label: '镜头语言'},
  {key: 'dialogue_density', label: '对白密度'},
  {key: 'dramatic_function', label: '戏剧功能'},
  {key: 'transition_variety', label: '转场多样'},
  {key: 'continuity', label: '连续性'},
];

function scoreTone(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 80) return 'good';
  if (score >= 60) return 'warn';
  return 'bad';
}

type Props = {
  session: string;
  epId?: string | null;
};

export function QualityReportPanel({session, epId}: Props) {
  const [report, setReport] = useState<QualityReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [recomputing, setRecomputing] = useState(false);

  const load = useCallback(() => {
    if (!session) return;
    setLoading(true);
    setError('');
    getQualityReport(session, epId)
      .then((data) => setReport(data))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [session, epId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleRecompute() {
    if (!session || recomputing) return;
    setRecomputing(true);
    setError('');
    try {
      const data = await recomputeQualityReport(session, epId);
      setReport(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRecomputing(false);
    }
  }

  if (loading) {
    return (
      <div className="qreport-loading">
        <div className="app-initializing-spinner" />
        <span>正在读取质量自评…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="inline-error qreport-error" role="alert">
        <span>{error}</span>
      </div>
    );
  }

  if (!report || !report.available) {
    return (
      <div className="qreport-empty">
        <span>尚未生成质量自评报告</span>
        <p>完成分镜生成后，pipeline 会自动落盘 quality_report.json，agent 也会在自主模式下主动自评。</p>
      </div>
    );
  }

  const overallTone = scoreTone(report.overall_score);
  const passed = report.passed;

  return (
    <div className="qreport">
      <div className="qreport-head">
        <div className={`qreport-score is-${overallTone}`}>
          <strong>{report.overall_score}</strong>
          <span>总分 / {report.gate_threshold} 门禁</span>
        </div>
        <div className="qreport-head-meta">
          <span className={`qreport-badge ${passed ? 'is-pass' : 'is-fail'}`}>
            {passed ? <CheckCircle2 size={14} aria-hidden="true" /> : <XCircle size={14} aria-hidden="true" />}
            {passed ? '达标 · 可进渲染' : '未达标 · 建议修订'}
          </span>
          <span className="qreport-sub">
            {report.shot_count} 镜 · {report.segment_count} 段
            {report.aggregated ? '（多集聚合）' : ''}
          </span>
          <button
            type="button"
            className="qreport-recompute"
            onClick={() => void handleRecompute()}
            disabled={recomputing}
            title="基于当前 storyboard.json 重新计算多维度评分"
          >
            <RefreshCw size={13} aria-hidden="true" className={recomputing ? 'is-spinning' : ''} />
            {recomputing ? '重新评估中…' : '重新评估'}
          </button>
        </div>
      </div>

      {report.stale && (
        <div className="qreport-stale">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>报告可能已过期（分镜比报告更新），点「重新评估」可刷新。</span>
        </div>
      )}

      <div className="qreport-bars">
        {DIMENSION_META.map(({key, label}) => {
          const dim = report.dimensions[key];
          if (!dim) return null;
          const tone = scoreTone(dim.score);
          return (
            <div className="qreport-bar-row" key={key}>
              <span className="qreport-bar-label" title={`权重 ${(dim.weight * 100).toFixed(0)}%`}>{label}</span>
              <span className="qreport-bar-track">
                <i className={`qreport-bar-fill is-${tone}`} style={{width: `${Math.max(0, Math.min(100, dim.score))}%`}} />
              </span>
              <span className={`qreport-bar-score is-${tone}`}>{dim.score}</span>
            </div>
          );
        })}
      </div>

      {report.summary && <p className="qreport-summary">{report.summary}</p>}

      {/* 结构性缺陷（阻断放行）：总分再高也不可进渲染，必须让用户一眼看到原因 */}
      {(report.blocking?.length ?? 0) > 0 && (
        <div className="qreport-blocking" role="alert">
          <strong>结构性缺陷（{report.blocking!.length}）· 总分不参与放行判定</strong>
          <ul>
            {report.blocking!.map((item, i) => (
              <li key={`blk-${i}`}>{item}</li>
            ))}
          </ul>
          <p>请先补齐上述字段（镜头语言 / 画面内容 / 段归属 / 时长），再点「重新评估」。</p>
        </div>
      )}

      {report.top_issues.length > 0 && (
        <div className="qreport-issues">
          <strong>待优化（{report.top_issues.length}）</strong>
          <ul>
            {report.top_issues.map((item, i) => (
              <li key={`iss-${i}`}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {report.revision_suggestions.length > 0 && (
        <div className="qreport-suggestions">
          <strong>修订建议</strong>
          <ul>
            {report.revision_suggestions.map((item, i) => (
              <li key={`sug-${i}`}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
