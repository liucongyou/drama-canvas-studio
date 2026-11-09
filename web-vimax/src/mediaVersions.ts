// 媒体历史版本索引解析（无依赖纯函数，便于单测）。
//
// 卡片上的「‹ N/M ›」用哪个版本做当前版？看似只要把 mediaUrl 和版本列表里的
// URL 对上就行，实际历史数据里同一张图常出现多条：
//   · 恢复下载会重新落一个本地文件名，再记一版；
//   · TOS 原始图会以另一条（带签名的）URL 追加一版；
//   · 早期数据在重复生成时可能把同一 URL 记了多次。
// 这时 URL 不是版本的唯一标识，只取 findIndex 的第一条会让所有重复项都指向
// 第 0 版：界面停在 1/3，点「下一版」把 mediaUrl 写成同一个地址，编号也不动，
// 永远到不了后面的版本（线上图36 的现场）。
//
// 规则：
//   1. 有效 activeVersionId 是唯一权威，URL 和索引都不得覆盖它；
//   2. 只有历史数据找不到有效 ID 时，才按 URL / 索引兼容；
//   3. 仍无法定位时回退到最新版。

export type MediaVersionLike = {
  versionId?: string;
  url: string;
};

export type MediaVersionPointer = {
  activeVersionId?: string;
  index?: number;
};

export function resolveMediaVersionIndex(
  currentUrl: string,
  versions: MediaVersionLike[],
  pointer: MediaVersionPointer = {},
): number {
  if (!versions.length) return 0;
  const activeVersionId = String(pointer.activeVersionId || '').trim();
  if (activeVersionId) {
    const idIndex = versions.findIndex(
      (version) => String(version.versionId || '').trim() === activeVersionId,
    );
    if (idIndex >= 0) return idIndex;
  }

  const url = String(currentUrl || '').trim();
  const matchingIndexes: number[] = [];
  if (url) {
    versions.forEach((version, index) => {
      if (version.url === url) matchingIndexes.push(index);
    });
  }
  if (matchingIndexes.length === 1) return matchingIndexes[0];

  const configured = Number(pointer.index ?? NaN);

  if (matchingIndexes.length > 1) {
    if (Number.isFinite(configured)) {
      const index = Math.trunc(configured);
      if (index >= 0 && matchingIndexes.includes(index)) return index;
    }
    return matchingIndexes[matchingIndexes.length - 1];
  }

  if (Number.isFinite(configured)) {
    return Math.max(0, Math.min(
      Math.trunc(configured),
      versions.length - 1,
    ));
  }
  return versions.length - 1;
}
