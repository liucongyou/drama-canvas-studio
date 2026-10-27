import {mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {afterEach, describe, expect, it} from 'vitest';
import {readAgentConfig, saveAgentConfig} from './config-store.mjs';

const roots = [];

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, {recursive: true, force: true})));
});

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'vimax-config-'));
  roots.push(root);
  await mkdir(path.join(root, 'configs'), {recursive: true});
  await writeFile(path.join(root, 'configs', 'agent.local.yaml'), [
    'llm:',
    '  model_provider: openai',
    '  model: existing-model',
    '  base_url: https://example.test/v1',
    '  api_key: secret-value',
    '',
  ].join('\n'));
  return root;
}

describe('agent config store', () => {
  it('never returns stored API keys', async () => {
    const root = await fixture();
    const config = await readAgentConfig(root);
    expect(config.sections.llm).toMatchObject({model: 'existing-model', api_key: '', has_api_key: true});
    expect(JSON.stringify(config)).not.toContain('secret-value');
  });

  it('never returns a complete short API key in its hint', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {llm: {api_key: 'abc123'}}});
    const config = await readAgentConfig(root);
    expect(config.sections.llm.has_api_key).toBe(true);
    expect(config.sections.llm.api_key_hint).not.toBe('abc123');
    expect(JSON.stringify(config)).not.toContain('abc123');
  });

  it('keeps a stored key when a blank key is saved', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {llm: {model: 'new-model', api_key: ''}}});
    const saved = await readFile(path.join(root, 'configs', 'agent.local.yaml'), 'utf8');
    expect(saved).toContain('model: new-model');
    expect(saved).toContain('api_key: secret-value');
  });

  it('rejects invalid base URLs', async () => {
    const root = await fixture();
    await expect(saveAgentConfig(root, {sections: {llm: {base_url: 'file:///tmp/key'}}})).rejects.toThrow(/http/);
  });

  it('round-trips video generation defaults', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {video: {model: 'm', base_url: 'https://example.test/v1', aspect_ratio: '9:16', resolution: '1080p', fps: '24', duration: '10'}}});
    const config = await readAgentConfig(root);
    expect(config.sections.video).toMatchObject({aspect_ratio: '9:16', resolution: '1080p', fps: '24', duration: '10'});
    const saved = await readFile(path.join(root, 'configs', 'agent.local.yaml'), 'utf8');
    expect(saved).toContain('aspect_ratio: 9:16');
    expect(saved).toContain('duration: 10');
  });

  it('keeps existing video defaults when fields are left blank', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {video: {model: 'm', base_url: 'https://example.test/v1', aspect_ratio: '9:16'}}});
    await saveAgentConfig(root, {sections: {video: {model: 'm2', base_url: 'https://example.test/v1', aspect_ratio: ''}}});
    const config = await readAgentConfig(root);
    expect(config.sections.video.aspect_ratio).toBe('9:16');
  });

  it('round-trips tos data-subscription fields', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {tos: {bucket: 'vimax-assets', region: 'cn-beijing', prefix: 'vimax/'}}});
    const config = await readAgentConfig(root);
    expect(config.sections.tos).toMatchObject({bucket: 'vimax-assets', region: 'cn-beijing', prefix: 'vimax/'});
    const saved = await readFile(path.join(root, 'configs', 'agent.local.yaml'), 'utf8');
    expect(saved).toContain('bucket: vimax-assets');
    expect(saved).toContain('prefix: vimax/');
  });

  it('round-trips video subtitles toggle', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {video: {model: 'm', base_url: 'https://example.test/v1', subtitles: '1'}}});
    let config = await readAgentConfig(root);
    expect(config.sections.video.subtitles).toBe('1');
    await saveAgentConfig(root, {sections: {video: {model: 'm', base_url: 'https://example.test/v1', subtitles: '0'}}});
    config = await readAgentConfig(root);
    expect(config.sections.video.subtitles).toBe('0');
    const saved = await readFile(path.join(root, 'configs', 'agent.local.yaml'), 'utf8');
    expect(saved).toContain('subtitles: false');
  });

  it('round-trips llm output budget and temperature as numbers', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {llm: {max_tokens: '196608', temperature: '0'}}});
    const config = await readAgentConfig(root);
    expect(config.sections.llm.max_tokens).toBe('196608');
    expect(config.sections.llm.temperature).toBe('0');
    const saved = await readFile(path.join(root, 'configs', 'agent.local.yaml'), 'utf8');
    expect(saved).toContain('max_tokens: 196608');
    expect(saved).toContain('temperature: 0');
    expect(saved).not.toContain("max_tokens: '196608'");
  });

  it('accepts numeric JSON values for the llm budget', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {llm: {max_tokens: 196608, temperature: 0.3}}});
    const config = await readAgentConfig(root);
    expect(config.sections.llm.max_tokens).toBe('196608');
    expect(config.sections.llm.temperature).toBe('0.3');
  });

  it('keeps the stored llm budget when blank is submitted', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {llm: {max_tokens: '196608'}}});
    await saveAgentConfig(root, {sections: {llm: {max_tokens: ''}}});
    const config = await readAgentConfig(root);
    expect(config.sections.llm.max_tokens).toBe('196608');
  });

  it('rejects an out-of-range output budget', async () => {
    const root = await fixture();
    await expect(saveAgentConfig(root, {sections: {llm: {max_tokens: '1000000'}}})).rejects.toThrow(/393216/);
    await expect(saveAgentConfig(root, {sections: {llm: {max_tokens: 'abc'}}})).rejects.toThrow(/max_tokens/);
  });

  it('rejects an out-of-range temperature', async () => {
    const root = await fixture();
    await expect(saveAgentConfig(root, {sections: {llm: {temperature: '3'}}})).rejects.toThrow(/temperature/);
    await expect(saveAgentConfig(root, {sections: {llm: {temperature: '-1'}}})).rejects.toThrow(/temperature/);
  });

  it('leaves subtitles untouched when blank is submitted', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {video: {model: 'm', base_url: 'https://example.test/v1', subtitles: '0'}}});
    await saveAgentConfig(root, {sections: {video: {model: 'm', base_url: 'https://example.test/v1', subtitles: ''}}});
    const config = await readAgentConfig(root);
    expect(config.sections.video.subtitles).toBe('0');
  });
  it('round-trips the storyboard tuning knobs as numbers', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {storyboard: {shots_per_batch: '24', batch_concurrency: '2'}}});
    const config = await readAgentConfig(root);
    expect(config.sections.storyboard.shots_per_batch).toBe('24');
    expect(config.sections.storyboard.batch_concurrency).toBe('2');
    const saved = await readFile(path.join(root, 'configs', 'agent.local.yaml'), 'utf8');
    expect(saved).toContain('shots_per_batch: 24');
    expect(saved).toContain('batch_concurrency: 2');
    expect(saved).not.toContain("shots_per_batch: '24'");
  });

  it('rejects storyboard knobs outside their ranges', async () => {
    const root = await fixture();
    await expect(saveAgentConfig(root, {sections: {storyboard: {shots_per_batch: '400'}}})).rejects.toThrow(/2 ~ 200/);
    await expect(saveAgentConfig(root, {sections: {storyboard: {batch_concurrency: '99'}}})).rejects.toThrow(/1 ~ 8/);
    await expect(saveAgentConfig(root, {sections: {storyboard: {shots_per_batch: 'abc'}}})).rejects.toThrow(/shots_per_batch/);
  });

  it('stores the thinking effort and can clear it back to the model default', async () => {
    const root = await fixture();
    await saveAgentConfig(root, {sections: {llm: {reasoning_effort: 'none'}}});
    let config = await readAgentConfig(root);
    expect(config.sections.llm.reasoning_effort).toBe('none');
    await saveAgentConfig(root, {sections: {llm: {reasoning_effort: 'default'}}});
    config = await readAgentConfig(root);
    expect(config.sections.llm.reasoning_effort).toBe('');
    await expect(saveAgentConfig(root, {sections: {llm: {reasoning_effort: '超快'}}})).rejects.toThrow(/reasoning_effort/);
  });
});
