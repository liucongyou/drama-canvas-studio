import {useEffect, useState} from 'react';
import {Check, CloudUpload, KeyRound, RefreshCw, Save, ShieldCheck, X} from 'lucide-react';
import {
  getStorageConfig,
  saveStorageConfig,
  testStorageConfig,
  type StorageSpec,
} from './api';

type StorageDraft = {
  enabled: boolean;
  bucket: string;
  region: string;
  endpoint: string;
  internal_endpoint: string;
  prefix: string;
  url_ttl_seconds: string;
  auto_persist_images: boolean;
  require_for_ark_portrait: boolean;
  access_key: string;
  secret_key: string;
};

const DEFAULT_DRAFT: StorageDraft = {
  enabled: false,
  bucket: '',
  region: 'cn-beijing',
  endpoint: 'tos-cn-beijing.volces.com',
  internal_endpoint: 'tos-cn-beijing.ivolces.com',
  prefix: 'drama-canvas/',
  url_ttl_seconds: '3600',
  auto_persist_images: true,
  require_for_ark_portrait: true,
  access_key: '',
  secret_key: '',
};

function draftFromStorage(storage: StorageSpec): StorageDraft {
  return {
    enabled: storage.enabled,
    bucket: storage.bucket,
    region: storage.region || 'cn-beijing',
    endpoint: storage.endpoint || 'tos-cn-beijing.volces.com',
    internal_endpoint: storage.internal_endpoint || 'tos-cn-beijing.ivolces.com',
    prefix: storage.prefix || 'drama-canvas/',
    url_ttl_seconds: String(storage.url_ttl_seconds || 3600),
    auto_persist_images: storage.auto_persist_images !== false,
    require_for_ark_portrait: storage.require_for_ark_portrait !== false,
    access_key: '',
    secret_key: '',
  };
}

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

export function TosSettingsDialog({onClose}: {onClose: () => void}) {
  const [storage, setStorage] = useState<StorageSpec | null>(null);
  const [draft, setDraft] = useState<StorageDraft>({...DEFAULT_DRAFT});
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [status, setStatus] = useState('');
  const [testState, setTestState] = useState<{ok: boolean; message: string} | null>(null);

  const apply = (next: StorageSpec) => {
    setStorage(next);
    setDraft(draftFromStorage(next));
  };

  const load = async () => {
    setStatus('正在读取 TOS 配置…');
    try {
      apply(await getStorageConfig());
      setStatus('');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const update = <K extends keyof StorageDraft>(key: K, value: StorageDraft[K]) => {
    setDraft((current) => ({...current, [key]: value}));
    setStatus('');
  };

  const save = async () => {
    if (busy) return;
    setBusy(true);
    setStatus('');
    setTestState(null);
    try {
      const next = await saveStorageConfig({
        ...draft,
        url_ttl_seconds: Number(draft.url_ttl_seconds || 3600),
      });
      apply(next);
      setStatus('TOS 配置已保存');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    if (testing) return;
    setTesting(true);
    setTestState(null);
    try {
      const result = await testStorageConfig();
      setTestState({ok: result.ok, message: result.message || '连接成功'});
    } catch (error) {
      setTestState({
        ok: false,
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="canvas-help-backdrop model-settings-backdrop" onClick={onClose}>
      <section
        className="canvas-help tos-settings-dialog"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <strong>对象存储 TOS</strong>
            <span className="model-settings-subtitle">私有桶保存生成原始字节，Seedance 使用内网预签名读取</span>
          </div>
          <div className="model-settings-header-actions">
            <span className={`tos-state-pill ${storage?.ready ? 'is-ready' : ''}`}>
              {storage?.ready ? <Check size={12} /> : <CloudUpload size={12} />}
              {storage?.ready ? '配置完整' : '未启用'}
            </span>
            <button type="button" onClick={() => void load()} title="刷新 TOS 配置"><RefreshCw size={14} />刷新</button>
            <button type="button" className="canvas-inspector-close" onClick={onClose} aria-label="关闭"><X size={15} /></button>
          </div>
        </header>

        {status && <div className="model-settings-status">{status}</div>}

        <div className="tos-settings-scroll">
          <div className="tos-settings-hero">
            <span className="tos-settings-hero-icon"><ShieldCheck size={20} /></span>
            <div>
              <strong>原始产物直传</strong>
              <p>不压缩、不重绘；本地文件与 TOS 对象使用同一份原始字节。</p>
            </div>
            <Switch
              checked={draft.enabled}
              label="启用 TOS 原始产物持久化"
              onChange={(value) => update('enabled', value)}
            />
          </div>

          <div className="model-settings-form tos-settings-form">
            <label>
              <span>Bucket</span>
              <input value={draft.bucket} onChange={(event) => update('bucket', event.target.value)} placeholder="例如 drama-originals" autoComplete="off" />
            </label>
            <label>
              <span>Region</span>
              <input value={draft.region} onChange={(event) => update('region', event.target.value)} placeholder="cn-beijing" autoComplete="off" />
            </label>
            <label>
              <span>公网 Endpoint</span>
              <input value={draft.endpoint} onChange={(event) => update('endpoint', event.target.value)} placeholder="tos-cn-beijing.volces.com" autoComplete="off" />
            </label>
            <label>
              <span>内网 Endpoint</span>
              <input value={draft.internal_endpoint} onChange={(event) => update('internal_endpoint', event.target.value)} placeholder="tos-cn-beijing.ivolces.com" autoComplete="off" />
            </label>
            <label>
              <span>对象前缀</span>
              <input value={draft.prefix} onChange={(event) => update('prefix', event.target.value)} placeholder="drama-canvas/" autoComplete="off" />
            </label>
            <label>
              <span>预签名有效期</span>
              <input value={draft.url_ttl_seconds} onChange={(event) => update('url_ttl_seconds', event.target.value)} placeholder="3600" inputMode="numeric" />
            </label>
            <label className="is-wide">
              <span>Access Key {storage?.has_access_key ? <i>{storage.access_key_hint}</i> : null}</span>
              <div className="tos-secret-input">
                <KeyRound size={14} />
                <input type="password" value={draft.access_key} onChange={(event) => update('access_key', event.target.value)} placeholder="留空保留现有 Access Key" autoComplete="new-password" />
              </div>
            </label>
            <label className="is-wide">
              <span>Secret Key {storage?.has_secret_key ? <i>{storage.secret_key_hint}</i> : null}</span>
              <div className="tos-secret-input">
                <KeyRound size={14} />
                <input type="password" value={draft.secret_key} onChange={(event) => update('secret_key', event.target.value)} placeholder="留空保留现有 Secret Key" autoComplete="new-password" />
              </div>
            </label>
          </div>

          <div className="tos-settings-options">
            <div>
              <span><strong>图片生成后自动转存</strong><small>新生成的图片立即写入 TOS</small></span>
              <Switch checked={draft.auto_persist_images} label="图片生成后自动转存 TOS" onChange={(value) => update('auto_persist_images', value)} />
            </div>
            <div>
              <span><strong>Seedream 角色图强制 TOS</strong><small>进入 Seedance 前没有 TOS 原始对象时阻止提交</small></span>
              <Switch checked={draft.require_for_ark_portrait} label="Seedream 角色图强制使用 TOS" onChange={(value) => update('require_for_ark_portrait', value)} />
            </div>
          </div>

          {testState && (
            <div className={`tos-test-result ${testState.ok ? 'is-ok' : 'is-error'}`}>
              {testState.ok ? <Check size={13} /> : <X size={13} />}
              <span>{testState.message}</span>
            </div>
          )}
        </div>

        <footer className="model-settings-actions tos-settings-actions">
          <button type="button" className="is-primary" disabled={busy} onClick={() => void save()}>
            <Save size={13} />{busy ? '保存中…' : '保存 TOS 配置'}
          </button>
          <button type="button" disabled={testing || !storage?.ready} onClick={() => void test()}>
            {testing ? '测试中…' : '测试连接'}
          </button>
          <span className="model-settings-hint"><ShieldCheck size={12} />仅从服务端读取密钥，不返回完整 Secret</span>
        </footer>
      </section>
    </div>
  );
}
