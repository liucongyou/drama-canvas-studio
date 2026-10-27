import {useEffect, useMemo, useState, type CSSProperties} from 'react';
import {Clapperboard, Download, Film, Sparkles} from 'lucide-react';
import {mediaUrl} from './ArtifactViews';
import type {Artifact} from './types';

/**
 * 「我的成片」交付中心——"只要结果"的兑现时刻。
 *
 * 把散落在"产物"视图里的成片前置为独立视图：最新一集大图内嵌播放 + 一键下载，
 * 全部集成片网格陈列，制作进度一眼可见。让用户点进来第一眼看到的就是自己的电影。
 */

function fmtEp(raw: string): string {
  // ep01 / ep1 / 01 / 1 → 第 N 集
  const m = raw.match(/(\d+)/);
  if (m) return `第 ${Number(m[1])} 集`;
  return raw;
}

function parseEpisode(path: string): {key: string; label: string} {
  const norm = path.replace(/\\/g, '/');
  const m = norm.match(/episodes\/([^/]+)\/final_video\.mp4$/i);
  if (m) return {key: m[1], label: fmtEp(m[1])};
  return {key: 'main', label: '正片'};
}

function posterUrl(artifact: Artifact): string {
  if (!artifact.url) return '';
  return artifact.url.replace('/api/artifact?', '/api/video-poster?');
}

/** 横竖屏自适应播放器：加载元数据后按真实宽高比设容器尺寸 */
function SmartVideo({src, poster, className}: {src: string; poster?: string; className?: string}) {
  const [ratio, setRatio] = useState<number | null>(null);
  const style: CSSProperties = {};
  if (ratio) {
    style.aspectRatio = String(ratio);
    // 竖屏视频限宽，横屏铺满
    if (ratio < 1) style.maxWidth = '320px';
    else style.maxWidth = '100%';
  }
  return (
    <div
      className={`results-video-wrap${className ? ' ' + className : ''}`}
      style={style}
    >
      <video
        src={src}
        poster={poster || undefined}
        controls
        playsInline
        preload="metadata"
        onLoadedMetadata={(e) => {
          const v = e.currentTarget;
          if (v.videoWidth > 0 && v.videoHeight > 0) setRatio(v.videoWidth / v.videoHeight);
        }}
      />
    </div>
  );
}

function fmtSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

export default function ResultsView({
  artifacts,
  session,
  onCreate,
}: {
  artifacts: Artifact[];
  session: string;
  onCreate: () => void;
}) {
  // 成片 = final_video.mp4；按集去重（保留每集最新），最新一集置顶展示
  const episodes = useMemo(() => {
    const byKey = new Map<string, Artifact>();
    for (const a of artifacts) {
      if (!/final_video\.mp4$/i.test(a.path.replace(/\\/g, '/'))) continue;
      const {key} = parseEpisode(a.path);
      const prev = byKey.get(key);
      if (!prev || new Date(a.updatedAt) > new Date(prev.updatedAt)) byKey.set(key, a);
    }
    return [...byKey.entries()]
      .map(([key, a]) => ({key, label: parseEpisode(a.path).label, artifact: a}))
      .sort((x, y) => new Date(y.artifact.updatedAt).getTime() - new Date(x.artifact.updatedAt).getTime());
  }, [artifacts]);

  // 残缺成片标记：final_video.mp4 在盘但缺段/缺镜（实测段级直出 ep01 只落了 7/14 段）。
  // "我的成片"是用户真正点开播放的地方——播到一半突然结束却什么都不说，
  // 正是"视频没完就结束了"的来源，所以这里按 /api/episodes 的进度显式标注缺多少。
  const [renderStates, setRenderStates] = useState<Record<string, {rendered: number; total: number; gap: string}>>({});
  useEffect(() => {
    if (!session) {
      setRenderStates({});
      return;
    }
    let cancelled = false;
    fetch(`/api/episodes?session=${encodeURIComponent(session)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (cancelled || !data?.episodes) return;
        const next: Record<string, {rendered: number; total: number; gap: string}> = {};
        for (const ep of data.episodes as Array<Record<string, unknown>>) {
          if (!ep?.partial_render) continue;
          next[String(ep.ep_id)] = {
            rendered: Number(ep.segments_rendered ?? 0),
            total: Number(ep.segments_total ?? 0),
            gap: String(ep.render_gap ?? ''),
          };
        }
        setRenderStates(next);
      })
      .catch(() => { /* 静默：进度拿不到也不能挡住看成片 */ });
    return () => { cancelled = true; };
  }, [session]);

  if (!session) {
    return (
      <div className="results-view">
        <div className="results-empty">
          <Film size={40} />
          <h2>我的成片</h2>
          <p>先创建或选择一个项目，这里会展示你的专业短剧成片。</p>
          <button type="button" className="results-cta" onClick={onCreate}><Sparkles size={16} />去创作</button>
        </div>
      </div>
    );
  }

  if (episodes.length === 0) {
    return (
      <div className="results-view">
        <div className="results-empty">
          <Clapperboard size={40} />
          <h2>还没有成片</h2>
          <p>用「一键创作」给一句创意，智能体会按电影级标准自动完成剧本、分镜、渲染，成片会出现在这里。</p>
          <button type="button" className="results-cta" onClick={onCreate}><Sparkles size={16} />一键创作</button>
        </div>
      </div>
    );
  }

  const [hero, ...rest] = episodes;
  const totalSize = episodes.reduce((sum, e) => sum + (e.artifact.size || 0), 0);
  const partialCount = episodes.reduce((count, ep) => (renderStates[ep.key] ? count + 1 : count), 0);
  const heroPartial = renderStates[hero.key];

  return (
    <div className="results-view">
      <header className="results-head">
        <div className="results-head-title">
          <Film size={20} />
          <div>
            <strong>我的成片</strong>
            <small>专业短剧 · 电影级标准</small>
          </div>
        </div>
        <div className="results-summary">
          <span className="results-stat"><b>{episodes.length}</b> 集成片</span>
          {partialCount > 0 && <span className="results-stat results-stat-partial">⚠ {partialCount} 集残缺</span>}
          {totalSize > 0 && <span className="results-stat">{fmtSize(totalSize)}</span>}
          <span className="results-stat results-stat-time">更新于 {new Date(hero.artifact.updatedAt).toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'})}</span>
        </div>
      </header>

      <section className="results-hero">
        <div className="results-hero-player">
          <SmartVideo src={mediaUrl(hero.artifact)} poster={posterUrl(hero.artifact)} />
        </div>
        <div className="results-hero-meta">
          <span className="results-hero-label">{hero.label}</span>
          <h3>最新成片</h3>
          <p>{hero.artifact.name}</p>
          {heroPartial && (
            <p className="results-partial-note" title={heroPartial.gap}>
              ⚠ 残缺成片：只渲染了 {heroPartial.rendered}/{heroPartial.total} 段，播放会提前结束（续跑可补齐）
            </p>
          )}
          <div className="results-hero-actions">
            <a className="results-download" href={mediaUrl(hero.artifact)} download={`${hero.label}-成片.mp4`}>
              <Download size={16} />下载成片
            </a>
          </div>
        </div>
      </section>

      {rest.length > 0 && (
        <section className="results-grid-section">
          <h4 className="results-grid-title">全部成片（{rest.length + 1}）</h4>
          <div className="results-grid">
            {[hero, ...rest].map((ep) => {
              const partial = renderStates[ep.key];
              return (
                <div key={ep.key} className={`results-card${partial ? ' is-partial' : ''}`}>
                  <SmartVideo src={mediaUrl(ep.artifact)} poster={posterUrl(ep.artifact)} />
                  <div className="results-card-meta">
                    <span className="results-card-label">{ep.label}</span>
                    {partial && (
                      <span className="results-partial-badge" title={partial.gap}>
                        残缺 {partial.rendered}/{partial.total} 段
                      </span>
                    )}
                    <a className="results-card-download" href={mediaUrl(ep.artifact)} download={`${ep.label}-成片.mp4`} title="下载">
                      <Download size={15} />
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
