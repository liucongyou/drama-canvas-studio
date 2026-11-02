import {useState} from 'react';
import {Check, CloudUpload, KeyRound, RefreshCw, Save, ShieldCheck, X} from 'lucide-react';
import {
  activateStorage,
  deactivateStorage,
  getStorageConfig,
  saveStorageConfig,
  testStorageConfig,
  type StorageSpec,
} from './api';
import {
  draftForProvider,
  draftFromStorage,
  mergeSavedStorageState,
  mergeStorageProfile,
  storedProviderState,
  type StorageDraft,
} from './storageSettings';

function storageLabel(provider: string) {
  if (provider === 'aliyun_oss') return '阿里云 OSS';
  if (provider === 'qiniu') return '七牛云';
  return '火山 TOS';
}

const DEFAULT_DRAFT: StorageDraft = {
  enabled: false,
  provider: 'tos',
  bucket: '',
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

function Switch({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`model-settings-switch ${checked ? 'is-on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <i />
    </button>
  );
}

export function TosSettingsDialog({
  onClose,
  initialStorage,
  onStorageChange,
}: {
  onClose: () => void;
  initialStorage: StorageSpec;
  onStorageChange?: (storage: StorageSpec) => void;
}) {
  const [storage, setStorage] = useState<StorageSpec>(initialStorage);
  const [draft, setDraft] = useState<StorageDraft>(() => draftFromStorage(initialStorage));
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testingProvider, setTestingProvider] = useState<'tos' | 'aliyun_oss' | 'qiniu' | null>(null);
  const [status, setStatus] = useState('');
  const [testState, setTestState] = useState<{ok: boolean; message: string} | null>(null);

  const apply = (next: StorageSpec) => {
    onStorageChange?.(next);
    setStorage(next);
    setDraft(draftFromStorage(next));
  };

  const load = async () => {
    setStatus('正在读取存储配置…');
    try {
      apply(await getStorageConfig());
      setStatus('');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  const update = <K extends keyof StorageDraft>(key: K, value: StorageDraft[K]) => {
    setDraft((current) => ({...current, [key]: value}));
    setStatus('');
  };

  const selectProvider = (provider: StorageDraft['provider']) => {
    setDraft((current) => draftForProvider(current, storage, provider));
    setTestState(null);
    setStatus('');
  };

  const save = async () => {
    if (busy) return;
    const provider = draft.provider;
    setBusy(true);
    setStatus('');
    setTestState(null);
    try {
      const next = await saveStorageConfig({
        ...draft,
        url_ttl_seconds: Number(draft.url_ttl_seconds || 3600),
      });
      setStorage(next);
      setDraft((current) => mergeSavedStorageState(current, next, provider));
      setStatus(`${storageLabel(provider)} 配置已保存`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    if (testing) return;
    const provider = draft.provider;
    setTesting(true);
    setTestingProvider(provider);
    setTestState(null);
    try {
      // Test the form the user is looking at. The endpoint without a provider
      // reads the last saved profile, so an edited key would otherwise be
      // silently ignored and produce a misleading success.
      const saved = await saveStorageConfig({
        ...draft,
        url_ttl_seconds: Number(draft.url_ttl_seconds || 3600),
      });
      setStorage(saved);
      setDraft((current) => mergeSavedStorageState(current, saved, provider));
      const result = await testStorageConfig(provider);
      setTestState({ok: result.ok, message: result.message || '连接成功'});
      const refreshed = await getStorageConfig();
      setStorage(refreshed);
      setDraft((current) => mergeStorageProfile(current, refreshed, provider));
    } catch (error) {
      setTestState({
        ok: false,
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setTesting(false);
      setTestingProvider(null);
    }
  };

  const activate = async () => {
    if (busy) return;
    setBusy(true);
    setStatus('');
    try {
      const next = await activateStorage(draft.provider);
      apply(next);
      setStatus(`${storageLabel(draft.provider)} 已启用`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const deactivate = async () => {
    if (busy) return;
    setBusy(true);
    setStatus('');
    try {
      const next = await deactivateStorage();
      apply(next);
      setStatus('自动转存已停用');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const storedCredentials = Boolean(
    storage?.profiles?.[draft.provider]?.has_access_key
    && storage.profiles[draft.provider].has_secret_key,
  );
  const draftCredentials = Boolean(
    draft.access_key.trim() && draft.secret_key.trim(),
  );
  const canTest = Boolean(
    draft.bucket.trim()
    && (storedCredentials || draftCredentials)
    && (draft.provider !== 'qiniu' || draft.public_domain.trim())
    && (draft.provider === 'qiniu' || draft.endpoint.trim()),
  );
  const selectedProfileTest = storage?.profiles?.[draft.provider];
  const selectedStoredState = storedProviderState(storage, draft.provider);
  const selectedProviderActive = Boolean(
    storage?.enabled && storage.provider === draft.provider,
  );
  const selectedProviderReady = (
    draft.provider === 'tos'
      ? storage?.tos_ready
      : draft.provider === 'aliyun_oss'
        ? storage?.oss_ready
        : storage?.qiniu_ready
  );

  return (
    <div className="canvas-help-backdrop model-settings-backdrop">
      <section
        className="canvas-help tos-settings-dialog"
        role="dialog"
        aria-modal="true"
      >
        <header>
          <div>
            <strong>素材存储与转存</strong>
            <span className="model-settings-subtitle">生成图片、视频后自动转入所选存储；存储失败不影响已生成的素材</span>
          </div>
          <div className="model-settings-header-actions">
            <span className={`tos-state-pill ${storage?.enabled ? 'is-ready' : ''}`}>
              {storage?.enabled ? <Check size={12} /> : <CloudUpload size={12} />}
              {storage?.enabled ? `${storageLabel(storage.provider)} 自动转存已启用` : '自动转存未启用'}
            </span>
            <button type="button" onClick={() => void load()} title="刷新存储配置"><RefreshCw size={14} />刷新</button>
            <button type="button" className="canvas-inspector-close" onClick={onClose} aria-label="关闭"><X size={15} /></button>
          </div>
        </header>

        {status && <div className="model-settings-status">{status}</div>}

        <div className="tos-settings-scroll">
          <div className="tos-settings-hero">
            <span className="tos-settings-hero-icon"><ShieldCheck size={20} /></span>
            <div>
              <strong>对象存储与 Seedance 原始图</strong>
              <p>
                自动转存只使用一个存储；火山 TOS 可独立保存并用于 Seedance 角色图。
              </p>
            </div>
          </div>
          <div className="storage-provider-picker">
            <button
              type="button"
              className={draft.provider === 'tos' ? 'is-active' : ''}
              onClick={() => {
                selectProvider('tos');
              }}
            >
              火山 TOS
              {storage?.profiles?.tos && (
                <small>
                  {storedProviderState(storage, 'tos') === 'active'
                    ? '自动转存'
                    : '已保存'}
                </small>
              )}
            </button>
            <button
              type="button"
              className={draft.provider === 'aliyun_oss' ? 'is-active' : ''}
              onClick={() => {
                selectProvider('aliyun_oss');
              }}
            >
              阿里云 OSS
              {storage?.profiles?.aliyun_oss && (
                <small>
                  {storedProviderState(storage, 'aliyun_oss') === 'active'
                    ? '自动转存'
                    : '已保存'}
                </small>
              )}
            </button>
            <button
              type="button"
              className={draft.provider === 'qiniu' ? 'is-active' : ''}
              onClick={() => {
                selectProvider('qiniu');
              }}
            >
              七牛云
              {storage?.profiles?.qiniu && (
                <small>
                  {storedProviderState(storage, 'qiniu') === 'active'
                    ? '自动转存'
                    : '已保存'}
                </small>
              )}
            </button>
          </div>
          <div className={`storage-global-toggle ${storage?.enabled ? 'is-on' : ''}`}>
            <span>
              <strong>自动转存 · {storage?.enabled ? '已开启' : '已关闭'}</strong>
              <small>
                {storage?.enabled
                  ? `生成资产会转入 ${storageLabel(storage.provider)}；切换上方供应商只编辑配置。`
                  : '供应商配置仍会保留，需要时可单独设置为自动转存。'}
              </small>
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={Boolean(storage?.enabled)}
              aria-label="自动转存"
              className={`model-settings-switch ${storage?.enabled ? 'is-on' : ''}`}
              disabled={busy}
              onClick={() => void (storage?.enabled ? deactivate() : activate())}
            >
              <i />
            </button>
          </div>

          <div className="model-settings-form tos-settings-form">
            <label>
              <span>Bucket</span>
              <input value={draft.bucket} onChange={(event) => update('bucket', event.target.value)} placeholder={draft.provider === 'qiniu' ? '七牛空间名' : draft.provider === 'aliyun_oss' ? 'OSS Bucket 名' : '例如 drama-originals'} autoComplete="off" />
            </label>
            {draft.provider !== 'qiniu' && <label>
              <span>Region</span>
              <input value={draft.region} onChange={(event) => update('region', event.target.value)} placeholder={draft.provider === 'aliyun_oss' ? 'oss-cn-beijing' : 'cn-beijing'} autoComplete="off" />
            </label>}
            {draft.provider === 'tos' && <>
              <label>
                <span>公网 Endpoint</span>
                <input value={draft.endpoint} onChange={(event) => update('endpoint', event.target.value)} placeholder="tos-cn-beijing.volces.com" autoComplete="off" />
              </label>
              <label>
                <span>内网 Endpoint</span>
                <input value={draft.internal_endpoint} onChange={(event) => update('internal_endpoint', event.target.value)} placeholder="tos-cn-beijing.ivolces.com" autoComplete="off" />
              </label>
            </>}
            {draft.provider === 'aliyun_oss' && <label>
              <span>OSS Endpoint</span>
              <input value={draft.endpoint} onChange={(event) => update('endpoint', event.target.value)} placeholder="oss-cn-beijing.aliyuncs.com" autoComplete="off" />
            </label>}
            {draft.provider === 'qiniu' && <label className="is-wide">
              <span>公网访问域名</span>
              <input value={draft.public_domain} onChange={(event) => update('public_domain', event.target.value)} placeholder="https://cdn.example.com" autoComplete="off" />
            </label>}
            <label>
              <span>对象前缀</span>
              <input value={draft.prefix} onChange={(event) => update('prefix', event.target.value)} placeholder="drama-canvas/" autoComplete="off" />
            </label>
            <label>
              <span>预签名有效期</span>
              <input value={draft.url_ttl_seconds} onChange={(event) => update('url_ttl_seconds', event.target.value)} placeholder="3600" inputMode="numeric" />
            </label>
            <label className="is-wide">
              <span>
                Access Key{' '}
                {storage?.profiles?.[draft.provider]?.has_access_key
                  ? <i>{storage.profiles[draft.provider].access_key_hint}</i>
                  : <i className="is-empty">未配置</i>}
              </span>
              <div className="tos-secret-input">
                <KeyRound size={14} />
              <input type="password" value={draft.access_key} onChange={(event) => update('access_key', event.target.value)} placeholder={storage?.profiles?.[draft.provider]?.has_access_key ? '留空保留现有 Access Key' : '填写该服务商的 Access Key'} autoComplete="new-password" />
              </div>
            </label>
            <label className="is-wide">
              <span>
                Secret Key{' '}
                {storage?.profiles?.[draft.provider]?.has_secret_key
                  ? <i>{storage.profiles[draft.provider].secret_key_hint}</i>
                  : <i className="is-empty">未配置</i>}
              </span>
              <div className="tos-secret-input">
                <KeyRound size={14} />
              <input type="password" value={draft.secret_key} onChange={(event) => update('secret_key', event.target.value)} placeholder={storage?.profiles?.[draft.provider]?.has_secret_key ? '留空保留现有 Secret Key' : '填写该服务商的 Secret Key'} autoComplete="new-password" />
              </div>
            </label>
          </div>

          <div className="tos-settings-options">
            <div>
              <span><strong>图片生成后自动转存</strong><small>新生成的图片立即写入当前对象存储</small></span>
              <Switch checked={draft.auto_persist_images} label="图片生成后自动转存" onChange={(value) => update('auto_persist_images', value)} />
            </div>
            <div>
              <span><strong>视频生成后自动转存</strong><small>视频先显示，再在后台缓存并写入当前对象存储</small></span>
              <Switch checked={draft.auto_persist_videos} label="视频生成后自动转存" onChange={(value) => update('auto_persist_videos', value)} />
            </div>
            <div>
              <span><strong>音频参考自动转存</strong><small>视频提交前把本地音频转成公开 URL</small></span>
              <Switch checked={draft.auto_persist_audio} label="音频参考自动转存" onChange={(value) => update('auto_persist_audio', value)} />
            </div>
            <div>
              <span><strong>Seedream 原链失效时使用对象存储</strong><small>临时链接仍有效时可直接引用；失效后才要求 TOS</small></span>
              <Switch checked={draft.require_for_ark_portrait} label="Seedream 原链失效时使用对象存储" onChange={(value) => update('require_for_ark_portrait', value)} />
            </div>
          </div>

          {draft.provider === 'tos' && storage?.profiles?.tos && (
            <div className={`tos-test-result ${selectedProviderReady ? 'is-ok' : ''}`}>
              {selectedProviderReady ? <Check size={13} /> : <CloudUpload size={13} />}
              <span>
                {selectedProviderActive
                  ? 'TOS 已用于全局自动转存；Seedance 会优先使用已保存的 TOS 原始图。'
                  : 'TOS 配置已独立保存；即使 OSS 是全局自动转存，Seedance 也可优先使用 TOS 原始图。'}
              </span>
            </div>
          )}
          {draft.provider !== 'tos' && selectedStoredState !== 'empty' && (
            <div className={`tos-test-result ${selectedStoredState === 'active' ? 'is-ok' : ''}`}>
              {selectedStoredState === 'active' ? <Check size={13} /> : <ShieldCheck size={13} />}
              <span>
                {selectedStoredState === 'active'
                  ? `${storageLabel(draft.provider)} 配置已保存，并用于全局自动转存。`
                  : `${storageLabel(draft.provider)} 配置已保存，可测试或设为自动转存。`}
              </span>
            </div>
          )}

          {testState && (
            <div className={`tos-test-result ${testState.ok ? 'is-ok' : 'is-error'}`}>
              {testState.ok ? <Check size={13} /> : <X size={13} />}
              <span>{testState.message}</span>
            </div>
          )}
          {!testState && testingProvider === draft.provider && (
            <div className="tos-test-result">
              <span>正在上传测试文件并检查公网访问…</span>
            </div>
          )}
          {!testState && testingProvider === null && selectedProfileTest?.last_test_ok === false && selectedProfileTest.last_test_error && (
            <div className="tos-test-result is-error">
              <X size={13} />
              <span>{selectedProfileTest.last_test_error}</span>
            </div>
          )}
        </div>

        <footer className="model-settings-actions tos-settings-actions">
          <button type="button" className="is-primary" disabled={busy} onClick={() => void save()}>
            <Save size={13} />{busy ? '保存中…' : '保存存储配置'}
          </button>
          <button type="button" disabled={testing || !canTest} onClick={() => void test()}>
            {testing ? '测试中…' : `测试 ${storageLabel(draft.provider)}`}
          </button>
          <button
            type="button"
            disabled={busy || !canTest || (draft.provider === 'tos' && !storage?.profiles?.tos) || selectedProviderActive}
            onClick={() => void activate()}
          >
            {selectedProviderActive ? '自动转存已启用' : '设为自动转存'}
          </button>
          {storage?.enabled && (
            <button type="button" disabled={busy} onClick={() => void deactivate()}>
              停用自动转存
            </button>
          )}
          <span className="model-settings-hint"><ShieldCheck size={12} />密钥仅保存在服务端，不返回完整值</span>
        </footer>
      </section>
    </div>
  );
}
