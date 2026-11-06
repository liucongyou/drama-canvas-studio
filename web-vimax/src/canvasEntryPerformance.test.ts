import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const api = readFileSync(resolve(__dirname, 'api.ts'), 'utf8');
const canvas = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');

describe('canvas entry performance contract', () => {
  it('loads notes, media and tasks in parallel', () => {
    const block = api.slice(
      api.indexOf('export async function getCanvasEpisodeWorkspace'),
      api.indexOf('export async function saveCanvasNotes'),
    );
    expect(block).toContain('const [canvas, mediaNodes, tasks] = await Promise.all([');
    expect(block).toContain('getCanvasMediaNodes');
    expect(block).toContain('getGenerationTasks');
  });

  it('keeps task history out of the critical workspace payload', async () => {
    const source = readFileSync(resolve(__dirname, 'api.ts'), 'utf8');
    const workspaceStart = source.indexOf('export async function getCanvasWorkspace');
    const nextExport = source.indexOf('export async function getCanvasEpisodeWorkspace');
    const workspace = source.slice(workspaceStart, nextExport);
    expect(workspace).toContain('getGenerationTasks');
    expect(workspace).not.toMatch(/await\s+getGenerationTasks/);
  });

  it('emits measurable entry and settled marks', () => {
    expect(canvas).toContain("performance.mark('canvas-enter-workspace'");
    expect(canvas).toContain("performance.mark('canvas-layout-settled'");
  });
});
