import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {ArrowUpDown, Search} from 'lucide-react';
import {getLedger, type Ledger, type LedgerEpisode, type LedgerScriptGate} from './api';
import {QualityReportPanel} from './QualityReportPanel';

type Props = {
  session: string;
  actions?: ReactNode;
  onCreate?: () => void;
};

type EpisodeFilter = 'all' | 'active' | 'done' | 'attention';
type EpisodeSort = 'episode' | 'progress' | 'updated';

function statusTone(status: string): string {
  if (!status) return 'is-pending';
  if (status === 'rendered' || status === 'completed' || status === 'done') return 'is-done';
  // 资产生成阶段（不是渲染）：状态串是 assets_*，绝不能按"渲染中"上色。
  if (status === 'assets_generating') return 'is-running';
  if (status === 'assets_ready' || status === 'assets_confirmed') return 'is-done';
  if (status === 'assets_error' || status === 'assets_timeout' || status === 'assets_failed') return 'is-error';
  if (status === 'rendering' || status === 'running') return 'is-running';
  if (status === 'error' || status === 'failed') return 'is-error';
  return 'is-pending';
}

/** 名单 + "还有 N 个未列出"（服务端已合并"已生成 + 计划"，这里只负责标注截断）。 */
function displayTags(display?: {names?: string[]; hidden?: number} | null, fallback: string[] = []): string[] {
  if (!display) return fallback;
  const names = display.names ?? [];
  return (display.hidden ?? 0) > 0 ? [...names, `…还有 ${display.hidden} 个`] : names;
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待处理', planned: '已规划', extracted: '已提取', assets: '资产阶段',
    storyboard: '分镜阶段', ready: '就绪', rendering: '渲染中', rendered: '已渲染',
    partial_rendered: '部分已渲染', storyboard_ready: '分镜就绪',
    completed: '已完成', done: '已完成', running: '进行中', error: '出错', failed: '失败',
    assets_generating: '资产生成中', assets_ready: '资产已就绪', assets_confirmed: '资产已确认',
    assets_pending: '待生成资产', assets_error: '资产生成失败', assets_timeout: '资产生成超时',
    assets_cancelled: '资产生成已取消', interrupted: '已中断',
  };
  return map[status] ?? status;
}

function formatTokens(tokens: number): string {
  if (!tokens || tokens <= 0) return '0';
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return String(Math.round(tokens));
}

function isEpisodeDone(episode: LedgerEpisode): boolean {
  return episode.final_ready || episode.status === 'rendered' || episode.status === 'done' || episode.status === 'completed';
}

/**
 * 分镜"真实完成量"：storyboard.json 写的是计划镜数，分镜生成中断时会远大于
 * 已经落地 shot_description.json 的镜头数（实测 32 计划 / 5 落地）。
 * 分母用计划数会让进度条永远停在 0%，看起来像"一点没做"。
 */
function episodeShotTotal(episode: LedgerEpisode): number {
  const described = episode.described_count ?? null;
  if (typeof described === 'number' && described > 0) return described;
  return Math.max(episode.shot_count ?? 0, 0);
}

function episodeShotLabel(episode: LedgerEpisode): string {
  const planned = episode.shot_count ?? 0;
  const described = episode.described_count ?? null;
  if (typeof described === 'number' && described !== planned) return `${described}/${planned}`;
  return String(planned);
}

function episodeShotTitle(episode: LedgerEpisode): string {
  const planned = episode.shot_count ?? 0;
  const described = episode.described_count ?? null;
  if (typeof described === 'number' && described !== planned) {
    return `已落地 ${described} 镜 / 计划 ${planned} 镜（分镜生成未完成，续跑后自动补齐）`;
  }
  return `计划并已落地 ${planned} 镜`;
}

function episodeProgress(episode: LedgerEpisode): number {
  if (episode.final_ready) return 100;
  // 段级直出的真实完成量是"段"，用镜头数当分母会算成 7/44 这种看着像"刚开头"的假进度。
  const segmentTotal = episode.segments_total ?? 0;
  if (segmentTotal > 0 && typeof episode.segments_rendered === 'number') {
    const ratio = Math.max(0, Math.min(1, episode.segments_rendered / segmentTotal));
    return Math.max(0, Math.min(99, Math.round(ratio * 100)));
  }
  const total = episodeShotTotal(episode);
  if (total === 0) return episode.storyboard_ready ? 12 : 0;
  const keyframes = Math.min(Math.max(episode.keyframes, 0), total);
  const clips = Math.min(Math.max(episode.clips, 0), total);
  const ratio = clips > 0 ? clips / total : keyframes / total;
  return Math.max(0, Math.min(99, Math.round(ratio * 100)));
}

function episodeStage(episode: LedgerEpisode): string {
  const total = episodeShotTotal(episode);
  if (episode.final_ready) return '成片完成';
  // 残缺成片（final_video 在盘但缺段/缺镜）必须显式说清"缺多少"，不能只报"片段 N/M"。
  if (episode.partial_render) {
    const segmentTotal = episode.segments_total ?? 0;
    if (segmentTotal > 0) return `残缺成片 · 段 ${episode.segments_rendered ?? 0}/${segmentTotal}（续跑补全）`;
    return '残缺成片（续跑补全）';
  }
  // 本集资产还没齐（Python 侧唯一口径判定的结果）：这是"一集一集"流程里最该先处理的
  // 一步，必须排在分镜/渲染阶段文案之前——否则用户看到"分镜已就绪"却不知道也出不了片。
  // 已出片的集不算（成片/残缺成片在上面已返回），避免成片集被反向报成"待补资产"。
  if (episode.assets_ready === false) return '待补资产';
  if (episode.clips > 0) return `片段 ${episode.clips}/${total}`;
  if (episode.keyframes > 0) return `关键帧 ${episode.keyframes}/${total}`;
  if (episode.storyboard_ready) {
    const planned = episode.shot_count ?? 0;
    const described = episode.described_count ?? null;
    // 分镜只做了一半不能报"已就绪"——否则用户以为可以直接进渲染，实际渲染门禁会拒。
    if (typeof described === 'number' && described > 0 && described < planned) return `分镜 ${described}/${planned} 镜（未完成）`;
    return '分镜已就绪';
  }
  return statusLabel(episode.status);
}

/** 本集缺项总数（身份级 + 道具/音色），前端只做求和，判定口径来自 Python 落盘的文件。 */
function assetMissingCount(episode: LedgerEpisode): number {
  if (typeof episode.assets_missing_count === 'number') return episode.assets_missing_count;
  const missing = episode.assets_missing ?? {};
  return Object.values(missing).reduce((sum, items) => sum + (Array.isArray(items) ? items.length : 0), 0);
}

/** 本集缺项明细（悬停提示用）：身份级缺项排最前，因为它们会直接拦住分镜/出片。 */
function assetMissingTitle(episode: LedgerEpisode): string {
  const missing = episode.assets_missing ?? {};
  const labels: Record<string, string> = {characters: '角色立绘', variants: '角色形态', scenes: '场景图', props: '道具图', voices: '音色'};
  const parts = Object.entries(missing)
    .filter(([, items]) => Array.isArray(items) && items.length > 0)
    .map(([key, items]) => `${labels[key] ?? key}：${(items as string[]).slice(0, 12).join('、')}${(items as string[]).length > 12 ? ' 等' : ''}`);
  if (parts.length === 0) return '本集资产已齐';
  return `本集待补资产（角色立绘/场景图缺了就不能分镜、不能出片）\n${parts.join('\n')}`;
}

function formatUpdatedAt(value: string | null): string {
  if (!value) return '暂无更新时间';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(timestamp);
}

function Section({title, children, hint, wide}: {title: string; children: ReactNode; hint?: string; wide?: boolean}) {
  return (
    <section className={`ledger-section${wide ? ' is-wide' : ''}`}>
      <header className="ledger-section-head">
        <strong>{title}</strong>
        {hint && <span className="ledger-section-hint">{hint}</span>}
      </header>
      <div className="ledger-section-body">{children}</div>
    </section>
  );
}

function Collapse({label, text}: {label: string; text: string}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="ledger-collapse">
      <button type="button" className="ledger-collapse-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span>{label}</span>
        <i className={open ? 'is-open' : ''} aria-hidden="true" />
      </button>
      {open && <div className="ledger-collapse-body">{text}</div>}
    </div>
  );
}

function TagList({items, empty}: {items: string[]; empty: string}) {
  if (!items || items.length === 0) return <span className="ledger-empty">{empty}</span>;
  return (
    <div className="ledger-tags">
      {items.map((item, index) => <span key={`${item}-${index}`} className="ledger-tag">{item}</span>)}
    </div>
  );
}

export function LedgerView({session, actions, onCreate}: Props) {
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [episodeQuery, setEpisodeQuery] = useState('');
  const [episodeFilter, setEpisodeFilter] = useState<EpisodeFilter>('all');
  const [episodeSort, setEpisodeSort] = useState<EpisodeSort>('episode');

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError('');
    getLedger(session)
      .then((data) => {
        if (!alive) return;
        if ('empty' in data) {
          setLedger(null);
        } else {
          setLedger(data);
        }
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [session]);

  const bible = ledger?.bible ?? null;
  const assets = ledger?.assets ?? null;
  const render = ledger?.render ?? null;
  const budget = ledger?.budget ?? null;
  // 机检门禁：优先根级报告；多集项目报告写在 episodes/<ep>/ 下，按集合并成一份可看的清单
  const scriptGate = useMemo<LedgerScriptGate | null>(() => {
    const root = ledger?.script_gate ?? null;
    if (root) return root;
    const byEp = ledger?.storyboard?.by_episode ?? {};
    const gates = Object.entries(byEp)
      .map(([epId, item]) => ({epId, gate: item?.gate ?? null}))
      .filter((entry): entry is {epId: string; gate: LedgerScriptGate} => Boolean(entry.gate));
    if (gates.length === 0) return null;
    return {
      passed: gates.every((entry) => entry.gate.passed),
      legacy: gates.some((entry) => entry.gate.legacy),
      fails: gates.flatMap((entry) => entry.gate.fails.map((text) => `${entry.epId} · ${text}`)),
      warns: gates.flatMap((entry) => entry.gate.warns.map((text) => `${entry.epId} · ${text}`)),
      stats: null,
    };
  }, [ledger]);
  const gateTone: 'pass' | 'fail' | 'legacy' = !scriptGate
    ? 'pass'
    : scriptGate.legacy
      ? 'legacy'
      : scriptGate.passed
        ? 'pass'
        : 'fail';
  const episodes = ledger?.episodes ?? [];
  const episodeCount = episodes.length;
  const doneEpisodes = episodes.filter(isEpisodeDone).length;
  const activeEpisodes = episodes.filter((ep) => !isEpisodeDone(ep) && statusTone(ep.status) !== 'is-error').length;
  const errorEpisodes = episodes.filter((ep) => statusTone(ep.status) === 'is-error').length;
  // "一集一集"流程里最该先看的数字：本集资产还没齐的集数（判定来自 Python 落盘的 asset_status.json）
  const assetsPendingEpisodes = episodes.filter((ep) => ep.assets_ready === false && !ep.final_ready).length;
  const totalKeyframes = episodes.reduce((sum, ep) => sum + ep.keyframes, 0);
  const totalClips = episodes.reduce((sum, ep) => sum + ep.clips, 0);
  const storyboardShots = ledger?.storyboard?.shot_count ?? 0;
  const completionPercent = episodeCount > 0 ? Math.round((doneEpisodes / episodeCount) * 100) : 0;

  const visibleEpisodes = useMemo(() => {
    const query = episodeQuery.trim().toLowerCase();
    const filtered = episodes.filter((episode) => {
      const matchesQuery = !query
        || episode.ep_id.toLowerCase().includes(query)
        || (episode.title ?? '').toLowerCase().includes(query);
      if (!matchesQuery) return false;
      if (episodeFilter === 'done') return isEpisodeDone(episode);
      if (episodeFilter === 'active') return !isEpisodeDone(episode) && statusTone(episode.status) !== 'is-error';
      if (episodeFilter === 'attention') return statusTone(episode.status) === 'is-error';
      return true;
    });

    return [...filtered].sort((a, b) => {
      if (episodeSort === 'progress') {
        const diff = episodeProgress(b) - episodeProgress(a);
        if (diff !== 0) return diff;
      }
      if (episodeSort === 'updated') {
        const diff = (Date.parse(b.updated_at ?? '') || 0) - (Date.parse(a.updated_at ?? '') || 0);
        if (diff !== 0) return diff;
      }
      return a.ep_id.localeCompare(b.ep_id, undefined, {numeric: true});
    });
  }, [episodes, episodeFilter, episodeQuery, episodeSort]);

  return (
    <div className="ledger-view">
      <header className="workspace-header">
        <div className="workspace-title-row">
          <div className="workspace-title-copy">
            <strong>台账</strong>
            <span>全剧流程总览 · 一致性约束与进度断点</span>
          </div>
          {actions}
        </div>
      </header>

      {error && <div className="inline-error ledger-error" role="alert"><span>{error}</span></div>}

      {loading ? (
        <div className="ledger-loading"><div className="app-initializing-spinner" /><span>正在汇总台账数据…</span></div>
      ) : !ledger ? (
        <div className="ledger-empty-state">
          <strong>还没有可展示的台账</strong>
          <p>当前项目尚未生成剧本与资产数据；选中一个已开工的项目后再看。</p>
          {onCreate && <button type="button" className="primary-btn" onClick={onCreate}>回到工作区</button>}
        </div>
      ) : (
        <div className="ledger-content">
          {/* 概览统计 */}
          <div className="ledger-stat-grid">
            <div className="ledger-stat is-wide">
              <span className="ledger-stat-label">项目</span>
              {/* 项目名优先（它才是"项目"）；根剧本首行只作为副标题/悬停信息，
                  否则单集脚本被误写进根文件时，这里会显示成"第 26 集：立案"。 */}
              <strong title={ledger.meta.project_name || ledger.script?.title || ledger.meta.session_id}>
                {ledger.meta.project_name || ledger.script?.title || ledger.meta.session_id}
              </strong>
              <em>{ledger.script ? `${ledger.script.lines} 行 · ${ledger.script.chars} 字` : '剧本尚未解析'}</em>
            </div>
            <div className="ledger-stat is-progress">
              <span className="ledger-stat-label">集进度</span>
              <strong>{doneEpisodes}/{episodeCount || 0}</strong>
              <em>{completionPercent}% 已产出成片</em>
            </div>
            <div className="ledger-stat">
              <span className="ledger-stat-label">分镜与段</span>
              <strong>{storyboardShots} <small>镜</small></strong>
              <em>{ledger.storyboard?.segment_count ?? 0} 个段级契约</em>
            </div>
            <div className="ledger-stat">
              <span className="ledger-stat-label">关键帧 / 片段</span>
              <strong>{totalKeyframes} <small>/</small> {totalClips}</strong>
              <em>全剧渲染产物累计</em>
            </div>
            <div className="ledger-stat">
              <span className="ledger-stat-label">未完成</span>
              <strong>{activeEpisodes}</strong>
              <em>尚未产出成片的集</em>
            </div>
            <div className={`ledger-stat ${errorEpisodes > 0 ? 'is-error' : ''}`}>
              <span className="ledger-stat-label">需处理</span>
              <strong>{errorEpisodes}</strong>
              <em>{errorEpisodes > 0 ? '存在失败或异常集' : '当前无异常集'}</em>
            </div>
            <div className="ledger-stat">
              <span className="ledger-stat-label">画风判定</span>
              <strong title={bible?.style_verdict || '未判定'}>{bible?.style_verdict || '未判定'}</strong>
              <em>production_bible</em>
            </div>
            <div className={`ledger-stat ${render ? `is-${statusTone(render.status ?? 'pending').slice(3)}` : ''}`}>
              <span className="ledger-stat-label">渲染状态</span>
              <strong>{statusLabel(render?.status ?? 'pending')}</strong>
              <em>{render?.retryable ? '可重试' : render ? render.error_type || 'render_status.json' : '尚未开始'}</em>
            </div>
            <div className={`ledger-stat ${budget ? (budget.configured ? '' : 'is-warn') : 'is-muted'}`}>
              <span className="ledger-stat-label">预算消耗</span>
              <strong title={budget ? `LLM tokens ${formatTokens(budget.used?.llm_tokens ?? 0)}` : '尚无记账流水'}>
                {budget ? `${budget.used?.image_calls ?? 0} 图 · ${budget.used?.video_calls ?? 0} 视频` : '—'}
              </strong>
              <em>{budget
                ? `${budget.used?.audio_calls ?? 0} 音色 · ${budget.configured ? '策略已配置' : '未配置策略（实时记账）'}`
                : '尚无预算台账'}</em>
            </div>
          </div>

          {/* 全剧宪法 */}
          {bible && (
            <Section title="全剧一致性宪法" hint="风格 / 影调 / 色彩 / 声音 / 节奏（来自 production_bible）">
              <div className="ledger-bible-grid">
                {bible.style_anchor && <Collapse label="风格锚定词" text={bible.style_anchor} />}
                {bible.global_lighting && <Collapse label="影调与光系" text={bible.global_lighting} />}
                {bible.character_colors && Object.keys(bible.character_colors).length > 0 && (
                  <Collapse label={`角色配色（${Object.keys(bible.character_colors).length} 人）`} text={Object.entries(bible.character_colors).map(([k, v]) => `${k}：${v}`).join('\n')} />
                )}
                {bible.scene_palette && Object.keys(bible.scene_palette).length > 0 && (
                  <Collapse label={`场景配色（${Object.keys(bible.scene_palette).length} 处）`} text={Object.entries(bible.scene_palette).map(([k, v]) => `${k}：${v}`).join('\n')} />
                )}
                {bible.sound_baseline && <Collapse label="声音基线" text={bible.sound_baseline} />}
                {bible.platform_rhythm && <Collapse label="平台节奏" text={bible.platform_rhythm} />}
              </div>
            </Section>
          )}

          {/* 出稿机检门禁：报告写盘后此前没有任何界面能回看，FAIL 项看不见就改不了 */}
          {scriptGate && (
            <Section
              title="出稿机检"
              hint={gateTone === 'fail'
                ? `${scriptGate.fails.length} 项 FAIL · ${scriptGate.warns.length} 项提醒`
                : gateTone === 'legacy'
                  ? `旧版报告 · ${scriptGate.warns.length} 项提醒`
                  : `通过 · ${scriptGate.warns.length} 项提醒`}
            >
              <div className={`ledger-gate-head is-${gateTone}`}>
                <strong>
                  {gateTone === 'fail' ? '❌ 机检未通过' : gateTone === 'legacy' ? '⚠️ 机检结果不完整' : '✅ 机检通过'}
                </strong>
                <span>
                  {gateTone === 'fail'
                    ? 'FAIL 项必须修复后重跑（否则大概率产出无声/违规成片）。'
                    : gateTone === 'legacy'
                      ? '旧版机检报告未记录 FAIL 明细，无法判断具体原因；重跑叙事规划后会得到完整报告。'
                      : '可进入渲染；下列提醒只影响观感与节奏，按需处理。'}
                </span>
              </div>
              {scriptGate.fails.length > 0 && (
                <ul className="ledger-gate-list is-fail">
                  {scriptGate.fails.slice(0, 20).map((item, i) => (
                    <li key={`f-${i}`} title={item}>{item}</li>
                  ))}
                </ul>
              )}
              {scriptGate.fails.length > 20 && (
                <span className="ledger-empty">仅显示前 20 项 FAIL，共 {scriptGate.fails.length} 项</span>
              )}
              {scriptGate.warns.length > 0 && (
                <Collapse
                  label={`提醒 ${scriptGate.warns.length} 项（展开查看）`}
                  text={scriptGate.warns.join('\n')}
                />
              )}
              {scriptGate.stats && (
                <Collapse label="机检统计" text={Object.entries(scriptGate.stats).map(([k, v]) => `${k}：${String(v)}`).join('\n')} />
              )}
            </Section>
          )}

          {/* 创作质量自评：agent 对分镜做多维度「自评」，此前只在后台落盘，没有界面能回看 */}
          <Section title="创作质量自评" wide hint="分镜多维评分 · 达标 / 待修订一目了然">
            <QualityReportPanel session={session} />
          </Section>

          {/* 资产清单 */}
          <Section title="资产清单" wide hint="已生成 / 清单计划（角色数取自角色卡）">
            <div className="ledger-asset-grid">
              <div className="ledger-asset-card">
                <header><strong>角色</strong><span>{assets?.characters?.count ?? 0}</span></header>
                <TagList items={displayTags(assets?.display?.characters, assets?.characters?.names ?? [])} empty="尚未提取角色" />
              </div>
              <div className="ledger-asset-card">
                <header><strong>角色立绘</strong><span>{assets?.portraits?.count ?? 0}{assets?.plan ? ` / ${assets.plan.characters}` : ''}</span></header>
                <TagList
                  items={displayTags(assets?.display?.portraits, Object.entries(assets?.portraits?.by_char ?? {}).map(([name, n]) => `${name} ×${n}`))}
                  empty="尚未生成立绘"
                />
              </div>
              <div className="ledger-asset-card">
                <header><strong>场景</strong><span>{assets?.scenes_props?.scenes ?? 0}{assets?.plan ? ` / ${assets.plan.scenes}` : ''}</span></header>
                <TagList items={displayTags(assets?.display?.scenes, assets?.scenes_props?.scene_names ?? assets?.plan?.scene_names ?? [])} empty="尚未提取场景" />
              </div>
              <div className="ledger-asset-card">
                <header><strong>道具</strong><span>{assets?.scenes_props?.props ?? 0}{assets?.plan ? ` / ${assets.plan.props}` : ''}</span></header>
                <TagList items={displayTags(assets?.display?.props, assets?.plan?.prop_names ?? assets?.scenes_props?.prop_names ?? [])} empty="尚未提取道具" />
              </div>
              <div className="ledger-asset-card">
                <header><strong>音色</strong><span>{assets?.voices?.count ?? 0}{assets?.plan ? ` / ${assets.plan.voices}` : ''}</span></header>
                <TagList items={displayTags(assets?.display?.voices, assets?.plan?.voice_names ?? assets?.voices?.names ?? [])} empty="尚未生成音色" />
              </div>
            </div>
          </Section>

          {/* 集级进度 */}
          <Section title="集级进度" wide hint={`${episodeCount} 集 · ${doneEpisodes} 集成片${assetsPendingEpisodes > 0 ? ` · ${assetsPendingEpisodes} 集缺资产` : ''} · ${errorEpisodes} 集需处理`}>
            {episodeCount === 0 ? (
              <span className="ledger-empty">尚未拆分集目录（script2video/episodes 为空）</span>
            ) : (
              <>
                <div className="ledger-ep-toolbar">
                  <label className="ledger-search-field">
                    <Search size={14} aria-hidden="true" />
                    <input
                      type="search"
                      value={episodeQuery}
                      onChange={(event) => setEpisodeQuery(event.target.value)}
                      placeholder="搜索集号或标题"
                      aria-label="搜索集号或标题"
                    />
                  </label>
                  <div className="ledger-filter-chips" role="group" aria-label="按状态筛选集">
                    {([
                      ['all', '全部', episodeCount],
                      ['active', '未完成', activeEpisodes],
                      ['done', '已完成', doneEpisodes],
                      ['attention', '需处理', errorEpisodes],
                    ] as const).map(([value, label, count]) => (
                      <button
                        key={value}
                        type="button"
                        className={episodeFilter === value ? 'is-active' : ''}
                        aria-pressed={episodeFilter === value}
                        onClick={() => setEpisodeFilter(value)}
                      >
                        {label}<b>{count}</b>
                      </button>
                    ))}
                  </div>
                  <label className="ledger-sort-field">
                    <ArrowUpDown size={14} aria-hidden="true" />
                    <select value={episodeSort} onChange={(event) => setEpisodeSort(event.target.value as EpisodeSort)} aria-label="集进度排序">
                      <option value="episode">按集序</option>
                      <option value="progress">按进度</option>
                      <option value="updated">按更新时间</option>
                    </select>
                  </label>
                </div>

                {visibleEpisodes.length === 0 ? (
                  <div className="ledger-ep-filter-empty">
                    <strong>没有匹配的集</strong>
                    <span>调整搜索词或状态筛选后再看。</span>
                  </div>
                ) : (
                  <div className="ledger-ep-table" role="table" aria-label="集级进度表">
                    <div className="ledger-ep-head" role="row">
                      <span role="columnheader">集</span>
                      <span role="columnheader">标题与进度</span>
                      <span role="columnheader">状态</span>
                      <span role="columnheader">镜头</span>
                      <span role="columnheader">关键帧</span>
                      <span role="columnheader">片段</span>
                      <span role="columnheader">成片</span>
                    </div>
                    {visibleEpisodes.map((ep) => {
                      const progress = episodeProgress(ep);
                      return (
                        <div className={`ledger-ep-row ${ep.final_ready ? 'is-done' : ''}`} key={ep.ep_id} role="row">
                          <span className="ledger-ep-id" role="cell">{ep.ep_id.replace(/^ep/i, '')}</span>
                          <span className="ledger-ep-title-cell" role="cell">
                            <span className="ledger-ep-title" title={ep.title}>{ep.title || '未命名集'}</span>
                            <span className="ledger-ep-sub">
                              {episodeStage(ep)} · 更新 {formatUpdatedAt(ep.updated_at)}
                              {ep.assets_ready === false && !ep.final_ready && (
                                <b className="ledger-ep-assets" title={assetMissingTitle(ep)}>
                                  · 缺资产 {assetMissingCount(ep)} 项
                                </b>
                              )}
                            </span>
                            <span className="ledger-ep-progress" aria-label={`进度 ${progress}%`}>
                              <i style={{width: `${progress}%`}} />
                            </span>
                          </span>
                          <span className={`ledger-status ${statusTone(ep.status)}`} role="cell">{statusLabel(ep.status)}</span>
                          <span
                            className="ledger-ep-shots"
                            role="cell"
                            data-label="镜头"
                            title={episodeShotTitle(ep)}
                          >
                            {episodeShotLabel(ep)}
                          </span>
                          <span className="ledger-ep-keyframes" role="cell" data-label="关键帧">{ep.keyframes}</span>
                          <span className="ledger-ep-clips" role="cell" data-label="片段">{ep.clips}</span>
                          <span
                            className={`ledger-ep-final ${ep.final_ready ? 'ledger-check' : ep.partial_render ? 'is-partial' : ''}`}
                            role="cell"
                            data-label="成片"
                            title={ep.render_gap ?? undefined}
                          >
                            {ep.final_ready ? '✓ 成片' : ep.partial_render ? '⚠ 残缺' : '—'}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            )}
          </Section>

          {/* 渲染状态与断点 */}
          {render && (
            <Section
              title={String(render.status ?? '').startsWith('assets_') ? '资产生成状态' : '渲染状态与断点'}
              hint={String(render.status ?? '').startsWith('assets_') ? 'render_status.json（资产生成阶段，不是渲染）' : 'render_status.json'}
            >
              <div className="ledger-render">
                <div className="ledger-render-meta">
                  <span className={`ledger-status ${statusTone(render.status ?? 'pending')}`}>{statusLabel(render.status ?? 'pending')}</span>
                  {render.error_type && <span className="ledger-tag">{render.error_type}</span>}
                  {render.retryable === true && <span className="ledger-tag is-warn">可重试</span>}
                </div>
                {render.error && <div className="ledger-render-error" title={render.error}>{render.error}</div>}
                <div className="ledger-render-lists">
                  <div>
                    <strong>已就绪（{render.present?.length ?? 0}）</strong>
                    <TagList items={render.present ?? []} empty="无" />
                  </div>
                  <div>
                    <strong>缺失（{render.missing?.length ?? 0}）</strong>
                    <TagList items={(render.missing ?? []).slice(0, 40)} empty="无" />
                    {(render.missing?.length ?? 0) > 40 && (
                      <span className="ledger-empty">仅显示前 40 项，共 {render.missing?.length} 项缺失</span>
                    )}
                  </div>
                </div>
              </div>
            </Section>
          )}

          {!bible && !render && (ledger?.episodes?.length ?? 0) === 0 && !assets?.characters && (
            <div className="ledger-empty-state">
              <strong>数据还不完整</strong>
              <p>该项目的剧本解析尚未完成，继续在对话里推进创作流程后，台账会自动填充。</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
