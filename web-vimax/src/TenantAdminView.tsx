import {useEffect, useMemo, useState} from 'react';
import {
  Activity,
  ArrowLeft,
  Clock3,
  Database,
  ExternalLink,
  RefreshCw,
  Search,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import {
  getAuditHealth,
  getManagedAccounts,
  getManagedTenantAudit,
  type ManagedAccount,
} from './api';
import {
  auditRequestContext,
  formatAuditEntry,
  formatAuditOperation,
  type AuditEventItem,
} from './auditQuery';

type TenantRow = {
  tenantId: string;
  slug: string;
  memberCount: number;
  onlineCount: number;
  lastSeenAt: string;
};

type AuditHealth = {
  auditBytes: number;
  databaseBytes: number;
  freeBytes: number;
  lastAuditAt: string;
};

function tenantRows(accounts: ManagedAccount[]): TenantRow[] {
  const map = new Map<string, TenantRow>();
  for (const account of accounts) {
    const current = map.get(account.tenantId) || {
      tenantId: account.tenantId,
      slug: account.slug || account.tenantId,
      memberCount: 0,
      onlineCount: 0,
      lastSeenAt: '',
    };
    current.memberCount += 1;
    if (account.online) current.onlineCount += 1;
    if (
      account.lastSeenAt
      && (!current.lastSeenAt || account.lastSeenAt > current.lastSeenAt)
    ) {
      current.lastSeenAt = account.lastSeenAt;
    }
    map.set(account.tenantId, current);
  }
  return [...map.values()].sort((left, right) => left.slug.localeCompare(right.slug));
}

function dateTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function AuditRow({
  event,
  onFilterRequest,
}: {
  event: AuditEventItem;
  onFilterRequest: (requestId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const taskId = event.targetType === 'generation_task' ? event.targetId : '';
  const operationId = formatAuditOperation(event.details);
  const entry = formatAuditEntry(event.details);
  const {requestId, requestPath} = auditRequestContext(event.details);
  const recipeId = String(
    (event.details?.recipePlan as Record<string, unknown> | undefined)?.recipeId
    || event.details?.recipeId
    || '',
  );
  const requestUrl = requestPath && requestId
    ? `${requestPath}?requestId=${encodeURIComponent(requestId)}`
    : '';
  const summary = [
    taskId ? `任务 ${taskId}` : '',
    operationId ? `操作 ${operationId}` : '',
    entry !== 'unknown' ? `入口 ${entry}` : '',
    recipeId ? `配方 ${recipeId}` : '',
  ].filter(Boolean).join(' · ');
  return (
    <article className={`tenant-audit-row is-${event.status || 'success'}`}>
      <button
        type="button"
        className="tenant-audit-row-main"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <span className="tenant-audit-action">{event.action}</span>
        <span className="tenant-audit-summary">{summary || event.targetType || '系统操作'}</span>
        <span className="tenant-audit-when">{dateTime(event.createdAt)}</span>
      </button>
      {expanded && (
        <div className="tenant-audit-details">
          <dl>
            <div><dt>操作人</dt><dd>{event.actor || '系统'}</dd></div>
            <div><dt>对象</dt><dd>{event.targetType || '—'} / {event.targetId || '—'}</dd></div>
            <div><dt>结果</dt><dd>{event.status || 'success'}</dd></div>
            {taskId && <div><dt>任务 ID</dt><dd>{taskId}</dd></div>}
            {operationId && <div><dt>操作 ID</dt><dd>{operationId}</dd></div>}
            {entry !== 'unknown' && <div><dt>提交入口</dt><dd>{entry}</dd></div>}
          </dl>
          <pre>{JSON.stringify(event.details, null, 2)}</pre>
          {requestUrl ? (
            <div className="tenant-audit-request-actions">
              <a
                className="tenant-audit-request-link"
                href={requestUrl}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(clickEvent) => clickEvent.stopPropagation()}
              >
                <ExternalLink size={11} />
                同一请求原始记录
              </a>
              {requestId && (
                <button
                  type="button"
                  className="tenant-audit-request-filter"
                  onClick={() => onFilterRequest(requestId)}
                >
                  只看同一请求
                </button>
              )}
            </div>
          ) : null}
        </div>
      )}
    </article>
  );
}

export function TenantAdminView({
  accounts: initialAccounts,
  initialRequestId = '',
  onClose,
}: {
  accounts: ManagedAccount[];
  initialRequestId?: string;
  onClose: () => void;
}) {
  const [accounts, setAccounts] = useState(initialAccounts);
  const [selectedTenant, setSelectedTenant] = useState<TenantRow | null>(null);
  const [events, setEvents] = useState<AuditEventItem[]>([]);
  const [search, setSearch] = useState('');
  const [actionFilter, setActionFilter] = useState('');
  const [recipeFilter, setRecipeFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [beforeId, setBeforeId] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [retention, setRetention] = useState<{maxRows: number; ttlDays: number} | null>(null);
  const [requestFilter, setRequestFilter] = useState(
    () => initialRequestId,
  );
  const [health, setHealth] = useState<AuditHealth | null>(null);
  const tenants = useMemo(() => tenantRows(accounts), [accounts]);

  useEffect(() => {
    if (initialAccounts.length > 0) {
      setAccounts(initialAccounts);
      return;
    }
    let cancelled = false;
    void getManagedAccounts()
      .then((snapshot) => {
        if (!cancelled) setAccounts(snapshot.accounts);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : '租户列表读取失败');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [initialAccounts]);

  useEffect(() => {
    let cancelled = false;
    void getAuditHealth()
      .then((value) => {
        if (!cancelled) setHealth(value);
      })
      .catch(() => {
        // The main tenant/audit list remains usable if metrics are unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadEvents = async (
    tenant: TenantRow,
    options: {
      append?: boolean;
      cursor?: string;
      requestId?: string;
      action?: string;
      recipeId?: string;
    } = {},
  ) => {
    const append = options.append === true;
    if (append) setLoadingMore(true);
    else setLoading(true);
    setError('');
    try {
      const page = await getManagedTenantAudit(tenant.tenantId, {
        action: options.action ?? actionFilter,
        requestId: options.requestId ?? requestFilter,
        recipeId: options.recipeId ?? recipeFilter,
        beforeId: options.cursor || '',
        limit: 100,
      });
      setEvents((current) => append ? [...current, ...page.events] : page.events);
      setBeforeId(page.nextBeforeId);
      setHasMore(page.hasMore);
      setRetention(page.retention);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '审计记录读取失败');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  const openTenant = async (tenant: TenantRow) => {
    setSelectedTenant(tenant);
    setEvents([]);
    setBeforeId('');
    setHasMore(false);
    setActionFilter('');
    setRecipeFilter('');
    setSearch('');
    await loadEvents(tenant, {requestId: initialRequestId});
  };

  useEffect(() => {
    if (selectedTenant) void loadEvents(selectedTenant);
  }, [requestFilter]);

  const visibleEvents = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return events;
    return events.filter((event) => {
      const haystack = [
        event.action,
        event.actor,
        event.targetId,
        event.status,
        event.details?.operationId,
        event.details?.entry,
        event.details?.batchId,
        event.details?.projectId,
        event.details?.nodeId,
        (event.details?.recipePlan as Record<string, unknown> | undefined)?.recipeId,
        event.details?.recipeId,
        (event.details?.auditContext as Record<string, unknown> | undefined)?.requestId,
      ].join(' ').toLowerCase();
      return haystack.includes(needle);
    });
  }, [events, search]);

  const totalOnline = tenants.reduce((sum, tenant) => sum + tenant.onlineCount, 0);
  return (
    <div
      className="dialog-backdrop tenant-admin-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section className="tenant-admin-view" role="dialog" aria-modal="true" aria-labelledby="tenant-admin-title">
        <header>
          <div>
            <h2 id="tenant-admin-title">{selectedTenant ? `租户审计 · ${selectedTenant.slug}` : '租户数据与审计'}</h2>
            <p>
              {selectedTenant
                ? '只展示当前租户的隔离数据。点击记录查看任务 ID、操作 ID、批次与请求来源。'
                : '按租户查看账号、在线状态与操作记录；不同租户的数据不会混在一起。'}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={16} strokeWidth={2} /></button>
        </header>

        {!selectedTenant ? (
          <>
            <div className="tenant-admin-overview">
              <div><Users size={16} /><strong>{tenants.length}</strong><span>租户</span></div>
              <div><Activity size={16} /><strong>{totalOnline}</strong><span>在线账号</span></div>
              <div><ShieldCheck size={16} /><strong>隔离</strong><span>租户范围</span></div>
              <div>
                <Database size={16} />
                <strong>{health ? `${Math.ceil(health.auditBytes / 1024)} KB` : '自动'}</strong>
                <span>{health ? `数据库 ${Math.ceil(health.databaseBytes / 1024 / 1024)} MB` : '审计轮转'}</span>
              </div>
            </div>
            <div className="tenant-admin-list">
              {tenants.map((tenant) => (
                <button
                  key={tenant.tenantId}
                  type="button"
                  className="tenant-admin-list-row"
                  onClick={() => void openTenant(tenant)}
                >
                  <span>
                    <strong>{tenant.slug}</strong>
                    <small>{tenant.memberCount} 个账号 · {tenant.onlineCount} 人在线</small>
                  </span>
                  <span className="tenant-admin-last-seen">
                    {tenant.lastSeenAt ? `最近活动 ${dateTime(tenant.lastSeenAt)}` : '暂无活动记录'}
                  </span>
                  <ArrowLeft size={14} className="is-forward" aria-hidden="true" />
                </button>
              ))}
              {tenants.length === 0 && <p className="tenant-admin-empty">暂无租户账号。</p>}
            </div>
          </>
        ) : (
          <>
            <div className="tenant-audit-toolbar">
              <button type="button" onClick={() => setSelectedTenant(null)}>
                <ArrowLeft size={13} />返回租户列表
              </button>
              <label>
                <Search size={13} />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索操作、任务 ID、操作 ID、节点"
                />
              </label>
              <label>
                <Search size={13} />
                <input
                  value={recipeFilter}
                  onChange={(event) => setRecipeFilter(event.target.value)}
                  placeholder="按镜头配方 ID 筛选"
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void loadEvents(selectedTenant);
                  }}
                />
              </label>
              <label>
                <Search size={13} />
                <input
                  value={actionFilter}
                  onChange={(event) => setActionFilter(event.target.value)}
                  placeholder="按操作筛选，例如 generation"
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void loadEvents(selectedTenant);
                  }}
                />
              </label>
              <button type="button" onClick={() => void loadEvents(selectedTenant)} disabled={loading}>
                <RefreshCw size={13} className={loading ? 'is-spinning' : ''} />刷新
              </button>
            </div>
            {retention && (
              <div className="tenant-audit-retention">
                <Clock3 size={12} />
                保留最近 {retention.maxRows.toLocaleString()} 条 / {retention.ttlDays} 天，超出后由后台低频轮转。
              </div>
            )}
            {requestFilter && (
              <div className="tenant-audit-request-filter">
                仅显示请求 {requestFilter}
                <button
                  type="button"
                  onClick={() => {
                    const next = new URLSearchParams(window.location.search);
                    next.delete('requestId');
                    window.history.replaceState(null, '', `${window.location.pathname}${next.toString() ? `?${next}` : ''}`);
                    setRequestFilter('');
                  }}
                >
                  清除
                </button>
              </div>
            )}
            {error && <div className="tenant-admin-error" role="alert">{error}</div>}
            <div className="tenant-audit-list">
              {visibleEvents.map((event) => (
                <AuditRow
                  key={event.id}
                  event={event}
                  onFilterRequest={setRequestFilter}
                />
              ))}
              {!loading && visibleEvents.length === 0 && (
                <p className="tenant-admin-empty">当前筛选下没有审计记录。</p>
              )}
              {loading && <p className="tenant-admin-empty">正在读取审计记录…</p>}
            </div>
            {hasMore && !search.trim() && (
              <button
                type="button"
                className="tenant-audit-more"
                disabled={loadingMore}
                onClick={() => void loadEvents(selectedTenant, {append: true, cursor: beforeId})}
              >
                {loadingMore ? '读取中…' : '加载更早记录'}
              </button>
            )}
          </>
        )}
      </section>
    </div>
  );
}
