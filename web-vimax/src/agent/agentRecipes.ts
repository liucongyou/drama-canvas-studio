import type {AgentRecipe, AgentRecipeBinding} from './agentApi';

export function recipeToBindings(recipe: AgentRecipe | null): AgentRecipeBinding[] {
  if (!recipe) return [];
  return [{
    id: recipe.id,
    version: recipe.version,
    sha256: recipe.contentSha256,
    role: 'primary',
    segmentNo: 0,
    slots: {},
  }];
}

export function recipeFromDetail(
  detail: Record<string, unknown>,
  fallback?: Partial<AgentRecipeBinding>,
): AgentRecipe | null {
  const id = String(detail.id || fallback?.id || '');
  if (!id) return null;
  const slots = Array.isArray(detail.slots) ? detail.slots : [];
  return {
    id,
    version: String(detail.version || fallback?.version || ''),
    kind: String(detail.kind || 'creative'),
    category: String(detail.category || ''),
    title: String(detail.title || id),
    description: String(detail.description || ''),
    summary: String(detail.summary || ''),
    models: Array.isArray(detail.models) ? detail.models.map(String) : [],
    requiredSlots: slots
      .map((slot) => String((slot as Record<string, unknown>).key || ''))
      .filter(Boolean),
    contentSha256: String(detail.contentSha256 || fallback?.sha256 || ''),
  };
}
