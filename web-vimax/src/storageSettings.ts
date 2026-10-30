import type {StorageSpec} from './api';

export type StorageDraft = {
  enabled: boolean;
  provider: 'tos' | 'aliyun_oss' | 'qiniu';
  bucket: string;
  region: string;
  endpoint: string;
  internal_endpoint: string;
  public_domain: string;
  prefix: string;
  url_ttl_seconds: string;
  auto_persist_images: boolean;
  auto_persist_videos: boolean;
  auto_persist_audio: boolean;
  require_for_ark_portrait: boolean;
  access_key: string;
  secret_key: string;
};

export function mergeStorageProfile(
  current: StorageDraft,
  storage: StorageSpec,
  provider: StorageDraft['provider'],
): StorageDraft {
  const profile = storage.profiles?.[provider];
  if (!profile) return current;
  return {
    ...current,
    bucket: profile.bucket || current.bucket,
    region: profile.region || current.region,
    endpoint: profile.endpoint || current.endpoint,
    internal_endpoint: profile.internal_endpoint || current.internal_endpoint,
    public_domain: profile.public_domain || current.public_domain,
    prefix: profile.prefix || current.prefix,
    url_ttl_seconds: String(profile.url_ttl_seconds || current.url_ttl_seconds),
  };
}

export function mergeSavedStorageState(
  current: StorageDraft,
  response: StorageSpec,
  savedProvider: StorageDraft['provider'],
): StorageDraft {
  const merged = mergeStorageProfile(current, response, savedProvider);
  return {
    ...merged,
    enabled: response.enabled,
    provider: savedProvider,
    access_key: '',
    secret_key: '',
  };
}

export function draftForProvider(
  current: StorageDraft,
  storage: StorageSpec | null,
  provider: StorageDraft['provider'],
): StorageDraft {
  const profile = storage?.profiles?.[provider];
  return {
    ...current,
    enabled: current.enabled,
    provider,
    bucket: profile?.bucket || '',
    region: profile?.region || 'cn-beijing',
    endpoint: profile?.endpoint || (
      provider === 'qiniu'
        ? ''
        : provider === 'aliyun_oss'
          ? 'oss-cn-beijing.aliyuncs.com'
          : 'tos-cn-beijing.volces.com'
    ),
    internal_endpoint: profile?.internal_endpoint || 'tos-cn-beijing.ivolces.com',
    public_domain: profile?.public_domain || '',
    prefix: profile?.prefix || 'drama-canvas/',
    url_ttl_seconds: String(profile?.url_ttl_seconds || 3600),
    access_key: '',
    secret_key: '',
  };
}
