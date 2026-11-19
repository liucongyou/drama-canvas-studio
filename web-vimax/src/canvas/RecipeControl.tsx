import {Search, Wand2, X} from 'lucide-react';
import {useEffect, useState} from 'react';

import {agentApi, type AgentRecipe, type AgentRecipeBinding} from '../agent/agentApi';
import {recipeFromDetail} from '../agent/agentRecipes';

type RecipeControlProps = {
  recipe: AgentRecipeBinding | null;
  segmentNo: number;
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
  recipe,
  segmentNo,
  readOnly = false,
  onChange,
}: RecipeControlProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<AgentRecipe[]>([]);
  const [selected, setSelected] = useState<AgentRecipe | null>(null);
  const [slots, setSlots] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError('');
      void agentApi.recipes({query, kind: 'creative', limit: 20})
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
  }, [open, query]);

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

  const openPicker = () => {
    setOpen((current) => !current);
    setQuery('');
    setError('');
  };

  const choose = (item: AgentRecipe) => {
    setSelected(item);
    setSlots({});
    setError('');
  };

  const apply = () => {
    if (!selected) return;
    const missing = selected.requiredSlots.filter((key) => !slots[key]?.trim());
    if (missing.length) {
      setError('请先填写：' + missing.join('、'));
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

  return (
    <div className="canvas-recipe-control">
      <button type="button" className={'canvas-recipe-trigger' + (recipe ? ' has-recipe' : '')} onClick={openPicker}>
        <Wand2 size={12} />
        <span>{recipe ? displayName : '添加镜头配方'}</span>
        {recipe?.version ? <small>v{recipe.version}</small> : null}
      </button>
      {recipe && (
        <button type="button" className="canvas-recipe-remove" title="移除镜头配方" onClick={() => onChange(null)}>
          <X size={11} />
        </button>
      )}
      {open && (
        <div className="canvas-recipe-popover">
          <div className="canvas-recipe-search">
            <Search size={13} />
            <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索动作、镜头、群像或综合机制" />
          </div>
          <div className="canvas-recipe-note">配方只约束镜头机制；正文 @ 仍是角色和图片引用的唯一权威。</div>
          {!selected ? (
            <div className="canvas-recipe-results">
              {results.map((item) => (
                <button key={item.id} type="button" onClick={() => choose(item)}>
                  <strong>{item.title}</strong>
                  <span>{item.category} · {item.requiredSlots.length} 个槽位</span>
                  <small>{item.description}</small>
                </button>
              ))}
              {loading && <p>正在检索配方…</p>}
              {!loading && results.length === 0 && <p>没有匹配的镜头配方</p>}
            </div>
          ) : (
            <div className="canvas-recipe-form">
              <div className="canvas-recipe-head">
                <div>
                  <strong>{selected.title}</strong>
                  <span>{selected.category} · v{selected.version}</span>
                </div>
                <button type="button" onClick={() => setSelected(null)}>返回</button>
              </div>
              <p>{selected.description}</p>
              <div className="canvas-recipe-slots">
                {selected.requiredSlots.map((key) => (
                  <label key={key}>
                    <span>{key}</span>
                    <input value={slots[key] || ''} onChange={(event) => setSlots((current) => ({...current, [key]: event.target.value}))} placeholder="填写本段实际内容" />
                  </label>
                ))}
                {selected.requiredSlots.length === 0 && <p>这个配方没有额外变量槽。</p>}
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
      )}
    </div>
  );
}
