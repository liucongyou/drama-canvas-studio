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
//   1. 唯一 URL 命中 → 以文件为准（兼容历史版本 ID 错位的数据）；
//   2. 多个 URL 命中 → 用 activeVersionId / mediaVersionIndex 消歧；
//   3. 指针落在命中项之外 → 取最后一条命中项（与后端「最新同名项为当前版」一致），
//      保证「下一版」总能继续往前走；
//   4. 没有 URL 命中（例如 TOS 重新签名）→ 按保存的指针，最后回退到最新版。

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
  const url = String(currentUrl || '').trim();
  const matchingIndexes: number[] = [];
  if (url) {
    versions.forEach((version, index) => {
      if (version.url === url) matchingIndexes.push(index);
    });
  }
  if (matchingIndexes.length === 1) return matchingIndexes[0];

  const configured = Number(pointer.index ?? NaN);
  const activeVersionId = String(pointer.activeVersionId || '').trim();

  if (matchingIndexes.length > 1) {
    if (activeVersionId) {
      const idIndex = versions.findIndex(
        (version) => version.versionId === activeVersionId,
      );
      if (idIndex >= 0 && matchingIndexes.includes(idIndex)) return idIndex;
    }
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
