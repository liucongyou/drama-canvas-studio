export type ChangelogEntry = {
  version: string;
  date: string;
  title: string;
  changes: string[];
};

// 每次发布前端改动时：先升级 APP_VERSION，再把同版本记录放到数组第一项。
// changelog.test.ts 会阻止版本升级但漏写更新记录。
export const APP_VERSION = '2026.10.29.2';
export const RELEASE_SEEN_KEY = 'yingjie-release-seen';

export const CHANGELOG: ChangelogEntry[] = [
  {
    version: '2026.10.29.2',
    date: '2026-10-29',
    title: '手动集数管理与更新记录',
    changes: [
      '画布列表新增手动创建集数入口，支持输入集编号和自定义标题。',
      '手动新建的集数会写入项目载荷，后续在该集添加的媒体节点自动归属当前集。',
      '设置菜单新增前端更新记录，并在出现新版本时显示提示标记。',
    ],
  },
  {
    version: '2026.10.29.1',
    date: '2026-10-29',
    title: '引用与操作稳定性',
    changes: [
      '手动连线、引用和项目连接关系改为持久化保存。',
      '修复删线后引用残留、重新连线被旧黑名单拦截的问题。',
      '修复快速复制撤销后节点复活，以及多标签页连线不同步的问题。',
    ],
  },
];
