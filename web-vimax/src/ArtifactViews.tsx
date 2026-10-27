import {useCallback, useEffect, useMemo, useRef, useState, type ReactNode} from 'react';
import {dialog} from './GDialog';
import {ChevronDown, ChevronLeft, ChevronRight, ChevronUp, ChevronsDown, ChevronsUp, Copy, Download, FileJson, Files, Film, Image as ImageIcon, Maximize2, Mountain, Music, Package, Pause, Play, RefreshCw, RotateCcw, Search, User, Video, X} from 'lucide-react';
import {getJsonArtifact, regenerateCharacterPortraits} from './api';
import {notify} from './toast';
import {
  activeRenderCheckpoint,
  classifyMediaArtifact,
  deriveStoryboardReadiness,
  DOCUMENT_GROUPS,
  documentGroupKey,
  extractStoryboardPreviews,
  formatStructuredValue,
  friendlyArtifactTitle,
  friendlyFieldLabel,
  isJsonArtifact,
  isJsonObject,
  isJsonPrimitive,
  isArtifactPathField,
  isStoryboardArtifact,
  relatedVisualArtifacts,
  structuredRecordTitle,
  resolveMediaOrientation,
  type ReadinessStatus,
  type StoryboardPreview,
} from './artifactPresentation';
import type {Artifact, JsonValue, SessionSummary} from './types';
type ArtifactTab = 'all' | 'final' | 'clips' | 'frames' | 'segments' | 'documents' | 'assets';

function StatChip({count, label}: {count: number; label: string}) {
  return (
    <span className="artifact-stat-chip" title={`${label} ${count}`}>
      <b>{count}</b><i>{label}</i>
    </span>
  );
}

const ARTIFACT_TABS: Array<{key: ArtifactTab; label: string}> = [
  {key: 'all', label: '生成文件'},
  {key: 'final', label: '成片'},
  {key: 'clips', label: '视频片段'},
  {key: 'frames', label: '关键帧'},
  {key: 'segments', label: '段'},
  {key: 'documents', label: '文档'},
  {key: 'assets', label: '资产'},
];

type ArtifactSort = 'updated' | 'name' | 'size';
type PreviewState = {artifact: Artifact; items: Artifact[]};
type MediaGroups = Record<'final_video' | 'video_clip' | 'key_frame' | 'character' | 'scene' | 'prop' | 'voice', Artifact[]>;

const videoThumbnailCache = new Map<string, string>();
const THUMBNAIL_DB = 'yingjie-media-cache';
const THUMBNAIL_STORE = 'video-thumbnails';

function openThumbnailDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(THUMBNAIL_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(THUMBNAIL_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function readCachedThumbnail(key: string): Promise<string> {
  const db = await openThumbnailDb();
  return new Promise((resolve, reject) => {
    const request = db.transaction(THUMBNAIL_STORE, 'readonly').objectStore(THUMBNAIL_STORE).get(key);
    request.onsuccess = () => resolve(typeof request.result === 'string' ? request.result : '');
    request.onerror = () => reject(request.error);
  });
}

async function writeCachedThumbnail(key: string, value: string): Promise<void> {
  const db = await openThumbnailDb();
  await new Promise<void>((resolve, reject) => {
    const request = db.transaction(THUMBNAIL_STORE, 'readwrite').objectStore(THUMBNAIL_STORE).put(value, key);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

function artifactSearchText(artifact: Artifact): string {
  return [artifact.name, normalizeArtifactPath(artifact.path), visualArtifactLabel(artifact), artifact.kind].join(' ').toLowerCase();
}

/** 项目级通用资产（跨集复用，按集筛选时始终保留展示） */
function isProjectLevelAsset(artifact: Artifact): boolean {
  return artifact.path.startsWith('script2video/character_portraits/')
    || artifact.path.startsWith('script2video/scenes/')
    || artifact.path.startsWith('script2video/props/')
    || artifact.path.startsWith('script2video/voices/');
}

type EpisodeMeta = {
  ep_id: string;
  title: string;
  assets_ready?: boolean | null;
  assets_missing_count?: number | null;
  storyboard_ready: boolean;
  shot_count: number | null;
  keyframes: number;
  clips: number;
  final_ready: boolean;
  status: string;
};

const EPISODE_STATUS_LABEL: Record<string, string> = {
  rendered: '完成',
  rendering: '渲染中',
  partial: '样片',
  error: '失败',
  pending: '待处理',
  interrupted: '已中断',
  assets_pending: '资产待生成',
  assets_generating: '资产生成中',
  assets_ready: '资产已就绪',
  assets_confirm_pending: '待确认资产',
  assets_confirmed: '资产已确认',
  assets_error: '资产失败',
  assets_timeout: '资产超时',
  assets_cancelled: '资产已取消',
};

function assetMissingSuffix(ep: EpisodeMeta): string {
  return ep.assets_ready === false ? ` · 缺资产${ep.assets_missing_count ? ` ${ep.assets_missing_count} 项` : ''}` : '';
}

function EpisodeProgressPanel({episodes, activeEp, onSelect}: {episodes: EpisodeMeta[]; activeEp: string; onSelect: (ep: string) => void}) {
  const [open, setOpen] = useState(false);
  if (episodes.length === 0) return null;
  const doneCount = episodes.filter((ep) => ep.final_ready).length;
  const assetPending = episodes.filter((ep) => ep.assets_ready === false && !ep.final_ready).length;
  return (
    <div className="episode-progress-panel">
      <button type="button" className="episode-progress-toggle" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span>集进度</span>
        <em>{doneCount}/{episodes.length} 集成片</em>
        {assetPending > 0 && <em className="episode-progress-toggle-assets">{assetPending} 集缺资产</em>}
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && (
        <div className="episode-progress-grid">
          {episodes.map((ep) => (
            <button
              key={ep.ep_id}
              type="button"
              className={`episode-progress-card ${activeEp === ep.ep_id ? 'is-active' : ''} is-${ep.status}`}
              onClick={() => onSelect(activeEp === ep.ep_id ? '' : ep.ep_id)}
              title={`${ep.title ? `${ep.ep_id} · ${ep.title}` : ep.ep_id}${assetMissingSuffix(ep)}`}
            >
              <strong>{ep.ep_id}</strong>
              <span className="episode-progress-dots" aria-hidden="true">
                <i className={ep.assets_ready ? 'is-done' : ''} title="资产就绪" />
                <i className={ep.storyboard_ready ? 'is-done' : ''} title="分镜已生成" />
                <i className={ep.keyframes > 0 ? 'is-done' : ''} title={`关键帧 ${ep.keyframes}`} />
                <i className={ep.clips > 0 ? 'is-done' : ''} title={`片段 ${ep.clips}`} />
                <i className={ep.final_ready ? 'is-done' : ''} title="成片" />
              </span>
              <em>{EPISODE_STATUS_LABEL[ep.status] ?? ep.status}</em>
              {ep.assets_ready === false && !ep.final_ready && (
                <b className="episode-progress-assets">缺资产{ep.assets_missing_count ? ` ${ep.assets_missing_count} 项` : ''}</b>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function sortArtifacts(artifacts: Artifact[], sort: ArtifactSort): Artifact[] {
  return [...artifacts].sort((left, right) => {
    if (sort === 'name') return left.name.localeCompare(right.name, undefined, {numeric: true});
    if (sort === 'size') return right.size - left.size;
    return right.updatedAt.localeCompare(left.updatedAt) || left.name.localeCompare(right.name, undefined, {numeric: true});
  });
}

function buildMediaGroups(artifacts: Artifact[]): MediaGroups {
  const groups: MediaGroups = {final_video: [], video_clip: [], key_frame: [], character: [], scene: [], prop: [], voice: []};
  artifacts.forEach((artifact) => {
    if (artifact.kind !== 'image' && artifact.kind !== 'video' && artifact.kind !== 'audio') return;
    const mediaClass = classifyMediaArtifact(artifact);
    if (mediaClass === 'other' || mediaClass === 'final_video') {
      if (mediaClass !== 'other') groups.final_video.push(artifact);
      return;
    }
    groups[mediaClass].push(artifact);
  });
  return groups;
}

export function ArtifactsView({session, artifacts, artifactsLoading = false, artifactsTruncated = false, artifactsTotal = 0, artifactsLoadingMore = false, onLoadMoreArtifacts, actions, onRefresh}: {session?: SessionSummary; artifacts: Artifact[]; artifactsLoading?: boolean; artifactsTruncated?: boolean; artifactsTotal?: number; artifactsLoadingMore?: boolean; onLoadMoreArtifacts?: () => void; actions?: ReactNode; onRefresh?: () => void}) {
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<ArtifactSort>('updated');
  const [episodeFilter, setEpisodeFilter] = useState('');
  const [episodesMeta, setEpisodesMeta] = useState<EpisodeMeta[]>([]);
  const normalizedQuery = query.trim().toLowerCase();
  const hasQuery = normalizedQuery.length > 0;
  const episodeOptions = useMemo(() => {
    const set = new Set<string>();
    artifacts.forEach((artifact) => {
      const match = /script2video\/episodes\/(ep[^/]+)\//.exec(artifact.path);
      if (match) set.add(match[1]);
    });
    return [...set].sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
  }, [artifacts]);
  useEffect(() => {
    let cancelled = false;
    if (!session) {
      setEpisodesMeta([]);
      return () => { cancelled = true; };
    }
    setEpisodesMeta([]);
    fetch(`/api/episodes?session=${encodeURIComponent(session.sessionId)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled && data?.episodes) setEpisodesMeta(data.episodes);
      })
      .catch(() => { /* 集进度接口不可用时静默降级，不影响产物浏览 */ });
    return () => { cancelled = true; };
  }, [session?.sessionId]);
  useEffect(() => {
    if (episodeFilter && !episodeOptions.includes(episodeFilter)) setEpisodeFilter('');
  }, [episodeOptions, episodeFilter]);
  const filteredArtifacts = useMemo(() => {
    const base = hasQuery ? artifacts.filter((artifact) => artifactSearchText(artifact).includes(normalizedQuery)) : artifacts;
    const scoped = !episodeFilter
      ? base
      : base.filter((artifact) => artifact.path.startsWith(`script2video/episodes/${episodeFilter}/`) || isProjectLevelAsset(artifact));
    return sortArtifacts(scoped, sort);
  }, [artifacts, hasQuery, normalizedQuery, episodeFilter, sort]);
  const allJsonArtifacts = useMemo(() => artifacts.filter(isJsonArtifact), [artifacts]);
  const jsonArtifacts = useMemo(() => filteredArtifacts.filter(isJsonArtifact), [filteredArtifacts]);
  const [selectedPath, setSelectedPath] = useState('');
  const selected = jsonArtifacts.find((artifact) => artifact.path === selectedPath) || jsonArtifacts[0];
  const [document, setDocument] = useState<JsonValue>();
  const [documentQuery, setDocumentQuery] = useState('');
  const [forceExpanded, setForceExpanded] = useState<boolean | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [view, setView] = useState<ArtifactTab>('all');
  const [preview, setPreview] = useState<PreviewState>();
  const mediaGroups = useMemo(() => buildMediaGroups(artifacts), [artifacts]);
  const visibleMediaGroups = useMemo(() => buildMediaGroups(filteredArtifacts), [filteredArtifacts]);
  const openPreview = useCallback((artifact: Artifact, items: Artifact[]) => {
    setPreview({artifact, items: items.filter((item) => item.kind === 'image' || item.kind === 'video' || item.kind === 'audio')});
  }, []);
  const relatedMedia = useMemo(
    () => selected ? relatedVisualArtifacts(selected, artifacts) : [],
    [selected, artifacts],
  );
  const groupedDocuments = useMemo(() => {
    const groups = new Map<string, Artifact[]>();
    jsonArtifacts.forEach((artifact) => {
      const key = documentGroupKey(artifact);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(artifact);
    });
    return DOCUMENT_GROUPS
      .filter((group) => (groups.get(group.key) || []).length > 0)
      .map((group) => ({key: group.key, label: group.label, items: groups.get(group.key)!}));
  }, [jsonArtifacts]);

  useEffect(() => {
    setSelectedPath((current) => jsonArtifacts.some((artifact) => artifact.path === current) ? current : jsonArtifacts[0]?.path || '');
  }, [jsonArtifacts]);

  useEffect(() => {
    let cancelled = false;
    setDocument(undefined);
    setDocumentQuery('');
    setForceExpanded(undefined);
    setError('');
    if (!selected) return () => { cancelled = true; };
    setLoading(true);
    void getJsonArtifact(selected)
      .then((payload) => !cancelled && setDocument(payload))
      .catch((reason) => !cancelled && setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [selected?.path, selected?.updatedAt]);

  async function copyDocument() {
    if (document === undefined || !selected) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(document, null, 2));
      notify('JSON 已复制');
    } catch {
      notify('复制失败，请检查浏览器剪贴板权限', 'error');
    }
  }

  function downloadDocument() {
    if (document === undefined || !selected) return;
    const blob = new Blob([JSON.stringify(document, null, 2)], {type: 'application/json;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const anchor = window.document.createElement('a');
    anchor.href = url;
    anchor.download = selected.name || 'document.json';
    anchor.click();
    URL.revokeObjectURL(url);
    notify('文档已下载');
  }

  return (
    <section className="artifacts-view">
      <div className="artifact-topbar">
        <div className="artifact-topbar-main">
        <div className="artifact-view-switcher-shell">
          <div className="artifact-view-switcher" role="tablist" aria-label="产物类型">
            {ARTIFACT_TABS.map((option) => (
              <button
                key={option.key}
                role="tab"
                aria-selected={view === option.key}
                className={view === option.key ? 'is-selected' : ''}
                onClick={() => setView(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div className="artifact-file-count" aria-label="产物统计">
          <StatChip count={mediaGroups.final_video.length} label="成片" />
          <StatChip count={mediaGroups.video_clip.length} label="片段" />
          <StatChip count={mediaGroups.key_frame.length} label="关键帧" />
          <StatChip count={allJsonArtifacts.length} label="文档" />
          <StatChip count={mediaGroups.character.length} label="角色" />
          <StatChip count={mediaGroups.scene.length} label="场景" />
          <StatChip count={mediaGroups.prop.length} label="道具" />
          <StatChip count={mediaGroups.voice.length} label="音色" />
        </div>
        </div>
        {actions && <div className="artifact-topbar-actions">{actions}</div>}
      </div>
      <EpisodeProgressPanel episodes={episodesMeta} activeEp={episodeFilter} onSelect={setEpisodeFilter} />
      <div className="artifact-filterbar">
        {episodeOptions.length > 0 && (
          <label className="artifact-episode-filter">
            <span>按集</span>
            <select value={episodeFilter} onChange={(event) => setEpisodeFilter(event.target.value)} aria-label="按集筛选产物">
              <option value="">全部集（含项目级资产）</option>
              {episodeOptions.map((ep) => <option key={ep} value={ep}>{ep}</option>)}
            </select>
          </label>
        )}
        <div className="artifact-search">
          <Search size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索名称、路径或类型"
            aria-label="搜索产物"
          />
          {query && <button type="button" onClick={() => setQuery('')} aria-label="清空搜索"><X size={13} /></button>}
        </div>
        <span className="artifact-filter-count">
          {normalizedQuery
            ? `命中 ${filteredArtifacts.length} 个`
            : artifactsTotal > artifacts.length
              ? `已加载 ${artifacts.length} / 共 ${artifactsTotal} 个产物`
              : `共 ${artifacts.length} 个产物`}
        </span>
        <label className="artifact-sort">
          <span>排序</span>
          <select value={sort} onChange={(event) => setSort(event.target.value as ArtifactSort)} aria-label="产物排序">
            <option value="updated">最近更新</option>
            <option value="name">名称顺序</option>
            <option value="size">体积从大到小</option>
          </select>
        </label>
      </div>
      <div className={`artifacts-body ${view === 'documents' ? 'is-documents' : ''}`}>
        {!session ? (
          <ArtifactsEmpty title="选择项目" detail="规划后项目文件会显示在这里" />
        ) : artifactsLoading && artifacts.length === 0 ? (
          <ArtifactsLoading />
        ) : artifacts.length === 0 ? (
          <ArtifactsEmpty title="暂无产物" detail="这个项目还没有生成图像、视频或文档。可以回到工作区开始创建。" />
        ) : normalizedQuery && filteredArtifacts.length === 0 ? (
          <ArtifactsEmpty title="没有匹配的产物" detail="换一个关键词，或清空搜索后查看全部产物。" />
        ) : (<> 
          {artifactsTruncated && (
            <div className="artifacts-truncated-notice" role="status">
              产物数量已超过列表上限，部分较旧产物未显示（可先按集筛选浏览，或清理已完成的集产物）。
            </div>
          )}
          {(view === 'final' || (view === 'all' && visibleMediaGroups.final_video.length > 0)) && (
            <MediaSection
              title="成片"
              detail="拼接完成的最终视频（真正的成品）"
              artifacts={visibleMediaGroups.final_video}
              onPreview={openPreview}
              emptyTitle="暂无成片"
              emptyDetail="所有镜头片段拼接完成后，成片会显示在这里"
            />
          )}
          {(view === 'clips' || (view === 'all' && visibleMediaGroups.video_clip.length > 0)) && (
            <MediaSection
              title="视频片段"
              detail="单个镜头/机位/转场的独立片段（未拼接，不是成片）"
              artifacts={visibleMediaGroups.video_clip}
              onPreview={openPreview}
              emptyTitle="暂无视频片段"
              emptyDetail="渲染过程中每个镜头的片段会显示在这里"
            />
          )}
          {(view === 'frames' || (view === 'all' && visibleMediaGroups.key_frame.length > 0)) && (
            <KeyFrameSection artifacts={visibleMediaGroups.key_frame} onPreview={openPreview} />
          )}
          {(view === 'assets' || (view === 'all' && [visibleMediaGroups.character, visibleMediaGroups.scene, visibleMediaGroups.prop, visibleMediaGroups.voice].some((group) => group.length > 0))) && <AssetSection characters={visibleMediaGroups.character} scenes={visibleMediaGroups.scene} props={visibleMediaGroups.prop} voices={visibleMediaGroups.voice} onPreview={openPreview} sessionId={session?.sessionId} onRefresh={onRefresh} />}
          {view === 'segments' && <SegmentSection artifacts={filteredArtifacts} />}
          {(view === 'documents' || (view === 'all' && jsonArtifacts.length > 0)) && (
            <section className="artifact-content-section">
              <SectionHeading title="结构化文档" detail="规划产物：剧本、分镜、镜头描述等 JSON 文件" />
              {jsonArtifacts.length === 0 ? (
                <ArtifactsEmpty title="暂无结构化文件" detail="规划后 JSON 产物会显示在这里" />
              ) : (
                <div className="artifact-browser">
                  <nav className="artifact-document-list" aria-label="生成的 JSON 文件">
                    <div className="artifact-document-list-title"><span>文档</span><span>{jsonArtifacts.length}</span></div>
                    {groupedDocuments.map((group) => (
                      <div key={group.key} className="artifact-document-group">
                        <div className="artifact-document-group-title"><span>{group.label}</span><small>{group.items.length}</small></div>
                        {group.items.map((artifact) => (
                          <button
                            key={artifact.path}
                            className={artifact.path === selected?.path ? 'is-selected' : ''}
                            onClick={() => setSelectedPath(artifact.path)}
                          >
                            <FileJson size={16} />
                            <span><strong>{friendlyArtifactTitle(artifact)}</strong><small>{formatBytes(artifact.size)} · {formatArtifactAge(artifact.updatedAt)}</small></span>
                          </button>
                        ))}
                      </div>
                    ))}
                  </nav>
                  <article className={`artifact-document ${view === 'all' ? 'has-related-visuals' : ''}`}>
                    {selected && (
                      <>
                      <header>
                        <div><span>结构化视图</span><h2>{friendlyArtifactTitle(selected)}</h2></div>
                        <small className="artifact-document-meta">
                          <span>{formatBytes(selected.size)}</span>
                          <span>更新于 {formatArtifactAge(selected.updatedAt)}</span>
                          <span className="artifact-document-path" title={normalizeArtifactPath(selected.path)}>{normalizeArtifactPath(selected.path)}</span>
                        </small>
                      </header>
                      <div className="artifact-document-toolbar">
                        <div className="document-search">
                          <Search size={14} />
                          <input value={documentQuery} onChange={(event) => setDocumentQuery(event.target.value)} placeholder="在当前文档中搜索" aria-label="搜索当前文档" />
                          {documentQuery && <button type="button" onClick={() => setDocumentQuery('')} aria-label="清空文档搜索"><X size={12} /></button>}
                        </div>
                        <div className="document-actions">
                          <button type="button" onClick={() => setForceExpanded(true)} title="展开全部字段"><ChevronsDown size={14} />展开</button>
                          <button type="button" onClick={() => setForceExpanded(false)} title="折叠全部字段"><ChevronsUp size={14} />折叠</button>
                          <button type="button" onClick={() => void copyDocument()} disabled={document === undefined}><Copy size={14} />复制 JSON</button>
                          <button type="button" onClick={downloadDocument} disabled={document === undefined}><Download size={14} />下载</button>
                        </div>
                      </div>
                      </>
                    )}
                    <div className="artifact-document-layout">
                      <div className="artifact-structured-content">
                        {loading ? (
                          <DocumentSkeleton />
                        ) : error ? (
                          <div className="artifact-document-state is-error">{error}</div>
                        ) : document !== undefined && selected ? (
                          <StructuredDocument value={document} artifact={selected} query={documentQuery} forceExpanded={forceExpanded} />
                        ) : null}
                      </div>
                      {view === 'all' && (
                        <RelatedVisuals artifacts={relatedMedia} onPreview={openPreview} />
                      )}
                    </div>
                  </article>
                </div>
              )}
            </section>
          )}
          {!normalizedQuery && artifactsTotal > artifacts.length && onLoadMoreArtifacts && (
            <div className="artifacts-loadmore" role="status">
              <button type="button" onClick={onLoadMoreArtifacts} disabled={artifactsLoadingMore} aria-label="加载更多产物">
                {artifactsLoadingMore ? '加载中…' : `加载更多产物（已显示 ${artifacts.length} / ${artifactsTotal}）`}
              </button>
            </div>
          )}
        </>)}
      </div>
      {preview && <MediaPreviewDialog artifact={preview.artifact} items={preview.items} onClose={() => setPreview(undefined)} onNavigate={(artifact) => setPreview((current) => current ? {...current, artifact} : current)} />}
    </section>
  );
}

type SegmentRecord = {
  segment_id?: number;
  title?: string;
  duration?: number;
  shot_idxs?: number[];
  asset_binding?: string;
  entry_state?: string;
  change_timeline?: string;
  exit_state?: string;
  inheritance_rules?: string;
  staging_and_axis?: string;
  group_bridge?: string;
  style_lighting?: string;
  forbidden?: string;
  end_state?: string;
};

const SEGMENT_FIELDS: Array<{key: keyof SegmentRecord; label: string; icon?: ReactNode}> = [
  {key: 'asset_binding', label: '参考资产绑定'},
  {key: 'entry_state', label: '入段状态'},
  {key: 'change_timeline', label: '变化时间窗'},
  {key: 'exit_state', label: '出段状态'},
  {key: 'inheritance_rules', label: '继承规则'},
  {key: 'staging_and_axis', label: '场面调度与摄影轴线'},
  {key: 'group_bridge', label: '背景群体桥接'},
  {key: 'style_lighting', label: '风格及光影'},
  {key: 'forbidden', label: '禁止项'},
  {key: 'end_state', label: '末帧站位交接'},
];

function SegmentSection({artifacts}: {artifacts: Artifact[]}) {
  const [segments, setSegments] = useState<SegmentRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const segmentArtifact = useMemo(
    () => artifacts.find((artifact) => artifact.name.toLowerCase() === 'segments.json'),
    [artifacts],
  );

  useEffect(() => {
    let cancelled = false;
    setSegments([]);
    setError('');
    if (!segmentArtifact) return () => { cancelled = true; };
    setLoading(true);
    void getJsonArtifact(segmentArtifact)
      .then((payload) => {
        if (cancelled) return;
        const raw = Array.isArray(payload) ? payload : payload && typeof payload === 'object' && Array.isArray((payload as {segments?: unknown}).segments)
          ? (payload as {segments: unknown[]}).segments
          : [];
        const list: SegmentRecord[] = raw
          .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
          .map((item) => {
            const record: SegmentRecord = {};
            (Object.keys(SEGMENT_FIELDS) as Array<keyof SegmentRecord>).forEach((key) => {
              const value = item[key];
              if (typeof value === 'string' || typeof value === 'number' || Array.isArray(value)) {
                record[key] = value as never;
              }
            });
            return record;
          });
        setSegments(list);
      })
      .catch((reason) => !cancelled && setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [segmentArtifact?.path, segmentArtifact?.updatedAt]);

  return (
    <section className="artifact-content-section">
      <SectionHeading title="段" detail="戏剧节拍单元：段内镜头独立生成、按时间轴拼接成段（4–15 秒容器），段级契约保证站位连续与资产绑定" count={segments.length} />
      {loading ? (
        <DocumentSkeleton />
      ) : error ? (
        <div className="artifact-document-state is-error">{error}</div>
      ) : !segmentArtifact ? (
        <ArtifactsEmpty title="暂无段级契约" detail="分镜生成后会输出 segments.json（导演级分段：连续性/资产绑定/时间轴），显示在这里。" />
      ) : segments.length === 0 ? (
        <ArtifactsEmpty title="段文件为空" detail="segments.json 已存在但内容为空，重新生成分镜后段级契约会写入。" />
      ) : (
        <div className="segment-grid">
          {segments.map((segment) => (
            <SegmentCard key={segment.segment_id ?? Math.random()} segment={segment} />
          ))}
        </div>
      )}
    </section>
  );
}

function SegmentCard({segment}: {segment: SegmentRecord}) {
  const [open, setOpen] = useState(false);
  const duration = typeof segment.duration === 'number' ? `${segment.duration.toFixed(1)}s` : '时长?';
  const shotCount = Array.isArray(segment.shot_idxs) ? segment.shot_idxs.length : 0;
  return (
    <article className={`segment-card ${open ? 'is-open' : ''}`}>
      <header className="segment-card-head" onClick={() => setOpen((current) => !current)} role="button" tabIndex={0} aria-expanded={open} onKeyDown={(event) => event.key === 'Enter' && setOpen((current) => !current)}>
        <span className="segment-card-idx">段{segment.segment_id ?? '?'}</span>
        <div className="segment-card-title">
          <strong>{segment.title || '未命名段'}</strong>
          <small><i>⏱ {duration}</i><i>🎬 {shotCount} 镜</i>{Array.isArray(segment.shot_idxs) && segment.shot_idxs.length > 0 && <i>镜头 {segment.shot_idxs.join('、')}</i>}</small>
        </div>
        <span className="segment-card-toggle">{open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}</span>
      </header>
      {open && (
        <div className="segment-card-body">
          {SEGMENT_FIELDS.map(({key, label}) => {
            const value = segment[key];
            if (!value) return null;
            return (
              <div key={key} className="segment-field">
                <span>{label}</span>
                <p>{String(value)}</p>
              </div>
            );
          })}
        </div>
      )}
    </article>
  );
}

type VideoMetadata = {width: number; height: number; duration: number};

const EAGER_MEDIA_COUNT = 12;

/** 视频卡片的生产信息：优先用浏览器实测的分辨率/时长，其次用服务端注入的生产信息。 */
function formatVideoProduction(artifact: Artifact, measured?: VideoMetadata): string {
  const parts: string[] = [];
  const duration = measured && measured.duration > 0.1 ? measured.duration : artifact.production?.duration;
  if (duration && duration > 0.1) {
    parts.push(`${duration.toFixed(duration >= 10 ? 0 : 1).replace(/\.0$/, '')}s`);
  }
  if (measured && measured.width > 0 && measured.height > 0) {
    parts.push(`${measured.width}×${measured.height}`);
  } else if (artifact.production?.resolution) {
    parts.push(artifact.production.resolution);
  }
  if (artifact.production?.modelName) parts.push(artifact.production.modelName);
  return parts.join(' · ');
}

function MediaCard({artifact, items, onPreview, eager}: {artifact: Artifact; items: Artifact[]; onPreview: (artifact: Artifact, items: Artifact[]) => void; eager: boolean}) {
  const [measured, setMeasured] = useState<VideoMetadata>();
  const label = visualArtifactLabel(artifact);
  const meta = artifact.kind === 'video' ? formatVideoProduction(artifact, measured) : '';
  // 横屏/竖屏用不同比例的播放窗口：竖屏卡片不再被塞进 16:9 框里出现大黑边
  const orientation = measured ? resolveMediaOrientation(measured.width, measured.height) : 'portrait';
  return (
    <article className={`render-item is-${artifact.kind}`}>
      <button
        className={`render-media is-${orientation}`}
        style={measured && measured.width > 0 && measured.height > 0 ? {aspectRatio: `${measured.width} / ${measured.height}`} : undefined}
        onClick={() => onPreview(artifact, items)}
        aria-label={`预览 ${label}`}
      >
        {artifact.kind === 'image'
          ? <img
              src={mediaUrl(artifact)}
              alt={label}
              loading={eager ? 'eager' : 'lazy'}
              decoding="async"
              onLoad={(event) => {
                const element = event.currentTarget;
                if (element.naturalWidth && element.naturalHeight) {
                  setMeasured((current) => current ?? {width: element.naturalWidth, height: element.naturalHeight, duration: 0});
                }
              }}
            />
          : <VideoThumbnail artifact={artifact} onMetadata={setMeasured} />}
        {artifact.kind === 'video' && <span className="render-play" aria-hidden="true"><Play size={22} /></span>}
        <i>{artifact.kind === 'video' ? <Video size={15} /> : <ImageIcon size={15} />}</i>
      </button>
      <span className="render-copy">
        <strong>{label}</strong>
        <small title={meta || undefined}>{meta || formatBytes(artifact.size)}</small>
      </span>
    </article>
  );
}

function MediaSection({title, detail, artifacts, onPreview, emptyTitle, emptyDetail}: {
  title: string;
  detail: string;
  artifacts: Artifact[];
  onPreview: (artifact: Artifact, items: Artifact[]) => void;
  emptyTitle: string;
  emptyDetail: string;
}) {
  return (
    <section className="artifact-content-section">
      <SectionHeading title={title} detail={detail} count={artifacts.length} />
      {artifacts.length > 0 ? (
        <div className="render-grid">
          {artifacts.map((artifact, index) => (
            <MediaCard key={artifact.path} artifact={artifact} items={artifacts} onPreview={onPreview} eager={index < EAGER_MEDIA_COUNT} />
          ))}
        </div>
      ) : (
        <div className="renders-empty">
          <i><Film size={22} /></i>
          <strong>{emptyTitle}</strong>
          <span>{emptyDetail}</span>
        </div>
      )}
    </section>
  );
}

type FrameGroup = {key: string; label: string; shot: number | null; items: Artifact[]};

function frameRank(artifact: Artifact): number {
  const normalized = artifact.path.replace(/\\/g, '/').toLowerCase();
  if (/\/first_frame\.png$/.test(normalized)) return 0;
  if (/\/first_frame_candidates\//.test(normalized)) return 1;
  if (/\/last_frame\.png$/.test(normalized)) return 2;
  if (/\/last_frame_candidates\//.test(normalized)) return 3;
  return 4;
}

function shortFrameLabel(artifact: Artifact): string {
  const normalized = artifact.path.replace(/\\/g, '/').toLowerCase();
  if (/\/first_frame\.png$/.test(normalized)) return '首帧';
  if (/\/last_frame\.png$/.test(normalized)) return '末帧';
  if (/\/first_frame_candidates\//.test(normalized)) return '首帧候选';
  if (/\/last_frame_candidates\//.test(normalized)) return '末帧候选';
  const candidate = /candidate_(\d+)\.png$/.exec(normalized);
  if (candidate) return `候选 ${Number(candidate[1]) + 1}`;
  return visualName(artifact);
}

function groupKeyFramesByShot(artifacts: Artifact[]): FrameGroup[] {
  const groups = new Map<string, FrameGroup>();
  artifacts.forEach((artifact) => {
    const match = /(?:^|\/)shots\/(\d+)(?:\/|$)/i.exec(artifact.path.replace(/\\/g, '/'));
    const shot = match ? Number(match[1]) : null;
    const key = shot === null ? 'other' : `shot-${shot}`;
    if (!groups.has(key)) groups.set(key, {key, label: shot === null ? '其他画面' : `镜头 ${shot + 1}`, shot, items: []});
    groups.get(key)!.items.push(artifact);
  });
  return [...groups.values()]
    .map((group) => ({...group, items: [...group.items].sort((left, right) => frameRank(left) - frameRank(right) || left.path.localeCompare(right.path, undefined, {numeric: true}))}))
    .sort((left, right) => (left.shot ?? Number.MAX_SAFE_INTEGER) - (right.shot ?? Number.MAX_SAFE_INTEGER) || left.key.localeCompare(right.key));
}

function KeyFrameSection({artifacts, onPreview}: {artifacts: Artifact[]; onPreview: (artifact: Artifact, items: Artifact[]) => void}) {
  const groups = useMemo(() => groupKeyFramesByShot(artifacts), [artifacts]);
  const eagerPaths = useMemo(() => new Set(artifacts.slice(0, EAGER_MEDIA_COUNT).map((artifact) => artifact.path)), [artifacts]);
  return (
    <section className="artifact-content-section">
      <SectionHeading title="关键帧与候选画面" detail="按镜头分组：首帧/末帧与候选图（中间产物，用于生成片段）" count={artifacts.length} />
      {groups.length > 0 ? (
        <div className="keyframe-shot-grid">
          {groups.map((group) => (
            <article key={group.key} className="keyframe-shot">
              <header>
                <strong>{group.label}</strong>
                <small>{group.items.length} 张</small>
              </header>
              <div className="keyframe-shot-frames">
                {group.items.map((artifact) => (
                  <KeyFrameThumb
                    key={artifact.path}
                    artifact={artifact}
                    items={group.items}
                    eager={eagerPaths.has(artifact.path)}
                    onPreview={onPreview}
                  />
                ))}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="renders-empty">
          <i><Film size={22} /></i>
          <strong>暂无关键帧</strong>
          <span>生成视频片段前，各镜头关键帧会显示在这里</span>
        </div>
      )}
    </section>
  );
}

function KeyFrameThumb({artifact, items, eager, onPreview}: {
  artifact: Artifact;
  items: Artifact[];
  eager: boolean;
  onPreview: (artifact: Artifact, items: Artifact[]) => void;
}) {
  const [dimensions, setDimensions] = useState<{width: number; height: number}>();
  const orientation = dimensions ? resolveMediaOrientation(dimensions.width, dimensions.height) : 'portrait';
  const label = visualArtifactLabel(artifact);
  return (
    <button
      type="button"
      className={`keyframe-frame is-${orientation}`}
      onClick={() => onPreview(artifact, items)}
      aria-label={`预览 ${label}`}
      title={label}
    >
      <img
        src={mediaUrl(artifact)}
        alt={label}
        loading={eager ? 'eager' : 'lazy'}
        decoding="async"
        style={dimensions ? {aspectRatio: `${dimensions.width} / ${dimensions.height}`} : undefined}
        onLoad={(event) => {
          const element = event.currentTarget;
          if (element.naturalWidth && element.naturalHeight) {
            setDimensions({width: element.naturalWidth, height: element.naturalHeight});
          }
        }}
      />
      <span>{shortFrameLabel(artifact)}</span>
    </button>
  );
}

function VideoThumbnail({artifact, onMetadata}: {artifact: Artifact; onMetadata?: (meta: VideoMetadata) => void}) {
  const cacheKey = `${artifact.path}:${artifact.updatedAt}`;
  const [thumbnail, setThumbnail] = useState(videoThumbnailCache.get(cacheKey) || '');
  const [visible, setVisible] = useState(Boolean(thumbnail));
  const [failed, setFailed] = useState(false);
  const hostRef = useRef<HTMLSpanElement>(null);
  const metadataRef = useRef(onMetadata);
  metadataRef.current = onMetadata;

  // P2：优先用服务端 ffmpeg 生成的 poster（稳定、不依赖浏览器截帧），失败才回退前端截帧。
  const posterUrl = artifact.url ? artifact.url.replace('/api/artifact?', '/api/video-poster?') : '';

  useEffect(() => {
    if (thumbnail || !posterUrl) return;
    let cancelled = false;
    fetch(posterUrl).then((response) => {
      if (cancelled) return;
      if (response.ok) {
        videoThumbnailCache.set(cacheKey, posterUrl);
        void writeCachedThumbnail(cacheKey, posterUrl).catch(() => undefined);
        setThumbnail(posterUrl);
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [posterUrl, thumbnail, cacheKey]);

  useEffect(() => {
    if (thumbnail) return;
    let cancelled = false;
    void readCachedThumbnail(cacheKey).then((cached) => {
      if (!cancelled && cached) {
        videoThumbnailCache.set(cacheKey, cached);
        setThumbnail(cached);
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [cacheKey, thumbnail]);

  useEffect(() => {
    const node = hostRef.current;
    if (!node) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, {rootMargin: '240px'});
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // 已命中缓存的缩略图时，仍单独探测一次元数据，让卡片能显示真实时长/分辨率。
  useEffect(() => {
    if (!visible || !thumbnail) return;
    const video = document.createElement('video');
    let cancelled = false;
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    const emit = () => {
      if (cancelled || !video.videoWidth || !video.videoHeight) return;
      metadataRef.current?.({
        width: video.videoWidth,
        height: video.videoHeight,
        duration: Number.isFinite(video.duration) ? video.duration : 0,
      });
    };
    video.addEventListener('loadedmetadata', emit, {once: true});
    video.src = mediaUrl(artifact);
    video.load();
    return () => {
      cancelled = true;
      video.removeAttribute('src');
      video.load();
    };
  }, [artifact, thumbnail, visible]);

  useEffect(() => {
    if (!visible || thumbnail) return;
    const video = document.createElement('video');
    let cancelled = false;
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.src = mediaUrl(artifact);

    const capture = () => {
      if (cancelled || !video.videoWidth || !video.videoHeight) return;
      try {
        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const context = canvas.getContext('2d');
        if (!context) return;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.78);
        videoThumbnailCache.set(cacheKey, dataUrl);
        void writeCachedThumbnail(cacheKey, dataUrl).catch(() => undefined);
        setThumbnail(dataUrl);
      } catch {
        // Some providers do not expose frame pixels; keep the neutral poster fallback.
        setFailed(true);
      }
    };
    const onLoaded = () => {
      if (video.videoWidth && video.videoHeight) {
        metadataRef.current?.({
          width: video.videoWidth,
          height: video.videoHeight,
          duration: Number.isFinite(video.duration) ? video.duration : 0,
        });
      }
      if (video.duration > 0.2) {
        video.currentTime = Math.min(0.2, video.duration / 5);
      } else {
        capture();
      }
    };
    video.addEventListener('error', () => setFailed(true), {once: true});
    video.addEventListener('loadeddata', onLoaded, {once: true});
    video.addEventListener('seeked', capture, {once: true});
    video.load();
    return () => {
      cancelled = true;
      video.removeAttribute('src');
      video.load();
    };
  }, [artifact, cacheKey, thumbnail, visible]);

  return (
    <span ref={hostRef} className={`video-thumbnail ${thumbnail ? 'is-ready' : failed ? 'is-failed' : 'is-loading'}`}>
      {thumbnail ? <img src={thumbnail} alt="" /> : <span><Film size={20} /><small>{failed ? '暂无缩略图' : '正在生成缩略图'}</small></span>}
    </span>
  );
}

function AssetSection({characters, scenes, props, voices, onPreview, sessionId, onRefresh}: {characters: Artifact[]; scenes: Artifact[]; props: Artifact[]; voices: Artifact[]; onPreview: (artifact: Artifact, items: Artifact[]) => void; sessionId?: string; onRefresh?: () => void}) {
  const groups = useMemo(() => groupCharacterPortraits(characters), [characters]);
  async function regenerateCharacter(name: string) {
    if (!sessionId) return;
    const confirmed = await dialog.confirm({
      title: `重新生成「${name}」的设定图？`,
      message: '将清除该角色现有的设定图，并标记为待重新生成。\n本次会消耗 1 次生图额度。',
      confirmText: '重新生成',
      tone: 'danger',
    });
    if (!confirmed) return;
    try {
      const result = await regenerateCharacterPortraits(sessionId, [name]);
      if (result.count > 0) {
        notify(`已标记「${name}」重新生成，回到对话点击「继续」即可按新模板重跑`);
        onRefresh?.();
      } else {
        notify(`「${name}」没有已落盘的角色立绘可清除`, 'error');
      }
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), 'error');
    }
  }
  async function regenerateAll() {
    if (!sessionId) return;
    const count = groups.length;
    if (count === 0) return;
    const confirmed = await dialog.confirm({
      title: `重新生成全部 ${count} 个角色的设定图？`,
      message: `将清除这 ${count} 个角色现有的设定图，并全部标记为待重新生成。\n本次约消耗 ${count} 次生图额度。`,
      confirmText: `重新生成 ${count} 个`,
      tone: 'danger',
    });
    if (!confirmed) return;
    try {
      const result = await regenerateCharacterPortraits(sessionId, '*');
      notify(`已标记 ${result.count} 个角色重新生成，回到对话点击「继续」即可按新模板重跑`);
      onRefresh?.();
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), 'error');
    }
  }
  return (
    <section className="artifact-content-section">
      <SectionHeading title="资产" detail="角色 · 场景 · 道具 · 音色（点击图片可放大，点击音色可播放）" />
      <div className="asset-categories">
        <div className="asset-category">
          <h3><User size={15} />角色 <span>{characters.length}</span>
            {groups.length > 0 && sessionId && (
              <button type="button" className="asset-regen-all" onClick={() => void regenerateAll()} title="全部角色重新生成（清产物后回对话点「继续」）">
                <RotateCcw size={13} />全部重新生成
              </button>
            )}
          </h3>
          {groups.length > 0 ? (
            <div className="asset-character-list">
              {groups.map(([group, items]) => (
                <div key={group} className="asset-character">
                  <div className="asset-character-head">
                    <strong>{group}</strong>
                    {sessionId && (
                      <button type="button" className="asset-regen-one" onClick={() => void regenerateCharacter(group)} title={`重新生成 ${group} 的设定图`}>
                        <RefreshCw size={12} />重新生成
                      </button>
                    )}
                  </div>
                  <div className="asset-character-views">
                    {items.map((artifact) => (
                      <figure key={artifact.path}>
                        <button className="asset-media" onClick={() => onPreview(artifact, items)} aria-label={`放大查看 ${visualArtifactLabel(artifact)}`}>
                          <img src={mediaUrl(artifact)} alt={visualArtifactLabel(artifact)} loading="lazy" />
                          <i><Maximize2 size={13} /></i>
                        </button>
                        <figcaption>{portraitViewLabel(artifact)}</figcaption>
                      </figure>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <AssetEmpty icon={<User size={18} />} title="暂无角色立绘" detail="剧本生成后，角色的正面/侧面/背面立绘会归档显示在这里。" />
          )}
        </div>
        <div className="asset-category">
          <h3><Mountain size={15} />场景 <span>{scenes.length}</span></h3>
          {scenes.length > 0 ? (
            <div className="asset-asset-grid">
              {scenes.map((artifact) => (
                <figure key={artifact.path}>
                  <button className="asset-media is-wide" onClick={() => onPreview(artifact, scenes)} aria-label={`放大查看 ${visualArtifactLabel(artifact)}`}>
                    <img src={mediaUrl(artifact)} alt={visualArtifactLabel(artifact)} loading="lazy" />
                    <i><Maximize2 size={14} /></i>
                  </button>
                  <figcaption>{sceneAssetLabel(artifact)}</figcaption>
                </figure>
              ))}
            </div>
          ) : (
            <AssetEmpty icon={<Mountain size={18} />} title="暂未生成场景资产" detail="渲染时会自动提取场次视觉设定并生成环境基准图，作为该场次各镜头的一致性锚点。" />
          )}
        </div>
        <div className="asset-category">
          <h3><Package size={15} />道具 <span>{props.length}</span></h3>
          {props.length > 0 ? (
            <div className="asset-asset-grid">
              {props.map((artifact) => (
                <figure key={artifact.path}>
                  <button className="asset-media is-wide" onClick={() => onPreview(artifact, props)} aria-label={`放大查看 ${visualArtifactLabel(artifact)}`}>
                    <img src={mediaUrl(artifact)} alt={visualArtifactLabel(artifact)} loading="lazy" />
                    <i><Maximize2 size={14} /></i>
                  </button>
                  <figcaption>{propAssetLabel(artifact)}</figcaption>
                </figure>
              ))}
            </div>
          ) : (
            <AssetEmpty icon={<Package size={18} />} title="暂未生成道具资产" detail="渲染时会自动识别剧本中的关键道具并生成外观基准图，保持跨镜头一致。" />
          )}
        </div>
        <div className="asset-category">
          <h3><Music size={15} />音色 <span>{voices.length}</span></h3>
          {voices.length > 0 ? (
            <div className="asset-voice-list">
              {voices.map((artifact) => (
                <VoicePlayer key={artifact.path} artifact={artifact} onPreview={(item) => onPreview(item, voices)} />
              ))}
            </div>
          ) : (
            <AssetEmpty icon={<Music size={18} />} title="暂未生成音色资产" detail="配置百炼音频后，渲染时会为每个角色创建专属音色文件（参考音频），用于 Seedance 音画同步。" />
          )}
        </div>
      </div>
    </section>
  );
}

function AssetEmpty({icon, title, detail}: {icon: ReactNode; title: string; detail: string}) {
  return (
    <div className="asset-empty">
      <span className="asset-empty-icon">{icon}</span>
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}

function VoicePlayer({artifact, onPreview}: {artifact: Artifact; onPreview: (artifact: Artifact) => void}) {
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const label = voiceAssetLabel(artifact);
  const toggle = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) void audio.play(); else audio.pause();
  };
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnded = () => setPlaying(false);
    audio.addEventListener('play', onPlay);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('ended', onEnded);
    return () => {
      audio.removeEventListener('play', onPlay);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('ended', onEnded);
    };
  }, []);
  return (
    <figure className="asset-voice">
      <button className="asset-voice-play" onClick={toggle} aria-label={playing ? `暂停 ${label}` : `播放 ${label}`} title={playing ? '暂停' : '播放'}>
        {playing ? <Pause size={15} /> : <Play size={15} />}
      </button>
      <div className="asset-voice-body">
        <figcaption>{label}</figcaption>
        <div className="asset-voice-tools">
          <audio ref={audioRef} src={mediaUrl(artifact)} preload="metadata" />
          <button className="asset-voice-expand" onClick={() => onPreview(artifact)} aria-label={`放大查看 ${label}`} title="放大查看">
            <Maximize2 size={13} />
          </button>
        </div>
      </div>
    </figure>
  );
}

function voiceAssetLabel(artifact: Artifact): string {
  return artifact.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
}

function sceneAssetLabel(artifact: Artifact): string {
  const match = normalizeArtifactPath(artifact.path).match(/(?:^|\/)scenes\/([^/]+)$/);
  if (match?.[1]) {
    const sceneMatch = match[1].match(/^scene_(\d+)/i);
    return sceneMatch ? `第${Number(sceneMatch[1]) + 1}场` : match[1].replace(/\.[^.]+$/, '');
  }
  return artifact.name.replace(/\.[^.]+$/, '');
}

function propAssetLabel(artifact: Artifact): string {
  const match = normalizeArtifactPath(artifact.path).match(/(?:^|\/)props\/([^/]+)$/);
  const name = match?.[1]?.replace(/\.[^.]+$/, '') || artifact.name.replace(/\.[^.]+$/, '');
  return name.replace(/[_-]+/g, ' ');
}

function groupCharacterPortraits(artifacts: Artifact[]): Array<[string, Artifact[]]> {
  const map = new Map<string, Artifact[]>();
  artifacts.forEach((artifact) => {
    const normalized = normalizeArtifactPath(artifact.path);
    // 形态立绘：character_portraits/{角色}/variants/{形态}/{view}.png → 归属角色组
    const variantMatch = normalized.match(/(?:^|\/)character_portraits\/([^/]+)\/variants\/([^/]+)\/([^/]+)$/);
    const match = normalized.match(/(?:^|\/)character_portraits\/([^/]+)\/([^/]+)$/);
    const rawGroup = variantMatch?.[1] || match?.[1] || artifact.name.replace(/\.[^.]+$/, '');
    const group = rawGroup.replace(/^\d+_/, '') || rawGroup;
    if (!map.has(group)) map.set(group, []);
    map.get(group)!.push(artifact);
  });
  return [...map.entries()];
}

function portraitViewLabel(artifact: Artifact): string {
  const normalized = normalizeArtifactPath(artifact.path);
  // 形态立绘：显示「形态名 · 视图」
  const variantMatch = normalized.match(/(?:^|\/)character_portraits\/[^/]+\/variants\/([^/]+)\/([^/]+)$/);
  if (variantMatch) {
    const variantName = decodeURIComponent(variantMatch[1]);
    const viewBase = variantMatch[2].replace(/\.[^.]+$/, '').toLowerCase();
    const viewLabels: Record<string, string> = {sheet: '三视图合图', front: '正面', side: '侧面', back: '背面'};
    return `${variantName} · ${viewLabels[viewBase] || viewBase}`;
  }
  const base = artifact.name.replace(/\.[^.]+$/, '').toLowerCase();
  const labels: Record<string, string> = {sheet: '角色设定图（三视图）', front: '正面', side: '侧面', back: '背面'};
  return labels[base] || base;
}

function normalizeArtifactPath(path: string): string {
  return path.replace(/\\/g, '/');
}

function RelatedVisuals({artifacts, onPreview}: {artifacts: Artifact[]; onPreview: (artifact: Artifact, items: Artifact[]) => void}) {
  return (
    <aside className="artifact-related-visuals" aria-label="关联视觉">
      <header>
        <div><span>关联视觉</span><strong>{artifacts.length}</strong></div>
        <small>{artifacts.length > 0 ? '点击预览' : '等待渲染'}</small>
      </header>
      {artifacts.length > 0 ? (
        <div className="related-visual-grid">
          {artifacts.map((artifact) => (
            <button key={artifact.path} onClick={() => onPreview(artifact, artifacts)} aria-label={`预览 ${visualArtifactLabel(artifact)}`}>
              <span>
                {artifact.kind === 'image'
                  ? <img src={mediaUrl(artifact)} alt={visualArtifactLabel(artifact)} />
                  : <VideoThumbnail artifact={artifact} />}
                <i><Maximize2 size={14} /></i>
              </span>
              <strong>{visualArtifactLabel(artifact)}</strong>
              <small>{formatBytes(artifact.size)}</small>
            </button>
          ))}
        </div>
      ) : (
        <div className="related-visual-empty">
          <ImageIcon size={19} />
          <span>匹配的图片和片段将显示在该文档旁。</span>
        </div>
      )}
    </aside>
  );
}

function MediaPreviewDialog({artifact, items, onClose, onNavigate}: {artifact: Artifact; items: Artifact[]; onClose: () => void; onNavigate: (artifact: Artifact) => void}) {
  const [copied, setCopied] = useState(false);
  const index = items.findIndex((item) => item.path === artifact.path);
  const currentIndex = index >= 0 ? index : 0;
  const canNavigate = items.length > 1;
  const go = (delta: number) => {
    if (!canNavigate) return;
    const nextIndex = (currentIndex + delta + items.length) % items.length;
    setCopied(false);
    onNavigate(items[nextIndex]);
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowLeft') go(-1);
      if (event.key === 'ArrowRight') go(1);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  async function copyPath() {
    try {
      await navigator.clipboard.writeText(artifact.path);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="media-preview-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="media-preview-dialog" role="dialog" aria-modal="true" aria-label={`预览 ${visualArtifactLabel(artifact)}`}>
        <header>
          <div>
            <strong>{visualArtifactLabel(artifact)}</strong>
            <span>{formatBytes(artifact.size)}{canNavigate ? ` · 第 ${currentIndex + 1}/${items.length} 项` : ''}</span>
          </div>
          <div className="media-preview-actions">
            <button className="icon-button" onClick={() => go(-1)} disabled={!canNavigate} aria-label="上一个媒体" title="上一个（←）"><ChevronLeft size={18} /></button>
            <button className="icon-button" onClick={() => go(1)} disabled={!canNavigate} aria-label="下一个媒体" title="下一个（→）"><ChevronRight size={18} /></button>
            <button className="media-preview-copy" onClick={() => void copyPath()} title={artifact.path}><Copy size={15} /><span>{copied ? '已复制' : '复制路径'}</span></button>
            <a className="media-preview-download" href={mediaUrl(artifact)} download={artifact.name}><Download size={15} /><span>下载</span></a>
            <button className="icon-button" onClick={onClose} aria-label="关闭预览"><X size={18} /></button>
          </div>
        </header>
        <div className="media-preview-stage" key={artifact.path}>
          {artifact.kind === 'image'
            ? <img src={mediaUrl(artifact)} alt={visualArtifactLabel(artifact)} />
            : artifact.kind === 'audio'
              ? <audio src={mediaUrl(artifact)} controls autoPlay preload="metadata" />
              : <video src={mediaUrl(artifact)} controls autoPlay playsInline preload="metadata" />}
        </div>
      </section>
    </div>
  );
}

function ArtifactsLoading() {
  return (
    <div className="artifacts-loading" aria-label="正在加载产物">
      <div><span className="upload-spinner" /><strong>正在整理产物</strong></div>
      <DocumentSkeleton />
    </div>
  );
}

function DocumentSkeleton() {
  return (
    <div className="document-skeleton" aria-label="正在加载文档">
      <i /><i /><i />
      <div><i /><i /><i /><i /></div>
      <div><i /><i /><i /></div>
    </div>
  );
}

function SectionHeading({title, detail, count}: {title: string; detail: string; count?: number}) {
  return (
    <header className="artifact-section-heading">
      <div className="artifact-section-title">
        <h2>{title}</h2>
        {count !== undefined && <small className="artifact-section-count">{count}</small>}
      </div>
      <span>{detail}</span>
    </header>
  );
}

export function StoryboardPanel({open, artifacts, activeRenderStage, onClose, onCountChange}: {
  open: boolean;
  artifacts: Artifact[];
  activeRenderStage?: string;
  onClose: () => void;
  onCountChange: (count: number) => void;
}) {
  const storyboardArtifacts = useMemo(
    () => artifacts.filter(isStoryboardArtifact).sort((left, right) => left.path.localeCompare(right.path, undefined, {numeric: true})),
    [artifacts],
  );
  const [previews, setPreviews] = useState<StoryboardPreview[]>([]);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setError('');
    setIndex(0);
    if (storyboardArtifacts.length === 0) {
      setLoading(false);
      setPreviews([]);
      onCountChange(0);
      return () => { cancelled = true; };
    }
    setLoading(true);
    void Promise.allSettled(storyboardArtifacts.map(async (artifact) => ({
      artifact,
      document: await getJsonArtifact(artifact),
    }))).then((results) => {
      if (cancelled) return;
      const loaded = results.flatMap((result) => result.status === 'fulfilled'
        ? extractStoryboardPreviews(result.value.document, result.value.artifact.path)
        : []);
      setPreviews(loaded);
      onCountChange(loaded.length);
      if (loaded.length === 0 && results.some((result) => result.status === 'rejected')) {
        setError('分镜描述加载失败');
      }
    }).finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [storyboardArtifacts, onCountChange]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setIndex((current) => Math.max(0, current - 1));
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setIndex((current) => Math.min(previews.length - 1, current + 1));
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose, open, previews.length]);

  const current = previews[index];
  const readiness = useMemo(
    () => deriveStoryboardReadiness(artifacts, previews.length),
    [artifacts, previews.length],
  );
  const activeCheckpoint = activeRenderStage ? activeRenderCheckpoint(activeRenderStage) : undefined;
  return (
    <aside className={`storyboard-panel ${open ? 'is-open' : ''}`}>
      <header>
        <div>
          <strong>分镜</strong>
          {previews.length > 0 ? (
            <label className="storyboard-jump">
              <span>跳转</span>
              <select value={index} onChange={(event) => setIndex(Number(event.target.value))} aria-label="跳转到分镜">
                {previews.map((preview, previewIndex) => (
                  <option key={preview.id} value={previewIndex}>第 {previewIndex + 1} 个 / 共 {previews.length} 个</option>
                ))}
              </select>
            </label>
          ) : <span>预览</span>}
        </div>
        <button className="icon-button" onClick={onClose} aria-label="关闭分镜预览"><X size={18} /></button>
      </header>
      <section className="render-readiness" aria-label="渲染就绪度">
        <div className="render-readiness-summary">
          <strong>{readiness.readyToRender ? '可以渲染' : '规划未完成'}</strong>
        </div>
        <div className="render-checkpoints">
          <ReadinessCheckpoint
            label="分镜"
            detail={readiness.storyboards.count === 1 ? '1 个镜头' : `${readiness.storyboards.count} 个镜头`}
            status={readiness.storyboards.status}
          />
          <ReadinessCheckpoint
            label="镜头描述"
            detail={`${readiness.shotDescriptions.count}/${readiness.shotDescriptions.expected || 0}`}
            status={readiness.shotDescriptions.status}
          />
          <ReadinessCheckpoint
            label="机位规划"
            detail={`${readiness.cameraPlans.count}/${readiness.cameraPlans.expected || 0}`}
            status={readiness.cameraPlans.status}
          />
        </div>
        <div className="render-stage-heading">
          <span>渲染</span>
          <small>{activeRenderStage ? '生成中' : readiness.render.finalVideo.status === 'ready' ? '已完成' : readiness.render.started ? '部分完成' : '未开始'}</small>
        </div>
        <div className="render-checkpoints">
          <ReadinessCheckpoint
            label="关键帧"
            detail={`${readiness.render.frames.count}/${readiness.render.frames.expected || 0}`}
            status={readiness.render.frames.status}
            generating={activeCheckpoint === 'frames'}
          />
          <ReadinessCheckpoint
            label="视频片段"
            detail={`${readiness.render.clips.count}/${readiness.render.clips.expected || 0}`}
            status={readiness.render.clips.status}
            generating={activeCheckpoint === 'clips'}
          />
          <ReadinessCheckpoint
            label="成片"
            detail={readiness.render.finalVideo.status === 'ready' ? '就绪' : '待处理'}
            status={readiness.render.finalVideo.status}
            generating={activeCheckpoint === 'finalVideo'}
          />
        </div>
      </section>
      <div className="storyboard-description" aria-live="polite">
        {loading ? <span>正在加载分镜…</span> : error ? <span className="is-error">{error}</span> : current ? <p>{current.description}</p> : <span>暂无分镜描述</span>}
      </div>
      <footer>
        <button onClick={() => setIndex((currentIndex) => Math.max(0, currentIndex - 1))} disabled={index === 0 || previews.length === 0} aria-label="上一个分镜">
          <ChevronUp size={17} /><span>上一个</span>
        </button>
        <button onClick={() => setIndex((currentIndex) => Math.min(previews.length - 1, currentIndex + 1))} disabled={index >= previews.length - 1 || previews.length === 0} aria-label="下一个分镜">
          <ChevronDown size={17} /><span>下一个</span>
        </button>
      </footer>
    </aside>
  );
}

function ReadinessCheckpoint({label, detail, status, generating = false}: {label: string; detail: string; status: ReadinessStatus; generating?: boolean}) {
  return (
    <div className="render-checkpoint">
      <StatusLight status={status} generating={generating} />
      <span>{label}</span>
      <small>{detail}</small>
    </div>
  );
}

function StatusLight({status, generating = false}: {status: ReadinessStatus; generating?: boolean}) {
  return <i className={`status-light is-${status} ${generating ? 'is-generating' : ''}`} aria-label={generating ? `${status}, generating` : status} />;
}

type StructuredProps = {query: string; forceExpanded?: boolean};

function StructuredDocument({value, artifact, query, forceExpanded}: {value: JsonValue; artifact: Artifact} & StructuredProps) {
  const normalizedQuery = query.trim().toLowerCase();
  if (Array.isArray(value)) {
    if (value.length === 0) return <div className="structured-empty">此文档为空</div>;
    if (value.every(isJsonPrimitive)) return <StructuredField label="内容" value={formatStructuredValue(value)} forceExpanded={forceExpanded} />;
    const records = value.filter((record) => jsonValueMatches(record, normalizedQuery));
    if (records.length === 0) return <div className="structured-empty">没有匹配的文档内容</div>;
    return (
      <div className="structured-records">
        {records.map((record, index) => (
          <StructuredRecord key={index} title={structuredRecordTitle(record, index, artifact)} value={record} depth={0} query={normalizedQuery} forceExpanded={forceExpanded} />
        ))}
      </div>
    );
  }
  if (isJsonObject(value)) {
    const entries = Object.entries(value).filter(([key, entryValue]) => !isArtifactPathField(key) && jsonValueMatches(entryValue, normalizedQuery, key));
    if (entries.length === 0) return <div className="structured-empty">没有匹配的文档内容</div>;
    if (entries.length > 0 && entries.every(([, entryValue]) => isJsonObject(entryValue))) {
      return (
        <div className="structured-records">
          {entries.map(([key, entryValue]) => (
            <StructuredRecord key={key} title={friendlyFieldLabel(key)} value={entryValue} depth={0} query={normalizedQuery} forceExpanded={forceExpanded} />
          ))}
        </div>
      );
    }
    return <StructuredRecord title="概览" value={value} depth={0} query={normalizedQuery} forceExpanded={forceExpanded} />;
  }
  return <StructuredField label="内容" value={formatStructuredValue(value)} forceExpanded={forceExpanded} />;
}

function StructuredRecord({title, value, depth, query, forceExpanded}: {title: string; value: JsonValue; depth: number} & StructuredProps) {
  if (!isJsonObject(value)) return <StructuredField label={title} value={formatStructuredValue(value)} forceExpanded={forceExpanded} />;
  const entries = Object.entries(value).filter(([key, entryValue]) => !isArtifactPathField(key) && jsonValueMatches(entryValue, query, key));
  const fields = entries.filter(([, entryValue]) => isJsonPrimitive(entryValue) || isPrimitiveArray(entryValue));
  const nested = entries.filter(([, entryValue]) => !isJsonPrimitive(entryValue) && !isPrimitiveArray(entryValue));
  return (
    <section className={`structured-record depth-${Math.min(depth, 3)}`}>
      <header><h3>{title}</h3></header>
      {fields.length > 0 && (
        <div className="structured-fields">
          {fields.map(([key, entryValue]) => (
            <StructuredField key={key} label={friendlyFieldLabel(key)} value={formatStructuredValue(entryValue, key)} forceExpanded={forceExpanded} />
          ))}
        </div>
      )}
      {nested.map(([key, entryValue]) => (
        <StructuredNested key={key} label={friendlyFieldLabel(key)} value={entryValue} depth={depth + 1} query={query} forceExpanded={forceExpanded} />
      ))}
    </section>
  );
}

function StructuredNested({label, value, depth, query, forceExpanded}: {label: string; value: JsonValue; depth: number} & StructuredProps) {
  if (depth > 5) return <StructuredField label={label} value="更多结构化细节" forceExpanded={forceExpanded} />;
  if (Array.isArray(value)) {
    if (value.length === 0) return <StructuredField label={label} value="无" forceExpanded={forceExpanded} />;
    const items = value.filter((item) => jsonValueMatches(item, query));
    if (items.length === 0) return null;
    return (
      <section className="structured-nested">
        <h4>{label}</h4>
        {items.map((item, index) => <StructuredRecord key={index} title={`${label} ${index + 1}`} value={item} depth={depth} query={query} forceExpanded={forceExpanded} />)}
      </section>
    );
  }
  return <StructuredRecord title={label} value={value} depth={depth} query={query} forceExpanded={forceExpanded} />;
}

function StructuredField({label, value, forceExpanded}: {label: string; value: string; forceExpanded?: boolean}) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (forceExpanded !== undefined) setExpanded(forceExpanded);
  }, [forceExpanded]);
  const isLong = value.length > 100;
  const shown = isLong && !expanded ? `${value.slice(0, 100)}…` : value;
  return (
    <div className={`structured-field ${isLong ? 'is-long' : ''}`}>
      <span>{label}</span>
      <p>{shown}</p>
      {isLong && (
        <button className="structured-field-toggle" onClick={() => setExpanded((current) => !current)}>
          {expanded ? '收起' : '展开'}
        </button>
      )}
    </div>
  );
}

function jsonValueMatches(value: JsonValue, query: string, key = ''): boolean {
  if (!query) return true;
  if (key.toLowerCase().includes(query)) return true;
  if (value === null) return 'null'.includes(query);
  if (typeof value === 'object') return JSON.stringify(value).toLowerCase().includes(query);
  return String(value).toLowerCase().includes(query);
}

function ArtifactsEmpty({title, detail}: {title: string; detail: string}) {
  return (
    <div className="artifacts-empty">
      <Files size={24} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function isPrimitiveArray(value: JsonValue): value is Array<string | number | boolean | null> {
  return Array.isArray(value) && value.every(isJsonPrimitive);
}

function projectTitle(session: SessionSummary): string {
  return session.projectName || session.idea || session.summary || '未命名视频';
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatArtifactAge(timestamp: string): string {
  const time = new Date(timestamp).getTime();
  if (!Number.isFinite(time)) return '';
  const diffSeconds = Math.round((Date.now() - time) / 1000);
  if (diffSeconds < 60) return '刚刚';
  if (diffSeconds < 3600) return `${Math.round(diffSeconds / 60)} 分钟前`;
  if (diffSeconds < 86400) return `${Math.round(diffSeconds / 3600)} 小时前`;
  return `${Math.round(diffSeconds / 86400)} 天前`;
}

export function mediaUrl(artifact: Artifact): string {
  const separator = artifact.url.includes('?') ? '&' : '?';
  return `${artifact.url}${separator}updated=${encodeURIComponent(artifact.updatedAt)}`;
}

export function visualArtifactLabel(artifact: Artifact): string {
  const normalized = artifact.path.replace(/\\/g, '/');
  // 形态立绘：角色 · 形态 · 视图
  const variantMatch = normalized.match(/(?:^|\/)character_portraits\/([^/]+)\/variants\/([^/]+)\/([^/]+)$/i);
  if (variantMatch) {
    const group = variantMatch[1].replace(/^\d+_/, '') || variantMatch[1];
    const variantName = decodeURIComponent(variantMatch[2]);
    return `${group} · ${variantName} · ${visualName(artifact)}`;
  }
  const portraitMatch = normalized.match(/(?:^|\/)character_portraits\/([^/]+)\/([^/]+)$/i);
  if (portraitMatch) {
    const group = portraitMatch[1].replace(/^\d+_/, '') || portraitMatch[1];
    return `${group} · ${visualName(artifact)}`;
  }
  const scene = normalized.match(/(?:^|\/)scene_(\d+)(?:\/|$)/i)?.[1];
  // 段级直出产物（segments/seg-N/video.mp4）按段号标注：这类片段集中在同一目录、
  // 文件名全是 video.mp4，没有段号标签时列表里 7 个卡片全叫"视频片段"，无法区分。
  const segment = normalized.match(/(?:^|\/)segments\/seg-(\d+)(?:\/|$)/i)?.[1];
  const shot = normalized.match(/(?:^|\/)shots\/(\d+)(?:\/|$)/i)?.[1];
  const context = [];
  if (scene !== undefined) context.push(`第${Number(scene) + 1}场`);
  if (segment !== undefined) context.push(`第${Number(segment) + 1}段`);
  if (shot !== undefined) context.push(`镜头${Number(shot) + 1}`);
  const base = visualName(artifact);
  return context.length > 0 ? `${context.join(' · ')} · ${base}` : base;
}

function visualName(artifact: Artifact): string {
  const name = artifact.name.toLowerCase();
  // 视频缩略图（管线/服务端在 <video>.poster.jpg 落盘）：不要拿文件名当标签展示。
  if (name.endsWith('.poster.jpg')) return '缩略图';
  if (name === 'final_video.mp4') return '成片';
  if (name === 'video.mp4') return '视频片段';
  if (name.startsWith('transition_video')) return '转场片段';
  if (name === 'first_frame.png') return '首帧';
  if (name === 'last_frame.png') return '末帧';
  if (name.startsWith('new_camera_')) return '新机位画面';
  const candidate = name.match(/^candidate_(\d+)\.png$/);
  if (candidate) return `候选 ${Number(candidate[1]) + 1}`;
  const portraitView = {sheet: '三视图合图', front: '正面', side: '侧面', back: '背面'}[name.replace(/\.[^.]+$/, '')];
  if (portraitView) return portraitView;
  return name
    .replace(/\.[^.]+$/, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
