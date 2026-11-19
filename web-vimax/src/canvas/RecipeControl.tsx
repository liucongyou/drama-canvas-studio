import {Search, Wand2, X} from 'lucide-react';
import {useEffect, useState} from 'react';
import {createPortal} from 'react-dom';

import {agentApi, type AgentRecipe, type AgentRecipeBinding} from '../agent/agentApi';
import {recipeFromDetail} from '../agent/agentRecipes';
import {
  autoFillRecipeSlots,
  RECIPE_CATEGORIES,
  recipeCategoryLabel,
  recipeDisplayDescription,
  recipeSlotHint,
  recipeSlotLabel,
} from './recipePresentation';

type RecipeControlProps = {
  nodeId: string;
  recipe: AgentRecipeBinding | null;
  segmentNo: number;
  prompt: string;
  readOnly?: boolean;
  onChange: (recipe: AgentRecipeBinding | null) => void;
};

export function recipeBindingReady(
  recipe: AgentRecipeBinding | null | undefined,
  slots: Record<string, string>,
): boolean {
  if (!recipe) return false;
  return recipe.missingSlots?.every((key) => Boolean(slots[key]?.trim())) ?? true;
}

export function RecipeControl({
  nodeId,
  recipe,
  segmentNo,
  prompt,
  readOnly = false,
  onChange,
}: RecipeControlProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [results, setResults] = useState<AgentRecipe[]>([]);
  const [selected, setSelected] = useState<AgentRecipe | null>(null);
  const [slots, setSlots] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setError('');
    setResults([]);
    const timer = window.setTimeout(() => {
      void agentApi.recipes({query, category, kind: 'creative', limit: 20})
        .then((data) => {
          if (alive) setResults(data.recipes || []);
        })
        .catch((err) => {
          if (alive) setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, 160);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [open, query, category]);

  useEffect(() => {
    if (!recipe) {
      setSelected(null);
      setSlots({});
      return;
    }
    setSlots(recipe.slots || {});
    void agentApi.recipe(recipe.id, recipe.version)
      .then((data) => {
        setSelected(recipeFromDetail(data.recipe, recipe));
      })
      .catch(() => {
        setSelected({
          id: recipe.id,
          version: recipe.version,
          kind: 'creative',
          category: '',
          title: recipe.id,
          description: '',
          models: [],
          requiredSlots: recipe.missingSlots || [],
          contentSha256: recipe.sha256 || '',
        });
      });
  }, [recipe?.id, recipe?.version]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open]);

  useEffect(() => {
    const onOpenRecipe = (event: Event) => {
      const detail = (event as CustomEvent<{nodeId?: string}>).detail || {};
      if (detail.nodeId !== nodeId) return;
      setQuery('');
      setCategory('');
      setError('');
      setOpen(true);
    };
    window.addEventListener('canvas-recipe-open', onOpenRecipe);
    return () => window.removeEventListener('canvas-recipe-open', onOpenRecipe);
  }, [nodeId]);

  const openPicker = () => {
    setOpen((current) => !current);
    setQuery('');
    setCategory('');
    setError('');
  };

  const choose = (item: AgentRecipe) => {
    setSelected(item);
    setSlots(autoFillRecipeSlots(item.requiredSlots, prompt));
    setError('');
    void agentApi.recipe(item.id, item.version)
      .then((data) => {
        const detail = recipeFromDetail(data.recipe, {
          id: item.id,
          version: item.version,
          sha256: item.contentSha256,
        });
        if (detail) {
          setSelected((current) => current?.id === item.id ? detail : current);
        }
      })
      .catch(() => {
        // The compact search result is sufficient when the detail request fails.
      });
  };

  const apply = () => {
    if (!selected) return;
    const missing = selected.requiredSlots.filter((key) => !slots[key]?.trim());
    if (missing.length) {
      setError('请先填写：' + missing.map(recipeSlotLabel).join('、'));
      return;
    }
    onChange({
      id: selected.id,
      version: selected.version,
      sha256: selected.contentSha256,
      role: 'primary',
      segmentNo,
      slots: Object.fromEntries(
        Object.entries(slots).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value),
      ),
      missingSlots: [],
    });
    setOpen(false);
  };
  const displayName = selected?.title || recipe?.id || '';

  if (readOnly) {
    return (
      <div className="canvas-recipe-readonly">
        <Wand2 size={12} />
        <span>{recipe ? displayName : '未绑定镜头配方'}</span>
        {recipe?.version ? <small>v{recipe.version}</small> : null}
      </div>
    );
  }

  if (!recipe && !open) return null;

  return (
    <div className="canvas-recipe-control">
      {recipe && (
        <button type="button" className="canvas-recipe-trigger has-recipe" onClick={openPicker}>
          <Wand2 size={12} />
          <span>{displayName}</span>
          {recipe.version ? <small>v{recipe.version}</small> : null}
        </button>
      )}
      {recipe && (
        <button type="button" className="canvas-recipe-remove" title="移除镜头配方" onClick={() => onChange(null)}>
          <X size={11} />
        </button>
      )}
      {open && createPortal((
        <div
          className="canvas-recipe-overlay"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <div className="canvas-recipe-popover" role="dialog" aria-modal="true" aria-label="选择镜头配方">
            <div className="canvas-recipe-search">
              <Search size={13} />
              <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索动作、镜头、群像或综合机制" />
              <button type="button" className="canvas-recipe-close" title="关闭" aria-label="关闭" onClick={() => setOpen(false)}>
                <X size={13} />
              </button>
            </div>
            <div className="canvas-recipe-note">配方只约束镜头机制；正文 @ 仍是角色和图片引用的唯一权威。</div>
            <div className="canvas-recipe-guide">
              <strong>怎么选：</strong>
              简单对白、单人静态画面可以不选。动作、追逐、群像、产品、转场或复杂运镜，先点下面的画面类型。
            </div>
            {!selected ? (
              <>
                <div className="canvas-recipe-categories" aria-label="镜头配方分类">
                  <button type="button" className={!category ? 'is-active' : ''} onClick={() => setCategory('')}>全部</button>
                  {RECIPE_CATEGORIES.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      className={category === item.value ? 'is-active' : ''}
                      onClick={() => setCategory(item.value)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
                <div className="canvas-recipe-results">
                  {results.map((item) => (
                    <button key={item.id} type="button" onClick={() => choose(item)}>
                      <strong>{item.title}</strong>
                      <span>{recipeCategoryLabel(item.category)} · {item.requiredSlots.length} 项参数</span>
                      <small>{recipeDisplayDescription(item)}</small>
                    </button>
                  ))}
                  {loading && <p>正在检索配方…</p>}
                  {!loading && results.length === 0 && <p>没有匹配的镜头配方</p>}
                </div>
              </>
            ) : (
              <div className="canvas-recipe-form">
                <div className="canvas-recipe-head">
                  <div>
                    <strong>{selected.title}</strong>
                    <span>{recipeCategoryLabel(selected.category)} · v{selected.version}</span>
                  </div>
                  <div className="canvas-recipe-head-actions">
                    <button type="button" onClick={() => setSlots(autoFillRecipeSlots(selected.requiredSlots, prompt))}>自动填写</button>
                    <button type="button" onClick={() => setSelected(null)}>返回</button>
                  </div>
                </div>
                <p>{recipeDisplayDescription(selected)}</p>
                <div className="canvas-recipe-fill-help">
                  已按当前提示词填入可编辑草稿。只需核对人物、场景和动作结果，不需要填写技术术语。
                </div>
                <div className="canvas-recipe-slots">
                  {selected.requiredSlots.map((key) => (
                    <label key={key}>
                      <span>{recipeSlotLabel(key)}</span>
                      <input value={slots[key] || ''} onChange={(event) => setSlots((current) => ({...current, [key]: event.target.value}))} placeholder="填写本段实际内容" />
                      <small>{recipeSlotHint(key)}</small>
                    </label>
                  ))}
                  {selected.requiredSlots.length === 0 && <p>这个配方没有额外参数。</p>}
                </div>
                {error && <div className="canvas-recipe-error">{error}</div>}
                <div className="canvas-recipe-actions">
                  <button type="button" className="is-primary" disabled={!recipeBindingReady({...recipe, id: selected.id, version: selected.version, sha256: selected.contentSha256, role: 'primary', segmentNo, slots, missingSlots: selected.requiredSlots}, slots)} onClick={apply}>应用配方</button>
                  <button type="button" onClick={() => setOpen(false)}>取消</button>
                </div>
              </div>
            )}
            {error && !selected && <div className="canvas-recipe-error">{error}</div>}
          </div>
        </div>
      ), document.body)}
    </div>
  );
}
