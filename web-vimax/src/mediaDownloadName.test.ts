import {describe, expect, it} from 'vitest';
import {mediaDownloadFilename} from './mediaDownloadName';

const base = {
  projectTitle: '铁西硬汉：第01集，三千块，买断一条命，投喂看板源',
  episodeId: 'EP002',
  nodeTitle: '段3 · 陈大强探出半个身子 · 塌方那回（8.1s · 3 镜）',
  kind: 'video' as const,
  url: '/uploads/generated.mp4',
};

describe('mediaDownloadFilename', () => {
  it('uses a short project, episode and segment name', () => {
    expect(mediaDownloadFilename(base)).toBe('铁西硬汉_EP02_段3.mp4');
  });

  it('adds the version only when downloading multiple versions', () => {
    expect(mediaDownloadFilename({...base, versionIndex: 1, versionTotal: 3}))
      .toBe('铁西硬汉_EP02_段3_版本2.mp4');
  });

  it('uses the URL extension and falls back by media kind', () => {
    expect(mediaDownloadFilename({...base, kind: 'audio', url: '/uploads/a.wav'}))
      .toBe('铁西硬汉_EP02_段3.wav');
    expect(mediaDownloadFilename({...base, kind: 'image', url: 'https://example.com/result'}))
      .toBe('铁西硬汉_EP02_段3.png');
  });

  it('keeps non-segment media identifiable without leaking a long title', () => {
    expect(mediaDownloadFilename({
      ...base,
      episodeId: '1',
      nodeTitle: '角色｜陈大强（图4）',
    })).toBe('铁西硬汉_EP01_角色｜陈大强（图4）.mp4');
  });

  it('replaces filesystem-reserved characters', () => {
    expect(mediaDownloadFilename({
      ...base,
      projectTitle: 'A/B:测试',
      nodeTitle: '段1 · 标题/副标题',
    })).toBe('A_B_EP02_段1.mp4');
  });
});
