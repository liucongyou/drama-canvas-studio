import {useState} from 'react';
import {History, X} from 'lucide-react';
import {APP_VERSION} from './changelog';
import {CHANGELOG} from './changelogEntries';

/**
 * 更新记录弹窗。
 *
 * 单独成文件是为了让它（以及 55KB 的发布记录文本）走异步分包：首屏不需要它，
 * 只有点开「更新记录」时才加载。DOM 结构与 class 名保持原样，样式不受影响。
 */
export default function ChangelogDialog({onClose}: {onClose: () => void}) {
  const [visibleCount, setVisibleCount] = useState(12);
  const entries = CHANGELOG.slice(0, visibleCount);
  const latest = CHANGELOG[0];
  const formatReleaseTime = (value: string) => {
    const match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)(?:\s*([+-]\d{2}:?\d{2}))?/.exec(value || '');
    if (match) {
      return `${match[1]} ${match[2]}`;
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value || '-'
      : date.toLocaleString('zh-CN', {hour12: false, timeZone: 'Asia/Shanghai'});
  };
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="canvas-changelog-dialog" role="dialog" aria-modal="true" aria-labelledby="changelog-title">
        <header>
          <div className="canvas-changelog-heading">
            <span><History size={17} strokeWidth={1.9} aria-hidden="true" /></span>
            <div>
              <h2 id="changelog-title">更新记录</h2>
              <p>当前版本 v{APP_VERSION} · 最近更新 {latest?.releasedAt ? formatReleaseTime(latest.releasedAt) : (latest?.date || '-')}</p>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭更新记录"><X size={16} strokeWidth={2} /></button>
        </header>
        <div className="canvas-changelog-list">
          {entries.map((entry, index) => (
            <article key={entry.version} className={index === 0 ? 'is-current' : ''}>
              <div className="canvas-changelog-version">
                <strong>v{entry.version}</strong>
                <time dateTime={entry.releasedAt || entry.date}>{formatReleaseTime(entry.releasedAt || entry.date)}</time>
                {index === 0 && <em>当前版本</em>}
              </div>
              <h3>{entry.title}</h3>
              <ul>
                {entry.changes.map((change) => <li key={change}>{change}</li>)}
              </ul>
            </article>
          ))}
          {visibleCount < CHANGELOG.length && (
            <button
              type="button"
              className="canvas-changelog-more"
              onClick={() => setVisibleCount((count) => Math.min(CHANGELOG.length, count + 12))}
            >
              加载更多记录（已显示 {visibleCount} / {CHANGELOG.length}）
            </button>
          )}
        </div>
        <footer>
          <span>当前页面 v{APP_VERSION} · 记录与前端代码同仓维护</span>
          <button
            type="button"
            onClick={() => window.location.reload()}
            title="重新加载最新前端资源"
          >
            重新加载
          </button>
          <button type="button" onClick={onClose}>知道了</button>
        </footer>
      </section>
    </div>
  );
}
