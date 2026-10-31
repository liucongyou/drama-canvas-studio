export type ModelPickerProvider = {
  id: string;
  name: string;
  icon?: string;
  enabled?: boolean;
};

export type ModelPickerModel = {
  providerId: string;
  label?: string;
  id?: string;
  remoteModel?: string;
  description?: string;
  badge?: string;
};

export type ModelPickerFavorite = {
  providerId?: string;
  label?: string;
  id?: string;
  remoteModel?: string;
  description?: string;
  badge?: string;
};

export type ProviderPickerGroup = {
  id: string;
  name: string;
  icon?: string;
  enabled?: boolean;
};

export type ModelPickerGroup<T extends ModelPickerModel> = {
  id: string;
  name: string;
  icon?: string;
  models: T[];
};

const UNKNOWN_PROVIDER_ID = '__unassigned__';

/**
 * Keep model selection organized by provider for every media capability.
 * Provider order follows the configured catalog so the selector stays stable
 * between renders; models omitted from a provider entry are retained in a
 * trailing fallback group instead of disappearing.
 */
export function groupModelsByProvider<T extends ModelPickerModel>(
  models: T[],
  providers: ModelPickerProvider[] | null | undefined,
  query = '',
): Array<ModelPickerGroup<T>> {
  const needle = query.trim().toLowerCase();
  const providerById = new Map(
    (providers ?? []).map((provider, index) => [
      provider.id || UNKNOWN_PROVIDER_ID,
      {provider, index},
    ]),
  );
  const groups = new Map<string, ModelPickerGroup<T>>();
  for (const model of models) {
    if (
      needle
      && ![
        model.label,
        model.id,
        model.remoteModel,
        model.description,
        model.badge,
        model.providerId,
        providerById.get(model.providerId)?.provider.name,
      ]
        .some((value) => String(value || '').toLowerCase().includes(needle))
    ) {
      continue;
    }
    const providerId = String(model.providerId || '').trim() || UNKNOWN_PROVIDER_ID;
    const provider = providerById.get(providerId)?.provider;
    const current = groups.get(providerId) ?? {
      id: providerId,
      name: provider?.name || (providerId === UNKNOWN_PROVIDER_ID ? '未分配供应商' : providerId),
      icon: String(provider?.icon || '').trim(),
      models: [],
    };
    current.models.push(model);
    groups.set(providerId, current);
  }
  return [...groups.values()].sort((left, right) => {
    const leftIndex = providerById.get(left.id)?.index ?? Number.MAX_SAFE_INTEGER;
    const rightIndex = providerById.get(right.id)?.index ?? Number.MAX_SAFE_INTEGER;
    if (leftIndex !== rightIndex) return leftIndex - rightIndex;
    return left.name.localeCompare(right.name, undefined, {numeric: true});
  });
}

export function activeProviderGroup<T extends ModelPickerModel>(
  groups: Array<ModelPickerGroup<T>>,
  preferredProviderId: string,
  selectedModelId: string,
): ModelPickerGroup<T> | null {
  if (groups.length === 0) return null;
  return groups.find((group) => group.id === preferredProviderId)
    ?? groups.find((group) => (
      selectedModelId
      && group.models.some((model) => model.id === selectedModelId)
    ))
    ?? groups[0];
}

export function favoriteModelsForProvider<T extends ModelPickerModel>(
  models: T[],
  favoriteIds: string[],
): T[] {
  const favorite = new Set(favoriteIds);
  return models.filter((model) => favorite.has(model.id || ''));
}

export function pickerProviderGroups<T extends ModelPickerModel>(
  groups: Array<ModelPickerGroup<T>>,
  favorites: ModelPickerFavorite[],
): ProviderPickerGroup[] {
  const result: ProviderPickerGroup[] = [];
  if (favorites.length > 0) {
    result.push({id: '__favorites__', name: '收藏'});
  }
  result.push(...groups.map((group) => ({
    id: group.id,
    name: group.name,
    icon: group.icon || '',
    enabled: group.models.some((model) => model.providerId === group.id),
  })));
  return result;
}
