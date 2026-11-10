import {useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {Check, ChevronRight, Copy} from 'lucide-react';
import type {AgentContentBlock} from './agentApi';

export function normalizeContentBlocks(blocks: AgentContentBlock[] | undefined, fallback = ''): AgentContentBlock[] {
  if (blocks && blocks.length) return blocks;
  return fallback ? [{type: 'text', text: fallback}] : [];
}

export function AgentMessageContent({blocks, fallback = ''}: {blocks?: AgentContentBlock[]; fallback?: string}) {
  const normalized = normalizeContentBlocks(blocks, fallback);
  return (
    <div className="agent-message-content">
      {normalized.map((block, index) => {
        if (block.type === 'markdown') {
          return (
            <div className="agent-markdown" key={index}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                skipHtml
                components={{
                  a: ({href, children}) => {
                    const safe = Boolean(href && /^(https?:|mailto:|\/)/i.test(href));
                    return safe
                      ? <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
                      : <span>{children}</span>;
                  },
                  code: ({className, children}) => {
                    const value = String(children || '');
                    const blockCode = Boolean(className) || value.includes('\n');
                    return blockCode
                      ? <CodeBlock value={value.replace(/\n$/, '')} />
                      : <code className="agent-inline-code">{value}</code>;
                  },
                }}
              >
                {block.text}
              </ReactMarkdown>
            </div>
          );
        }
        if (block.type === 'json') {
          return <JsonBlock key={index} value={block.data} />;
        }
        if (block.type === 'tool') {
          return (
            <details className="agent-tool-block" key={index}>
              <summary>
                <ChevronRight size={12} />
                <span>{block.name || '工具调用'}</span>
                {block.status && <small>{block.status}</small>}
              </summary>
              {block.summary && <p>{block.summary}</p>}
            </details>
          );
        }
        if (block.type === 'notice') {
          return (
            <div className={'agent-notice is-' + (block.level || 'info')} key={index}>
              {block.code && <small>{block.code}</small>}
              <span>{block.message}</span>
            </div>
          );
        }
        if (block.type === 'artifact') {
          return <div className="agent-artifact-ref" key={index}>产物 {block.artifactId}</div>;
        }
        if (block.type === 'text') {
          return <div className="agent-plain-text" key={index}>{block.text}</div>;
        }
        return <div className="agent-notice is-warning" key={index}>无法识别的内容块</div>;
      })}
    </div>
  );
}

function CodeBlock({value}: {value: string}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {}
  };
  return (
    <div className="agent-code-block">
      <button type="button" onClick={() => void copy()} title="复制代码" aria-label="复制代码">
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      <pre><code>{value}</code></pre>
    </div>
  );
}

function JsonBlock({value}: {value: unknown}) {
  const text = JSON.stringify(value, null, 2);
  return (
    <details className="agent-json-block">
      <summary><ChevronRight size={12} /> JSON</summary>
      <pre>{text.length > 60_000 ? text.slice(0, 60_000) + '\n\n… 已截断' : text}</pre>
    </details>
  );
}
