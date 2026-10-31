import {describe, expect, it} from 'vitest';
import {
  draftForProvider,
  draftFromStorage,
  mergeSavedStorageState,
  mergeStorageProfile,
  storedProviderState,
  type StorageDraft,
} from './storageSettings';
import type {StorageSpec} from './api';


const base: StorageDraft = {
  enabled: true,
  provider: 'tos',
  bucket: 'tos-bucket',
  region: 'cn-beijing',
  endpoint: 'tos-cn-beijing.volces.com',
  internal_endpoint: 'tos-cn-beijing.ivolces.com',
  public_domain: '',
  prefix: 'drama-canvas/',
  url_ttl_seconds: '3600',
  auto_persist_images: true,
  auto_persist_videos: true,
  auto_persist_audio: true,
  require_for_ark_portrait: true,
  access_key: '',
  secret_key: '',
};

const storage = {
  enabled: true,
  provider: 'tos',
  bucket: 'tos-bucket',
  region: 'cn-beijing',
  endpoint: 'tos-cn-beijing.volces.com',
  internal_endpoint: 'tos-cn-beijing.ivolces.com',
  public_domain: '',
  prefix: 'drama-canvas/',
  url_ttl_seconds: 3600,
  auto_persist_images: true,
  auto_persist_videos: true,
  auto_persist_audio: true,
  require_for_ark_portrait: true,
  has_access_key: true,
  has_secret_key: true,
  ready: true,
  tos_ready: true,
  oss_ready: false,
  qiniu_ready: false,
  profiles: {},
} satisfies StorageSpec;

describe('storage provider form isolation', () => {
  it('clears Tos connection fields when selecting unconfigured Qiniu', () => {
    const next = draftForProvider(base, storage, 'qiniu');
    expect(next.provider).toBe('qiniu');
    expect(next.enabled).toBe(true);
    expect(next.bucket).toBe('');
    expect(next.endpoint).toBe('');
    expect(next.public_domain).toBe('');
    expect(next.access_key).toBe('');
    expect(next.secret_key).toBe('');
    expect(next.region).toBe('cn-beijing');
  });

  it('uses the matching provider profile instead of the active Tos values', () => {
    const next = draftForProvider(
      base,
      {
        ...storage,
        profiles: {
          qiniu: {
            bucket: 'qiniu-bucket',
            region: 'cn-beijing',
            endpoint: '',
            internal_endpoint: '',
            public_domain: 'https://cdn.qiniu.example',
            prefix: 'qiniu/',
            url_ttl_seconds: 3600,
            has_access_key: true,
            has_secret_key: true,
          },
        },
      },
      'qiniu',
    );
    expect(next.bucket).toBe('qiniu-bucket');
    expect(next.public_domain).toBe('https://cdn.qiniu.example');
    expect(next.prefix).toBe('qiniu/');
    expect(storedProviderState({
      ...storage,
      provider: 'aliyun_oss',
      profiles: {
        qiniu: {
          bucket: 'qiniu-bucket',
          region: 'cn-beijing',
          endpoint: '',
          internal_endpoint: '',
          public_domain: 'https://cdn.qiniu.example',
          prefix: 'qiniu/',
          url_ttl_seconds: 3600,
          has_access_key: true,
          has_secret_key: true,
        },
      },
    }, 'qiniu')).toBe('saved');
  });

  it('keeps the selected OSS form when testing refreshes an active Qiniu state', () => {
    const current = {...base, provider: 'aliyun_oss' as const, bucket: 'oss-bucket'};
    const fromActiveQiniu = {
      ...storage,
      provider: 'qiniu',
      profiles: {
        aliyun_oss: {
          bucket: 'oss-bucket',
          region: 'cn-beijing',
          endpoint: 'oss-cn-beijing.aliyuncs.com',
          internal_endpoint: '',
          public_domain: '',
          prefix: 'drama-canvas/',
          url_ttl_seconds: 3600,
          has_access_key: true,
          has_secret_key: true,
        },
        qiniu: {
          bucket: 'qiniu-bucket',
          region: 'cn-beijing',
          endpoint: '',
          internal_endpoint: '',
          public_domain: 'https://cdn.qiniu.example',
          prefix: 'qiniu/',
          url_ttl_seconds: 3600,
          has_access_key: true,
          has_secret_key: true,
        },
      },
    };
    const next = mergeStorageProfile(current, fromActiveQiniu, 'aliyun_oss');
    expect(next.provider).toBe('aliyun_oss');
    expect(next.bucket).toBe('oss-bucket');
  });

  it('keeps the OSS selection after saving while TOS remains active', () => {
    const current = {...base, provider: 'aliyun_oss' as const, bucket: 'oss-bucket'};
    const next = mergeSavedStorageState(current, {
      ...storage,
      provider: 'tos',
      active_provider: 'tos',
      enabled: true,
      profiles: {
        aliyun_oss: {
          bucket: 'oss-bucket',
          region: 'cn-beijing',
          endpoint: 'oss-cn-beijing.aliyuncs.com',
          internal_endpoint: '',
          public_domain: '',
          prefix: 'drama-canvas/',
          url_ttl_seconds: 3600,
          has_access_key: true,
          has_secret_key: true,
        },
      },
    }, 'aliyun_oss');
    expect(next.provider).toBe('aliyun_oss');
    expect(next.bucket).toBe('oss-bucket');
    expect(next.access_key).toBe('');
  });

  it('does not mark a saved inactive profile as globally enabled', () => {
    const inactive = {
      ...base,
      enabled: false,
      provider: 'tos',
      active_provider: 'tos',
    };
    const next = mergeSavedStorageState(
      {...base, enabled: false, provider: 'aliyun_oss'},
      inactive,
      'aliyun_oss',
    );
    expect(next.enabled).toBe(false);
    expect(next.provider).toBe('aliyun_oss');
  });

  it('initializes the first render from the loaded active provider', () => {
    const next = draftFromStorage({
      ...storage,
      provider: 'aliyun_oss',
      active_provider: 'aliyun_oss',
      profiles: {
        aliyun_oss: {
          bucket: 'yjhuabu',
          region: 'cn-beijing',
          endpoint: 'oss-cn-beijing.aliyuncs.com',
          internal_endpoint: '',
          public_domain: '',
          prefix: 'drama-canvas/',
          url_ttl_seconds: 3600,
          has_access_key: true,
          has_secret_key: true,
        },
      },
    });
    expect(next.provider).toBe('aliyun_oss');
    expect(next.bucket).toBe('yjhuabu');
    expect(next.enabled).toBe(true);
  });
});
