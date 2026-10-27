import {useCallback, useEffect, useState, type ReactNode} from 'react';
import {dialog} from './GDialog';
import {BarChart3, Clock3, Database, Film, Image as ImageIcon, Mic, Monitor, RefreshCw, Trash2, Wallet} from 'lucide-react';
import {getUsage, type UsageEntry, type UsageSummary} from './api';
import type {SessionSummary} from './types';

const PURPOSE_LABELS: Record<string, string> = {
  character_portrait: '角色立绘',
  scene_asset: '场景图',
  prop_asset: '道具图',
  keyframe_candidate: '关键帧候选',
  shot_video: '正片镜头',
  transition_video: '转场视频',
  character_voice: '角色音色',
  novel_base_portrait: '小说基础立绘',
  novel_scene_portrait: '小说场次立绘',
  other: '其他',
};

const KIND_LABELS: Record<string, string> = {image: '图像', video: '视频', audio: '音色'};

type RecentEntryGroup = {
  key: string;
  label: string;
  entries: UsageEntry[];
  cost: number;
};

// 估算单价（元），按平台公开报价
const UNIT_PRICE = {
  image: 0.3, // Seedream-5.0-pro 输出图 ¥0.30/张
  // Seedance-2.0-mini 实际平台报价（无视频输入）：480P 10s=¥2.31，720P 10s=¥4.99
  video_per_sec: {'480p': 0.231, '720p': 0.499, '1080p': 1.0, unknown: 0.499} as Record<string, number>,
  audio: 0, // 声音设计创建音色免费
};
const AUDIO_TTS_PRICE_PER_10K_CHARS = 1.5;

function videoRateOf(resolution: string): number {
  return UNIT_PRICE.video_per_sec[resolution] ?? UNIT_PRICE.video_per_sec.unknown;
}

function entryCost(entry: UsageEntry): number {
  if (entry.kind === 'audio') return ((entry.tts_chars || 0) / 10000) * AUDIO_TTS_PRICE_PER_10K_CHARS;
  if (entry.kind === 'video') {
    // 按实际生成秒数计费
    const secs = Number(entry.duration) || 0;
    return videoRateOf(entry.resolution) * secs;
  }
  return UNIT_PRICE.image * entry.quantity;
}

function totalCost(usage: UsageSummary): number {
  return imageCost(usage) + videoCost(usage) + audioCost(usage);
}

function imageCost(usage: UsageSummary): number {
  return usage.image.count * UNIT_PRICE.image;
}

function videoCost(usage: UsageSummary): number {
  return usage.video.duration_sec * videoRateOf('720p'); // 汇总按主分辨率粗估，明细按实际
}

function audioCost(usage: UsageSummary): number {
  return ((usage.audio.tts_chars || 0) / 10000) * AUDIO_TTS_PRICE_PER_10K_CHARS;
}

function formatClock(ts: number): string {
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function localDayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function formatDayLabel(ts: number): string {
  const date = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (localDayKey(date) === localDayKey(today)) return '今天';
  if (localDayKey(date) === localDayKey(yesterday)) return '昨天';
  if (date.getFullYear() !== today.getFullYear()) return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function groupRecentEntries(entries: UsageEntry[]): RecentEntryGroup[] {
  const groups: RecentEntryGroup[] = [];
  for (const entry of entries) {
    const date = new Date(entry.ts * 1000);
    const key = localDayKey(date);
    const previous = groups.at(-1);
    if (!previous || previous.key !== key) {
      groups.push({key, label: formatDayLabel(entry.ts), entries: [entry], cost: entryCost(entry)});
      continue;
    }
    previous.entries.push(entry);
    previous.cost += entryCost(entry);
  }
  return groups;
}

function UsageKindIcon({kind}: {kind: UsageEntry['kind']}) {
  if (kind === 'image') return <ImageIcon size={15} />;
  if (kind === 'video') return <Film size={15} />;
  return <Mic size={15} />;
}

function DistributionBars({items, color}: {items: Array<[string, number]>; color: string}) {
  const max = Math.max(1, ...items.map(([, count]) => count));
  return (
    <div className="budget-bars">
      {items.map(([label, count]) => (
        <div className="budget-bar-row" key={label}>
          <span className="budget-bar-label" title={label}>{PURPOSE_LABELS[label] ?? label}</span>
          <div className="budget-bar-track">
            <div className="budget-bar-fill" style={{width: `${Math.max(4, (count / max) * 100)}%`, background: color}} />
          </div>
          <span className="budget-bar-count">{count}</span>
        </div>
      ))}
    </div>
  );
}

type ModelCount = {name: string; ref: string; count: number};

/** 模型分布：双行显示“真实模型名 + 端点 ID”，避免长名称被截断。 */
function ModelDistributionBars({items, color}: {items: ModelCount[]; color: string}) {
  const max = Math.max(1, ...items.map((item) => item.count));
  return (
    <div className="budget-bars">
      {items.map((item) => (
        <div className="budget-bar-row is-stacked" key={`${item.name}::${item.ref}`}>
          <div className="budget-bar-head">
            <span className="budget-bar-label" title={item.name}>{item.name}</span>
            {item.ref && <small className="budget-bar-ref" title={item.ref}>{item.ref}</small>}
            <span className="budget-bar-count">{item.count}</span>
          </div>
          <div className="budget-bar-track">
            <div className="budget-bar-fill" style={{width: `${Math.max(4, (item.count / max) * 100)}%`, background: color}} />
          </div>
        </div>
      ))}
    </div>
  );
}

function DailyChart({days}: {days: UsageSummary['breakdown']['daily']}) {
  const max = Math.max(1, ...days.map((day) => day.count));
  return (
    <div className="budget-daily">
      {days.map((day) => (
        <div className="budget-daily-col" key={day.date}>
          <div className="budget-daily-stack">
            {day.audio > 0 && <div className="budget-daily-seg is-audio" style={{height: `${(day.audio / max) * 100}%`}} />}
            {day.video > 0 && <div className="budget-daily-seg is-video" style={{height: `${(day.video / max) * 100}%`}} />}
            {day.image > 0 && <div className="budget-daily-seg is-image" style={{height: `${(day.image / max) * 100}%`}} />}
          </div>
          <span className="budget-daily-date">{day.date.slice(5)}</span>
          <span className="budget-daily-count">{day.count}</span>
        </div>
      ))}
    </div>
  );
}

function StatCard({icon, label, value, hint, tone}: {icon: ReactNode; label: string; value: string; hint?: string; tone?: 'total'}) {
  return (
    <div className={`budget-card ${tone === 'total' ? 'is-total' : ''}`}>
      <div className="budget-card-icon">{icon}</div>
      <div className="budget-card-copy">
        <span>{label}</span>
        <strong>{value}</strong>
        {hint && <em>{hint}</em>}
      </div>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

type EpisodeProgress = {
  ep_id: string;
  title: string;
  /** 按集资产状态（Python 侧唯一口径落盘，前端只读）：null/缺省 = 未知 */
  assets_ready?: boolean | null;
  assets_missing_count?: number | null;
  assets_blocking?: string[];
  storyboard_ready: boolean;
  shot_count: number | null;
  keyframes: number;
  clips: number;
  segments_total?: number | null;
  segments_rendered?: number | null;
  partial_render?: boolean;
  final_video_exists?: boolean;
  render_gap?: string | null;
  final_ready: boolean;
  status: string;
};

type DiskGroup = {bytes: number; files: number};

// P2-2：按集进度地图——80 集长剧在预算页一眼看全集进度
const EP_STATUS_META: Record<string, {label: string; className: string}> = {
  rendered: {label: '完成', className: 'is-rendered'},
  rendering: {label: '渲染中', className: 'is-rendering'},
  // 残缺成片：final_video.mp4 在盘（能播）但缺段/缺镜（段级直出实测 ep01 声明 14 段、只落 7 段）。
  // 状态字符串与 utils/episode_completeness.py 的 STATUS_PARTIAL_RENDERED 对齐；'partial' 是旧写法，归一档。
  partial_rendered: {label: '残缺成片', className: 'is-partial'},
  partial: {label: '残缺成片', className: 'is-partial'},
  error: {label: '失败', className: 'is-error'},
  pending: {label: '待处理', className: 'is-pending'},
  // 本集资产还没齐（"一集一集"流程里第一步要处理的事）：不能笼统显示"待处理"
  assets_missing: {label: '待补资产', className: 'is-assets-missing'},
};

function episodeStatusOf(ep: EpisodeProgress): {label: string; className: string} {
  // 磁盘实况压过状态字符串：残缺成片必须先判，且排在 storyboard_ready 之前——
  // 否则只渲了一半的集会落到「已分镜」分支，显示成"还没开始渲染"。
  if (ep.partial_render || ep.status === 'partial_rendered') return EP_STATUS_META.partial_rendered;
  if (ep.status === 'rendered') return EP_STATUS_META.rendered;
  if (ep.status === 'rendering') return EP_STATUS_META.rendering;
  if (ep.status === 'partial') return EP_STATUS_META.partial;
  if (ep.status === 'error') return EP_STATUS_META.error;
  // 本集缺身份级资产（角色立绘/场景图）：缺了就不能分镜、不能出片，必须排在"已分镜"之前说明白
  if (ep.assets_ready === false && !ep.final_ready) return EP_STATUS_META.assets_missing;
  if (ep.storyboard_ready) return {label: '已分镜', className: 'is-planned'};
  return EP_STATUS_META.pending;
}

function EpisodeProgressMap({episodes, onRefresh, loading}: {episodes: EpisodeProgress[]; onRefresh: () => void; loading?: boolean}) {
  if (episodes.length === 0) {
    return (
      <div className="budget-chart-card episode-progress-map is-empty">
        <header className="budget-table-header">
          <div>
            <h2><Film size={15} />按集进度地图</h2>
            <p>尚未拆分任何集，或还没有集级进度数据（先跑剧本解析/按集分镜后这里会按集显示进度）</p>
          </div>
          <button className="icon-button" onClick={onRefresh} aria-label="刷新集进度" title="刷新集进度">
            <RefreshCw size={15} className={loading ? 'is-spinning' : ''} />
          </button>
        </header>
        <div className="episode-progress-empty">暂无集进度数据</div>
      </div>
    );
  }
  const counts = {rendered: 0, rendering: 0, partial: 0, error: 0, planned: 0, pending: 0, assets: 0};
  episodes.forEach((ep) => {
    const status = episodeStatusOf(ep);
    if (status.className === 'is-rendered') counts.rendered += 1;
    else if (status.className === 'is-rendering') counts.rendering += 1;
    else if (status.className === 'is-partial') counts.partial += 1;
    else if (status.className === 'is-error') counts.error += 1;
    else if (status.className === 'is-assets-missing') counts.assets += 1;
    else if (status.className === 'is-planned') counts.planned += 1;
    else counts.pending += 1;
  });
  return (
    <div className="budget-chart-card episode-progress-map">
      <header className="budget-table-header">
        <div>
          <h2><Film size={15} />按集进度地图</h2>
          <p>共 {episodes.length} 集 · 完成 {counts.rendered} · 待补资产 {counts.assets} · 残缺成片 {counts.partial} · 渲染中 {counts.rendering} · 失败 {counts.error} · 已分镜 {counts.planned} · 待处理 {counts.pending}</p>
        </div>
        <button className="icon-button" onClick={onRefresh} aria-label="刷新集进度" title="刷新集进度">
          <RefreshCw size={15} className={loading ? 'is-spinning' : ''} />
        </button>
      </header>
      <div className="episode-progress-legend">
        <span className="is-rendered">完成</span>
        <span className="is-assets-missing">待补资产</span>
        <span className="is-partial">残缺成片</span>
        <span className="is-rendering">渲染中</span>
        <span className="is-planned">已分镜</span>
        <span className="is-pending">待处理</span>
        <span className="is-error">失败</span>
      </div>
      <div className="episode-progress-grid">
        {episodes.map((ep) => {
          const status = episodeStatusOf(ep);
          const missing = ep.assets_missing_count ?? 0;
          // 已出片的集不算"待补资产"（成片阶段已越过资产前置）：否则格子文案自相矛盾
          const assetsMissing = ep.assets_ready === false && !ep.final_ready;
          return (
            <div className={`episode-progress-cell ${status.className}`} key={ep.ep_id} title={`${ep.ep_id} ${ep.title || ''}：${status.label}${ep.render_gap ? ` · ${ep.render_gap}` : ''}${assetsMissing ? ` · 缺资产 ${missing} 项（${(ep.assets_blocking ?? []).slice(0, 8).join('、')}）` : ''}`}>
              <strong>{ep.ep_id.replace(/^ep/i, '')}</strong>
              <em>{status.label}</em>
              <span>
                {assetsMissing
                  ? `缺资产 ${missing} 项`
                  : ep.storyboard_ready
                    ? `${ep.shot_count ?? '-'} 镜`
                    : '未分镜'}
                {ep.final_ready
                  ? ' · 成片✓'
                  : ep.partial_render
                    ? ` · 残缺段 ${ep.segments_rendered ?? 0}/${ep.segments_total ?? '-'}`
                    : ep.clips > 0
                      ? ` · ${ep.clips} 片段`
                      : ep.keyframes > 0
                        ? ` · ${ep.keyframes} 帧`
                        : ''}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DiskUsageSection({session, onChanged}: {session: SessionSummary; onChanged: () => void}) {
  const [disk, setDisk] = useState<{totalBytes: number; totalFiles: number; groups: Record<string, DiskGroup>} | null>(null);
  const [episodes, setEpisodes] = useState<EpisodeProgress[]>([]);
  const [cleaning, setCleaning] = useState('');
  const [message, setMessage] = useState('');
  // 统计失败必须可见：此前静默吞掉异常，卡片会永远停在「正在统计磁盘占用…」，
  // 用户以为一直在算，实际请求早就挂了。
  const [diskError, setDiskError] = useState('');

  const load = useCallback(() => {
    let cancelled = false;
    setDiskError('');
    fetch(`/api/disk?session=${encodeURIComponent(session.sessionId)}`)
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
      .then((data) => { if (!cancelled && data) setDisk(data); })
      .catch((error) => { if (!cancelled) setDiskError(`磁盘统计失败：${error?.message || '请求异常'}`); });
    fetch(`/api/episodes?session=${encodeURIComponent(session.sessionId)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (!cancelled && data?.episodes) setEpisodes(data.episodes); })
      .catch(() => { /* 静默 */ });
    return () => { cancelled = true; };
  }, [session.sessionId]);

  useEffect(() => { load(); }, [load]);

  async function cleanup(epId: string) {
    const ok = await dialog.confirm({
      title: `清理第 ${epId} 集的渲染产物？`,
      message: '将删除该集的**关键帧 / 片段 / 成片**。\n\n剧本、分镜等文本规划与项目级资产（立绘 / 场景 / 道具 / 音色）都会保留 —— 确认后重新渲染即可恢复该集成片。',
      confirmText: '清理产物',
      tone: 'danger',
    });
    if (!ok) return;
    setCleaning(epId);
    setMessage('');
    try {
      const response = await fetch(`/api/cleanup-episode?session=${encodeURIComponent(session.sessionId)}&ep=${encodeURIComponent(epId)}`, {method: 'DELETE'});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error || '清理失败');
      setMessage(`✅ ${epId} 已清理，释放 ${formatBytes(payload.freed_bytes)}`);
      load();
      onChanged();
    } catch (error) {
      setMessage(`❌ 清理失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setCleaning('');
    }
  }

  const episodeBytes = new Map<string, number>();
  if (disk?.groups?.episodes?.bytes) {
    // episodes 目录整体大小在 groups.episodes，无法拆分到每集——不做虚假拆分，总览展示整体
  }
  const episodeGroup = disk?.groups?.episodes as DiskGroup | undefined;
  const otherGroups = disk ? Object.entries(disk.groups).filter(([name]) => name !== 'episodes') : [];

  return (
    <div className="budget-chart-card budget-disk-card">
      <header className="budget-table-header">
        <div>
          <h2><Database size={15} />磁盘占用</h2>
          <p>项目工作区实际占用的磁盘空间（图像/视频/音色等产物文件）</p>
        </div>
        <button className="icon-button" onClick={() => load()} aria-label="刷新磁盘统计" title="刷新磁盘统计">
          <RefreshCw size={16} className={cleaning ? 'is-spinning' : ''} />
        </button>
      </header>
      {message && <div className="budget-disk-message">{message}</div>}
      {!disk ? (
        diskError ? (
          <div className="budget-empty is-error" role="alert">
            <span>{diskError}</span>
            <button type="button" className="is-retry" onClick={() => load()}>重试</button>
          </div>
        ) : (
          <div className="view-loading">正在统计磁盘占用…</div>
        )
      ) : (
        <>
          <div className="budget-disk-total">
            <strong>{formatBytes(disk.totalBytes)}</strong>
            <span>共 {disk.totalFiles} 个文件</span>
          </div>
          {episodeGroup && episodeGroup.bytes > 0 && (
            <div className="budget-disk-group">
              <header>
                <span>episodes（各集渲染产物）</span>
                <em>{formatBytes(episodeGroup.bytes)} · {episodeGroup.files} 个文件</em>
              </header>
              <ul className="budget-disk-episode-list">
                {episodes.map((ep) => {
                  const status = episodeStatusOf(ep);
                  const hasArtifacts = Boolean(ep.keyframes || ep.clips || ep.final_video_exists || ep.final_ready);
                  return (
                    <li key={ep.ep_id} className={status.className} title={ep.render_gap ?? undefined}>
                      <div className="budget-disk-episode-copy">
                        <strong>{ep.ep_id}</strong>
                        <span title={ep.title}>
                          {ep.storyboard_ready ? `分镜 ${ep.shot_count ?? '-'} 镜` : '未分镜'} · 帧 {ep.keyframes} · 片段 {ep.clips} ·{' '}
                          {ep.final_ready ? '成片✓' : ep.partial_render ? `残缺成片 段 ${ep.segments_rendered ?? 0}/${ep.segments_total ?? '-'}` : ep.final_video_exists ? '成片（未校验）' : '无成片'}
                        </span>
                        <em>{status.label}</em>
                      </div>
                      <button
                        type="button"
                        className="budget-disk-cleanup"
                        onClick={() => void cleanup(ep.ep_id)}
                        disabled={Boolean(cleaning) || !hasArtifacts}
                        title={hasArtifacts ? `清理 ${ep.ep_id} 的渲染产物` : '该集还没有渲染产物'}
                      >
                        <Trash2 size={13} />{cleaning === ep.ep_id ? '清理中…' : '清理渲染产物'}
                      </button>
                    </li>
                  );
                })}
                {episodes.length === 0 && <li className="budget-disk-episode-empty">尚未生成任何集的渲染产物</li>}
              </ul>
            </div>
          )}
          {otherGroups.length > 0 && (
            <div className="budget-disk-group">
              <header><span>其他目录</span><em>项目级资产与规划文件</em></header>
              <ul className="budget-disk-dir-list">
                {otherGroups
                  .filter(([, stats]) => stats.bytes > 0)
                  .sort((a, b) => b[1].bytes - a[1].bytes)
                  .map(([name, stats]) => (
                    <li key={name}>
                      <span>{name}</span>
                      <em>{formatBytes(stats.bytes)} · {stats.files} 个文件</em>
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function BudgetView({session, actions, onCreate}: {session: SessionSummary | undefined; actions?: ReactNode; onCreate?: () => void}) {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [kindFilter, setKindFilter] = useState<'all' | UsageEntry['kind']>('all');
  // P2-2：按集进度地图数据
  const [episodes, setEpisodes] = useState<EpisodeProgress[]>([]);
  const [episodesLoading, setEpisodesLoading] = useState(false);

  const loadEpisodes = useCallback(() => {
    if (!session) {
      setEpisodes([]);
      return;
    }
    setEpisodesLoading(true);
    fetch(`/api/episodes?session=${encodeURIComponent(session.sessionId)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data?.episodes) setEpisodes(data.episodes); })
      .catch(() => { /* 静默 */ })
      .finally(() => setEpisodesLoading(false));
  }, [session]);

  useEffect(() => {
    loadEpisodes();
  }, [loadEpisodes]);

  const load = useCallback(() => {
    if (!session) {
      setUsage(null);
      return;
    }
    setLoading(true);
    setError('');
    getUsage(session.sessionId)
      .then((payload) => setUsage(payload))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, [session]);

  useEffect(() => {
    load();
  }, [load]);

  const projectName = session?.projectName || '当前项目';
  const generatedAt = usage?.generated_at ? new Date(usage.generated_at).toLocaleTimeString('zh-CN', {hour12: false}) : '';
  const recentEntries = usage
    ? kindFilter === 'all'
      ? usage.recent
      : usage.recent_by_kind?.[kindFilter] ?? usage.recent.filter((entry) => entry.kind === kindFilter)
    : [];
  const recentTotal = usage ? (kindFilter === 'all' ? usage.entries : usage.breakdown.by_kind[kindFilter] || 0) : 0;
  const recentGroups = groupRecentEntries(recentEntries);
  const modelDetails: ModelCount[] = (usage?.breakdown.by_model_detail?.length
    ? [...usage.breakdown.by_model_detail]
    : Object.entries(usage?.breakdown.by_model ?? {}).map(([name, count]) => ({name, ref: '', count}))
  ).sort((left, right) => right.count - left.count);

  return (
    <section className="budget-view">
      <header className="budget-header">
        <div className="budget-title-group">
          <div className="budget-title-copy">
            <span>预算统计 · {projectName}</span>
            <h1><BarChart3 size={19} />用量与费用</h1>
            {generatedAt && <em>更新于 {generatedAt} · 估算单价：图像 ¥0.30/张 · 视频按秒计 480P ¥0.23/s · 720P ¥0.50/s · 1080P ¥1.00/s · 音色创建 ¥0.00</em>}
          </div>
        </div>
        <div className="budget-actions">
          <button className="icon-button" onClick={() => void load()} disabled={loading} aria-label="刷新统计" title="刷新统计">
            <RefreshCw size={17} className={loading ? 'is-spinning' : ''} />
          </button>
          {actions}
        </div>
      </header>

      {error ? (
        <div className="budget-empty is-error">{error}</div>
      ) : !session ? (
        <div className="budget-empty">未选择项目，请先在左侧选择一个项目查看预算统计</div>
      ) : (
        <EpisodeProgressMap episodes={episodes} onRefresh={loadEpisodes} loading={episodesLoading} />
      )}

      {error ? (
        <div className="budget-empty is-error">{error}</div>
      ) : !session ? (
        <div className="budget-empty">未选择项目，请先在左侧选择一个项目查看预算统计</div>
      ) : loading && !usage ? (
        <div className="budget-empty">正在统计…</div>
      ) : !usage || usage.entries === 0 ? (
        <div className="budget-empty">
          <strong>暂无用量记录</strong>
          <span>从下一次图像、视频或音色生成开始，这里会自动汇总费用。</span>
          {onCreate && <button type="button" onClick={onCreate}>回到工作区创建任务</button>}
        </div>
      ) : (
        <>
          <div className="budget-cards">
            <StatCard icon={<ImageIcon size={18} />} label="图像生成" value={`${usage.image.count} 张`} hint={`${Object.keys(usage.image.models).length} 个模型`} />
            <StatCard icon={<Film size={18} />} label="视频片段" value={`${usage.video.count} 条`} hint={`约 ${Math.round(usage.video.duration_sec)} 秒`} />
            <StatCard icon={<Mic size={18} />} label="角色音色" value={`${usage.audio.count} 个`} hint={usage.audio.billable_count > 0 ? `${usage.audio.billable_count} 个回退合成 · ${usage.audio.tts_chars} 字符` : '已计入统计 · 建音色 ¥0.00'} />
            <StatCard icon={<Wallet size={18} />} label="费用估算" value={`¥${totalCost(usage).toFixed(2)}`} hint={`图像 ¥${imageCost(usage).toFixed(2)} · 视频 ¥${videoCost(usage).toFixed(2)} · 音色 ¥${audioCost(usage).toFixed(2)}`} tone="total" />
          </div>

          <div className="budget-scope-note">
            <strong>统计范围</strong>
            <span>图像、视频、角色音色。音色创建为 ¥0.00；回退到语音合成时按 ¥1.50/万字符估算并计入。当前不含剧本、分镜等 LLM token、embedding 和 rerank 调用。</span>
          </div>

          <div className="budget-charts">
            <div className="budget-chart-card">
              <header><h2>用途分布</h2><p>按生成用途统计的调用量</p></header>
              <DistributionBars items={Object.entries(usage.breakdown.by_purpose).sort((a, b) => b[1] - a[1])} color="var(--blue)" />
            </div>
            <div className="budget-chart-card">
              <header><h2>视频分辨率</h2><p>各分辨率下的片段条数</p></header>
              <DistributionBars items={Object.entries(usage.video.by_resolution).sort((a, b) => b[1] - a[1])} color="var(--violet, #7c3aed)" />
            </div>
            <div className="budget-chart-card">
              <header><h2>模型分布</h2><p>各模型被调用的次数</p></header>
              <ModelDistributionBars items={modelDetails} color="var(--green, #059669)" />
            </div>
          </div>

          {usage.breakdown.daily.length > 0 && (
            <div className="budget-chart-card budget-daily-card">
              <header><h2>每日趋势</h2><p>近 {usage.breakdown.daily.length} 天的调用量（图/视频/音色堆叠）</p></header>
              <DailyChart days={usage.breakdown.daily} />
              <div className="budget-daily-legend">
                <span><i className="is-image" />图像</span>
                <span><i className="is-video" />视频</span>
                <span><i className="is-audio" />音色</span>
              </div>
            </div>
          )}

          <div className="budget-chart-card budget-table-card">
            <header className="budget-table-header">
              <div><h2>最近记录</h2><p>显示 {recentEntries.length} / {recentTotal} 条调用明细，按日期分组、时间倒序</p></div>
              <label className="budget-kind-filter">
                <span>类型</span>
                <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value as 'all' | UsageEntry['kind'])} aria-label="筛选费用类型">
                  <option value="all">全部</option>
                  <option value="image">图像</option>
                  <option value="video">视频</option>
                  <option value="audio">音色</option>
                </select>
              </label>
            </header>
            <div className="budget-record-columns" aria-hidden="true">
              <span />
              <span className="is-copy">用途 / 类型</span>
              <span className="is-model">模型</span>
              <span className="is-facts">时长 / 分辨率</span>
              <span className="is-time">时间</span>
              <span className="is-cost">费用</span>
            </div>
            <div className="budget-record-list">
              {recentGroups.map((group) => (
                <section className="budget-record-group" key={group.key}>
                  <header className="budget-record-group-header">
                    <span>{group.label}</span>
                    <em>{group.entries.length} 条</em>
                    <strong>合计 ¥{group.cost.toFixed(2)}</strong>
                  </header>
                  <ul className="budget-record-rows">
                    {group.entries.map((entry, index) => {
                      const resolution = entry.resolution && entry.resolution !== 'unknown' ? entry.resolution : '';
                      return (
                        <li className={`budget-record is-${entry.kind}`} key={`${entry.ts}-${index}`}>
                          <span className="budget-record-type-icon" aria-hidden="true"><UsageKindIcon kind={entry.kind} /></span>
                          <div className="budget-record-copy">
                            <div className="budget-record-title">
                              <strong>{PURPOSE_LABELS[entry.purpose] ?? entry.purpose}</strong>
                              <span className={`budget-kind is-${entry.kind}`}>{KIND_LABELS[entry.kind] ?? entry.kind}</span>
                            </div>
                          </div>
                          <div className="budget-record-model" title={[entry.model_name || entry.model, entry.model_ref].filter(Boolean).join('\n')}>
                            <strong>{entry.model_name || entry.model || '未知模型'}</strong>
                            {entry.model_ref && <small>{entry.model_ref}</small>}
                          </div>
                          <div className="budget-record-facts">
                            {entry.kind === 'video' && <span><Clock3 size={13} />{entry.duration} 秒</span>}
                            {resolution && <span><Monitor size={13} />{resolution}</span>}
                            {entry.quantity > 1 && <span>×{entry.quantity}</span>}
                          </div>
                          <time className="budget-record-time" dateTime={new Date(entry.ts * 1000).toISOString()}>{formatClock(entry.ts)}</time>
                          <div className="budget-record-cost-cell">
                            <strong className="budget-record-cost">¥{entryCost(entry).toFixed(2)}</strong>
                            {entry.kind === 'video' && (
                              <small className="budget-record-rate">{entry.duration}s × ¥{videoRateOf(entry.resolution).toFixed(2)}/s</small>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          </div>

          {session && <DiskUsageSection session={session} onChanged={load} />}
        </>
      )}
    </section>
  );
}
