import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';


const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);


describe('multi-selection context menu', () => {
  it('opens the existing batch generation dialog from the context menu', () => {
    expect(source).toMatch(
      /const generatable = picked\.filter\(\(mn\) => mn\.kind === 'image' \|\| mn\.kind === 'video' \|\| mn\.kind === 'audio'\);/,
    );
    expect(source).toMatch(
      /批量生成…（\{generatable\.length\} 个）/,
    );
    expect(source).toMatch(
      /setBatchGenerateTargets\(generatable\)/,
    );
  });

  it('does not render the redundant cancel-selection item', () => {
    expect(source).toContain('取消选择（Esc）');
    expect(source).not.toMatch(
      /className="is-cancel"[^>]*onClick=\{\(\) => setContextMenu\(null\)\}/,
    );
  });

  it('shows group and ungroup as one mutually exclusive menu action', () => {
    expect(source).toMatch(
      /const allGrouped = contextMenu\.selIds!\.every\(\(id\) => groupMap\.has\(id\)\);/,
    );
    expect(source).toMatch(
      /allGrouped \? \(\s*<button[^>]*>[\s\S]*?解除编组（\{contextMenu\.selIds!\.length\} 项）[\s\S]*?\) : \(\s*<button[^>]*>[\s\S]*?编组（\{contextMenu\.selIds!\.length\} 项）/,
    );
    expect(source.match(/解除编组（\{contextMenu\.selIds!\.length\} 项）/g)).toHaveLength(1);
  });

  it('separates clipboard copy from paste in the context menu', () => {
    expect(source).toContain('>复制（Ctrl+C）</button>');
    expect(source).toContain('>粘贴（Ctrl+V）</button>');
    expect(source).toContain('copySelectionFromMenu()');
    expect(source).toContain('pasteNodesFromMenu()');
    expect(source).toContain('saveNodeClipboard(snapshot)');
  });

  it('uses the same cut/copy/paste contract for single nodes', () => {
    expect(source).not.toContain('>复制（Ctrl+D）</button>');
    expect(source).not.toMatch(/>复制<\/button>\}\s*<\/>/);
    expect(
      source.match(/>剪切（Ctrl\+X）<\/button>/g)?.length,
    ).toBeGreaterThanOrEqual(3);
    expect(
      source.match(/>复制（Ctrl\+C）<\/button>/g)?.length,
    ).toBeGreaterThanOrEqual(3);
    expect(
      source.match(/>粘贴（Ctrl\+V）<\/button>/g)?.length,
    ).toBeGreaterThanOrEqual(3);
  });
});
