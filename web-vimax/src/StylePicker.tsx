import {useEffect, useMemo, useState} from 'react';
import {dialog} from './GDialog';
import {
  getStyleLibrary,
  getStylePreference,
  saveStylePreference,
  type StyleCategory,
  type StyleItem,
} from './api';
import {Lock, X} from 'lucide-react';
import {notify} from './toast';

/**
 * 风格选择器（参照即梦生成页交互）：
 * 顶部 9 大分类 Tab + 子风格网格 + 底部「清除当前风格」。
 * 选中子风格 → 保存为全局默认风格（.vimax/style_preference.json），
 * 新建项目时自动注入（agent 不再提示重复选风格）；清除 → 回到剧本自动判定。
 */

// 每类子风格的展示色（用于网格卡片的视觉区分，与生成结果无关）
const CATEGORY_ACCENTS: Record<string, {bg: string; fg: string}> = {
  realistic: {bg: 'linear-gradient(135deg,#f6d9c2,#e8b48f)', fg: '#7a4a2b'},
  '3d_anime': {bg: 'linear-gradient(135deg,#cfe8ff,#9cc9f0)', fg: '#1e5a8a'},
  anime2d: {bg: 'linear-gradient(135deg,#fdd7e6,#f7b3cd)', fg: '#8a2b5a'},
  photography: {bg: 'linear-gradient(135deg,#dcdcdc,#b9b9b9)', fg: '#4a4a4a'},
  illustration: {bg: 'linear-gradient(135deg,#ffe9c7,#ffd18e)', fg: '#8a5a1e'},
  punk_art: {bg: 'linear-gradient(135deg,#d9d2ff,#b0a3f0)', fg: '#4a3a8a'},
  traditional_art: {bg: 'linear-gradient(135deg,#e7dcc8,#cfc0a6)', fg: '#6a5530'},
  craft_art: {bg: 'linear-gradient(135deg,#d9f0dd,#a8d8b5)', fg: '#2a6a3a'},
  custom: {bg: 'linear-gradient(135deg,#e8e8f5,#c8c8e8)', fg: '#4a4a7a'},
};

export default function StylePicker() {
  const [categories, setCategories] = useState<StyleCategory[]>([]);
  const [activeCategory, setActiveCategory] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [selectedName, setSelectedName] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [lib, pref] = await Promise.all([getStyleLibrary(), getStylePreference()]);
        if (cancelled) return;
        if (lib.categories?.length) setCategories(lib.categories);
        if (pref.style) {
          setSelectedId(pref.style);
          // 反向解析选中项名称（可能存的是 id 或名称）
          for (const cat of lib.categories ?? []) {
            for (const child of cat.children) {
              if (child.id === pref.style || child.name === pref.style) {
                setSelectedName(child.name);
                setActiveCategory(cat.id);
              }
            }
          }
        } else {
          setActiveCategory(lib.categories?.[0]?.id ?? '');
        }
        setError('');
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const activeChildren = useMemo(
    () => categories.find((cat) => cat.id === activeCategory)?.children ?? [],
    [categories, activeCategory],
  );

  function pick(item: StyleItem, catId: string) {
    if (saving) return;
    setSaving(true);
    setError('');
    setSelectedId(item.id);
    setSelectedName(item.name);
    setActiveCategory(catId);
    void saveStylePreference(item.id)
      .then(() => {
        setSaving(false);
        notify(`已设为默认风格：${item.name}`);
      })
      .catch((err) => {
        setSaving(false);
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        notify(`风格设置失败：${message}`, 'error');
      });
  }

  async function clearStyle() {
    if (saving) return;
    // 清除默认风格会影响之后所有新建项目，必须先确认；成功/失败也都要有反馈
    const ok = await dialog.confirm({
      title: '清除全局默认风格？',
      message: '清除后，**之后新建的项目**会回到「自动判定」，已有项目不受影响。',
      confirmText: '清除默认风格',
      tone: 'danger',
    });
    if (!ok) return;
    setSaving(true);
    setError('');
    setSelectedId('');
    setSelectedName('');
    void saveStylePreference('')
      .then(() => {
        setSaving(false);
        notify('已清除全局默认风格');
      })
      .catch((err) => {
        setSaving(false);
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        notify(`清除风格失败：${message}`, 'error');
      });
  }

  if (loading) return <div className="style-picker is-loading view-loading">正在加载风格库…</div>;

  return (
    <div className="style-picker">
      <div className="style-picker-head">
        <span className="style-picker-current">
          {selectedName
            ? <>当前风格：<strong>{selectedName}</strong></>
            : <><strong>自动判定</strong>（按剧本内容推荐画风）</>}
        </span>
        {error && <span className="style-picker-error">{error}</span>}
      </div>

      <div className="style-category-tabs" role="tablist" aria-label="风格分类">
        {categories.map((cat) => (
          <button
            key={cat.id}
            type="button"
            role="tab"
            aria-selected={activeCategory === cat.id}
            className={activeCategory === cat.id ? 'is-active' : ''}
            onClick={() => setActiveCategory(cat.id)}
            title={cat.desc}
          >
            {cat.name}
          </button>
        ))}
      </div>

      <div className="style-grid" role="tabpanel">
        {activeChildren.map((item) => {
          const accent = CATEGORY_ACCENTS[activeCategory] ?? {bg: '#e8e8f0', fg: '#555'};
          const isSelected = selectedId === item.id;
          return (
            <button
              key={item.id}
              type="button"
              className={`style-card${isSelected ? ' is-selected' : ''}`}
              onClick={() => pick(item, activeCategory)}
              aria-pressed={isSelected}
            >
              <i className="style-card-swatch" style={{background: accent.bg, color: accent.fg}} aria-hidden="true" />
              <span className="style-card-name">{item.name}</span>
              {isSelected && <Lock className="style-card-lock" size={14} aria-label="已锁定" />}
            </button>
          );
        })}
      </div>

      <div className="style-picker-foot">
        <button type="button" className="style-clear" onClick={clearStyle} disabled={saving || !selectedId}>
          <X size={14} />清除当前风格
        </button>
        <span className="style-picker-hint">清除后新项目将按剧本自动判定画风；选定后新建项目自动采用该风格，无需重复选择。</span>
      </div>
    </div>
  );
}
