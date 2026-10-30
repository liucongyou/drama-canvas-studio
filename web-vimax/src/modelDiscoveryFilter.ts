export type DiscoveryFilterModel = {
  remoteModel: string;
  capability: string;
};

export function filterDiscoveredModels<T extends DiscoveryFilterModel>(
  models: T[],
  capability: string,
  resolutions: Record<string, string>,
  query: string,
): T[] {
  const needle = query.trim().toLowerCase();
  return models.filter((model) => {
    const resolved = model.capability === 'unknown'
      ? resolutions[model.remoteModel] || ''
      : model.capability;
    if (capability === '__unknown__' && model.capability !== 'unknown') return false;
    if (capability && capability !== '__unknown__' && resolved !== capability) return false;
    if (!needle) return true;
    return [model.remoteModel, resolved]
      .some((value) => String(value).toLowerCase().includes(needle));
  });
}
