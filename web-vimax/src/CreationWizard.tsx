import {useEffect, useMemo, useState} from 'react';
import {Clapperboard, Sparkles, Wand2, X} from 'lucide-react';
import {composeKickoff, getCreationPresets, type CreationPreset} from './api';
import {notify} from './toast';

/**
 * 一键智能创作向导（傻瓜式出电影级短剧）。
 *
 * 用户只做三件事：输入一句创意 → 选类型 → 选模式，点「开始创作」。
 * 后台按专业配方库（utils/creation_presets）组装成一段完整的电影级创作指令，
 * 由 agent 端到端执行。协作/自主两种模式都走同一入口，区别只在人工闸门密度。
 */

const STYLE_QUICK = ['3D古风玄幻', '国漫玄幻', '都市写实', '电影质感写实', '悬疑冷调', '甜宠明亮'];
const EPISODE_OPTIONS = [1, 3, 5];
const CINEMATIC_BULLETS = [
  '前 3 秒强钩子 · 每 15 秒一个爆点 · 结尾留钩',
  '9:16 竖屏电影感 · 显式光影/色彩/镜头语言',
  '资产卡逐字复用，角色/场景跨镜一致',
  '分镜自评 ≥ 70 才进渲染，不达标自动修订',
];

export default function CreationWizard({
  open,
  onClose,
  onStart,
}: {
  open: boolean;
  onClose: () => void;
  onStart: (directive: string, mode: 'autonomous' | 'collaborative') => void | Promise<void>;
}) {
  const [presets, setPresets] = useState<CreationPreset[]>([]);
  const [idea, setIdea] = useState('');
  const [genre, setGenre] = useState('');
  const [style, setStyle] = useState('');
  const [episodes, setEpisodes] = useState(1);
  const [mode, setMode] = useState<'autonomous' | 'collaborative'>('autonomous');
  const [starting, setStarting] = useState(false);
  const [loadingPresets, setLoadingPresets] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoadingPresets(true);
    getCreationPresets()
      .then((res) => { if (alive) setPresets(res.presets || []); })
      .catch(() => { if (alive) setPresets([]); })
      .finally(() => { if (alive) setLoadingPresets(false); });
    return () => { alive = false; };
  }, [open]);

  const selected = useMemo(() => presets.find((p) => p.id === genre) || null, [presets, genre]);
  const canStart = idea.trim().length > 0 && !starting;

  async function start() {
    if (!canStart) return;
    setStarting(true);
    try {
      const res = await composeKickoff({idea: idea.trim(), genre, style, mode, episodes});
      await onStart(res.directive, mode);
      onClose();
    } catch (error) {
      notify(`创作启动失败：${error instanceof Error ? error.message : String(error)}`, 'error');
    } finally {
      setStarting(false);
    }
  }

  if (!open) return null;

  return (
    <div className="wizard-overlay" role="dialog" aria-modal="true" aria-label="一键智能创作">
      <div className="wizard-panel">
        <header className="wizard-head">
          <div className="wizard-title">
            <Wand2 size={20} />
            <div>
              <strong>一键创作专业短剧</strong>
              <small>给一句创意，剩下的交给电影级标准自动完成</small>
            </div>
          </div>
          <button type="button" className="wizard-close" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </header>

        <div className="wizard-body">
          <section className="wizard-step">
            <label className="wizard-label">
              <span className="wizard-step-num">1</span>你的创意 / 主题
            </label>
            <textarea
              className="wizard-idea"
              value={idea}
              onChange={(event) => setIdea(event.target.value)}
              placeholder="例如：外卖员其实是隐藏身份的龙王，送餐时撞见前女友被欺负…"
              rows={3}
              autoFocus
            />
          </section>

          <section className="wizard-step">
            <label className="wizard-label">
              <span className="wizard-step-num">2</span>选择类型配方
              <em>（决定钩子/爆点/情感/桥段的专业结构）</em>
            </label>
            {loadingPresets ? (
              <p className="wizard-hint">正在加载专业配方…</p>
            ) : (
              <div className="wizard-genres">
                <button
                  type="button"
                  className={`wizard-genre ${genre === '' ? 'is-active' : ''}`}
                  onClick={() => setGenre('')}
                >
                  <span className="wizard-genre-emoji">✨</span>
                  <span className="wizard-genre-name">智能判定</span>
                  <span className="wizard-genre-tag">按创意自动选最佳类型</span>
                </button>
                {presets.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className={`wizard-genre ${genre === p.id ? 'is-active' : ''}`}
                    onClick={() => setGenre(p.id)}
                    title={p.hook_preview}
                  >
                    <span className="wizard-genre-emoji">{p.emoji}</span>
                    <span className="wizard-genre-name">{p.name}</span>
                    <span className="wizard-genre-tag">{p.tagline}</span>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="wizard-step">
            <label className="wizard-label">
              <span className="wizard-step-num">3</span>风格与集数<em>（可选，留空按剧本自动判定）</em>
            </label>
            <div className="wizard-styles">
              <button type="button" className={`wizard-chip ${style === '' ? 'is-active' : ''}`} onClick={() => setStyle('')}>自动判定</button>
              {STYLE_QUICK.map((s) => (
                <button key={s} type="button" className={`wizard-chip ${style === s ? 'is-active' : ''}`} onClick={() => setStyle(s)}>{s}</button>
              ))}
            </div>
            <div className="wizard-episodes">
              <span className="wizard-episodes-label">集数</span>
              {EPISODE_OPTIONS.map((n) => (
                <button key={n} type="button" className={`wizard-chip ${episodes === n ? 'is-active' : ''}`} onClick={() => setEpisodes(n)}>{n} 集</button>
              ))}
            </div>
          </section>

          <section className="wizard-step">
            <label className="wizard-label"><span className="wizard-step-num">4</span>制作模式</label>
            <div className="wizard-modes">
              <button
                type="button"
                className={`wizard-mode ${mode === 'autonomous' ? 'is-active' : ''}`}
                onClick={() => setMode('autonomous')}
              >
                <strong><Sparkles size={15} /> 自主模式</strong>
                <span>端到端自动推进到成片，仅在资产确认/成片验收暂停。最适合"只要结果"。</span>
              </button>
              <button
                type="button"
                className={`wizard-mode ${mode === 'collaborative' ? 'is-active' : ''}`}
                onClick={() => setMode('collaborative')}
              >
                <strong><Clapperboard size={15} /> 协作模式</strong>
                <span>每个关键节点（大纲/资产/分镜）先给你确认，人机结合精雕细琢。</span>
              </button>
            </div>
          </section>

          <section className="wizard-preview">
            <div className="wizard-preview-head">将自动套用的电影级标准</div>
            <ul>
              {selected && <li className="is-genre"><strong>{selected.emoji} {selected.name}</strong>：{selected.hook_preview}</li>}
              {CINEMATIC_BULLETS.map((b) => <li key={b}>{b}</li>)}
            </ul>
          </section>
        </div>

        <footer className="wizard-foot">
          <span className="wizard-summary">
            {selected ? selected.name : '智能判定'} · {episodes} 集 · {style || '自动风格'} · {mode === 'autonomous' ? '自主端到端' : '协作确认'}
          </span>
          <div className="wizard-actions">
            <button type="button" className="wizard-cancel" onClick={onClose} disabled={starting}>取消</button>
            <button type="button" className="wizard-start" onClick={() => void start()} disabled={!canStart}>
              {starting ? '正在启动…' : '开始创作'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
