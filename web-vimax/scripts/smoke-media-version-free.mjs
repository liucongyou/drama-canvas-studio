import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

/**
 * Free end-to-end regression for local media versions.
 *
 * It exercises the real card menu and history controls against a temporary
 * project, then verifies durable bytes and the active version after reload.
 * Generation, provider-test, and paid recovery endpoints are aborted before
 * they can leave the browser.
 */
const apiBase = process.env.CANVAS_API_BASE || 'http://127.0.0.1:8890';
const webBase = process.env.CANVAS_BASE || 'http://127.0.0.1:8891';
const username = process.env.CANVAS_USERNAME || 'admin';
const password = process.env.CANVAS_PASSWORD || 'TmpCanvas2026!';
const tenantId = process.env.CANVAS_TENANT || 'owner';
const sourceImage = process.env.MEDIA_VERSION_IMAGE
  || 'D:/短剧/铁西硬汉/图像生成-1794150869463.png';
const root = path.resolve(import.meta.dirname, '..', '..');
const uploadsDir = path.join(root, 'data', 'uploads');
const projectTitle = `本地上传与历史版本免费回归-${Date.now()}`;
const episodeId = 'EP001';
const nodeId = 'media-version-free-regression';
const initialName = `media-version-initial-${Date.now()}.png`;
const initialUrl = `/uploads/${initialName}`;
const initialBytes = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
  + '+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);
const sourceBytes = await fs.readFile(sourceImage);
const sourceSha = crypto.createHash('sha256').update(sourceBytes).digest('hex');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function login() {
  const response = await fetch(`${apiBase}/api/auth/login`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password, tenantId}),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`登录失败 ${response.status}: ${JSON.stringify(payload)}`);
  }
  const setCookie = response.headers.get('set-cookie') || '';
  const match = /(?:^|,\s*)canvas_session=([^;]+)/.exec(setCookie);
  if (!match) throw new Error(`登录响应缺少 canvas_session: ${setCookie}`);
  return {token: decodeURIComponent(match[1]), payload};
}

const auth = await login();

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Cookie: `canvas_session=${auth.token}`,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  }
  return payload;
}

await fs.mkdir(uploadsDir, {recursive: true});
await fs.writeFile(path.join(uploadsDir, initialName), initialBytes);

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: projectTitle}),
});
const projectId = created.id;
let uploadedUrl = '';

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      episodeId,
      nodes: [{
        id: nodeId,
        type: 'image',
        title: '历史版本免费回归',
        position: {x: 260, y: 120},
        metadata: {
          prompt: '免费回归，不触发生成',
          status: 'done',
          content: initialUrl,
          source: {type: 'local', fileName: initialName},
          params: {
            ratio: '16:9',
            activeVersionId: 'image:initial-free-regression',
            mediaVersionIndex: 0,
            mediaVersions: [{
              versionId: 'image:initial-free-regression',
              url: initialUrl,
              sourceType: 'local',
              fileName: initialName,
            }],
          },
        },
      }],
    }),
  });

  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });

  try {
    const page = await browser.newPage();
    const pageErrors = [];
    const consoleErrors = [];
    const failedApiResponses = [];
    const blockedCostRequests = [];
    const versionResponses = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error' && !message.text().includes('favicon')) {
        consoleErrors.push(message.text());
      }
    });
    page.on('response', async (response) => {
      const url = response.url();
      if (url.includes('/local-version') || url.includes('/select-version')) {
        versionResponses.push({
          url,
          status: response.status(),
          body: await response.text().catch(() => ''),
        });
      }
      if (url.startsWith(apiBase) && response.status() >= 400) {
        failedApiResponses.push({
          url,
          status: response.status(),
          body: await response.text().catch(() => ''),
        });
      }
    });
    const workspaceSyncEvents = [];
    page.on('request', (request) => {
      const url = request.url();
      if (
        url.includes('/workspace-nodes')
        || url.includes('/workspace-facts')
        || url.includes('/nodes')
      ) {
        workspaceSyncEvents.push({
          phase: 'start',
          at: Date.now(),
          method: request.method(),
          url,
          body: request.postData() || '',
        });
      }
    });
    page.on('response', async (response) => {
      const url = response.url();
      if (
        url.includes('/workspace-nodes')
        || url.includes('/workspace-facts')
        || url.includes('/nodes')
      ) {
        workspaceSyncEvents.push({
          phase: 'end',
          at: Date.now(),
          method: response.request().method(),
          url,
          status: response.status(),
          body: await response.text().catch(() => ''),
        });
      }
    });
    await page.setRequestInterception(true);
    page.on('request', (request) => {
      const url = request.url();
      if (
        /\/generate(?:[/?]|$)/.test(url)
        || /\/config\/test(?:[/?]|$)/.test(url)
        || /\/recover-media(?:[/?]|$)/.test(url)
      ) {
        blockedCostRequests.push({url, method: request.method()});
        void request.abort('blockedbyclient');
        return;
      }
      void request.continue();
    });
    await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
    const layoutChecks = [];
    const checkVersionHistoryLayout = async (label) => {
      const count = `[data-id="media-${nodeId}"] .canvas-media-version-count`;
      await page.click('[aria-label="适应视图"]');
      await sleep(450);
      const position = await page.$eval(count, (button) => {
        const rect = button.getBoundingClientRect();
        return {
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
          width: rect.width,
          height: rect.height,
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
          visible: rect.width > 0
            && rect.height > 0
            && rect.left >= 0
            && rect.top >= 0
            && rect.right <= window.innerWidth
            && rect.bottom <= window.innerHeight,
        };
      });
      if (!position.visible) {
        throw new Error(`${label} 版本按钮不在视口：${JSON.stringify(position)}`);
      }
      await page.mouse.click(position.x, position.y);
      await page.waitForSelector(
        `[data-media-version-menu="${nodeId}"]`,
        {timeout: 10_000},
      );
      const layout = await page.$eval(
        `[data-media-version-menu="${nodeId}"]`,
        (menu) => {
          const rect = menu.getBoundingClientRect();
          return {
            left: Math.round(rect.left),
            top: Math.round(rect.top),
            right: Math.round(rect.right),
            bottom: Math.round(rect.bottom),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            viewportWidth: window.innerWidth,
            viewportHeight: window.innerHeight,
            withinViewport: rect.left >= 0
              && rect.top >= 0
              && rect.right <= window.innerWidth
              && rect.bottom <= window.innerHeight,
            readableWidth: rect.width >= 230,
          };
        },
      );
      if (!layout.withinViewport || !layout.readableWidth) {
        throw new Error(`${label} 历史版本浮层布局不合格：${JSON.stringify(layout)}`);
      }
      layoutChecks.push({label, ...layout});
      await page.click(count);
      await page.waitForSelector(
        `[data-media-version-menu="${nodeId}"]`,
        {hidden: true, timeout: 10_000},
      );
    };
    await page.setCookie({
      name: 'canvas_session',
      value: auth.token,
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    });
    await page.goto(
      `${webBase}/?ep=${episodeId}#${encodeURIComponent(projectId)}`,
      {waitUntil: 'domcontentloaded', timeout: 60_000},
    );
    await page.waitForSelector(`[data-id="media-${nodeId}"] .canvas-media-card`, {
      timeout: 60_000,
    });
    const initialApiProject = await api(`/api/projects/${projectId}`);
    const initialApiNode = initialApiProject.payload.nodes.find((node) => node.id === nodeId);
    const initialVersionCount = initialApiNode?.metadata?.params?.mediaVersions?.length;
    if (initialVersionCount !== 1) {
      throw new Error(`初始版本夹具应为 1 个，实际 ${initialVersionCount}；节点 ${JSON.stringify(initialApiNode?.metadata)}`);
    }

    await page.click(`[data-id="media-${nodeId}"] .canvas-media-foot-more > button`);
    await page.waitForSelector('.canvas-media-foot-pop', {timeout: 10_000});
    await page.evaluate(() => {
      const button = [...document.querySelectorAll('.canvas-media-foot-pop button')]
        .find((item) => (item.textContent || '').includes('从本地添加版本')
          || (item.textContent || '').includes('导入本地文件'));
      if (!(button instanceof HTMLElement)) throw new Error('从本地添加版本菜单不存在');
      button.click();
    });

    const uploadInput = await page.evaluateHandle(() => {
      const inputs = [...document.querySelectorAll('input[type="file"]')];
      // The card-level picker includes extensions such as .avif. The prompt
      // modal has a separate hidden image input that only accepts image/*.
      return inputs.find((input) => (
        !input.multiple
        && String(input.accept || '').includes('.avif')
      )) || null;
    });
    const uploadElement = uploadInput.asElement();
    if (!uploadElement) throw new Error('节点本地上传 input 不存在');
    await uploadElement.uploadFile(sourceImage);
    // The old image is already visible, so waiting for "some image" can pass
    // before the upload has even been persisted. Wait for the version pointer
    // to move, then verify the active version and durable bytes through the API.
    await page.waitForFunction(
      (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
        ?.textContent?.trim() === '2/2',
      {timeout: 90_000},
      nodeId,
    );

    const afterUploadProject = await api(`/api/projects/${projectId}`);
    const afterUploadNode = afterUploadProject.payload.nodes.find((node) => node.id === nodeId);
    const afterUploadParams = afterUploadNode?.metadata?.params || {};
    const afterUploadVersions = afterUploadParams.mediaVersions || [];
    if (afterUploadVersions.length !== 2) {
      throw new Error(`本地上传后应有 2 个版本，实际 ${afterUploadVersions.length}；节点 ${JSON.stringify(afterUploadNode?.metadata)}`);
    }
    const activeVersion = afterUploadVersions.find(
      (version) => version.versionId === afterUploadParams.activeVersionId,
    );
    if (
      afterUploadParams.mediaVersionIndex !== 1
      || !String(afterUploadParams.activeVersionId || '').startsWith('image:local:')
      || activeVersion?.sha256 !== sourceSha
      || activeVersion?.url !== afterUploadNode?.metadata?.content
    ) {
      throw new Error(`本地上传未成为当前引用：${JSON.stringify(afterUploadParams)}`);
    }
    uploadedUrl = String(afterUploadNode.metadata.content || '');
    const downloadUrl = /^https?:\/\//i.test(uploadedUrl)
      ? uploadedUrl
      : apiBase + uploadedUrl;
    const downloaded = await fetch(downloadUrl, {
      headers: /^https?:\/\//i.test(uploadedUrl)
        ? {}
        : {Cookie: `canvas_session=${auth.token}`},
    });
    if (!downloaded.ok) throw new Error(`上传文件回读失败：HTTP ${downloaded.status}`);
    const readbackSha = crypto
      .createHash('sha256')
      .update(Buffer.from(await downloaded.arrayBuffer()))
      .digest('hex');
    if (readbackSha !== sourceSha) {
      throw new Error(`上传文件字节不一致：${readbackSha} != ${sourceSha}`);
    }

    const versionLabel = async () => page.$eval(
      `[data-id="media-${nodeId}"] .canvas-media-version-count`,
      (node) => (node.textContent || '').trim(),
    );
    if (await versionLabel() !== '2/2') {
      throw new Error(`上传后版本标签应为 2/2，实际 ${await versionLabel()}`);
    }

    const versionCount = `[data-id="media-${nodeId}"] .canvas-media-version-count`;
    await page.click(versionCount);
    await page.waitForSelector(
      `[data-media-version-menu="${nodeId}"]`,
      {timeout: 10_000},
    );
    const historyMenu = await page.$eval(
      `[data-media-version-menu="${nodeId}"]`,
      (menu) => {
        const rect = menu.getBoundingClientRect();
        const items = [...menu.querySelectorAll('.canvas-media-version-item')];
        return {
          count: items.length,
          activeCount: items.filter((item) => item.classList.contains('is-active')).length,
          thumbCount: items.filter((item) => item.querySelector('img')).length,
          fullyVisible: rect.left >= 0
            && rect.top >= 0
            && rect.right <= window.innerWidth
            && rect.bottom <= window.innerHeight,
          items: items.map((item) => ({
            text: (item.textContent || '').trim(),
            disabled: item.disabled,
          })),
        };
      },
    );
    if (
      historyMenu.count !== 2
      || historyMenu.activeCount !== 1
      || historyMenu.thumbCount !== 2
      || !historyMenu.fullyVisible
    ) {
      throw new Error(`历史版本列表不完整：${JSON.stringify(historyMenu)}`);
    }
    layoutChecks.push({label: 'desktop-1440', ...historyMenu});
    const versionItems = `[data-media-version-menu="${nodeId}"] .canvas-media-version-item`;
    await page.click(`${versionItems}:nth-child(2)`);
    await page.waitForFunction(
      (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
        ?.textContent?.trim() === '1/2',
      {timeout: 20_000},
      nodeId,
    );
    await page.click(versionCount);
    await page.waitForSelector(
      `[data-media-version-menu="${nodeId}"]`,
      {timeout: 10_000},
    );
    await page.click(`${versionItems}:nth-child(3)`);
    await page.waitForFunction(
      (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
        ?.textContent?.trim() === '2/2',
      {timeout: 20_000},
      nodeId,
    );

    const previous = `[data-id="media-${nodeId}"] [aria-label="上一个生成版本"]`;
    const next = `[data-id="media-${nodeId}"] [aria-label="下一个生成版本"]`;
    const beforePreviousProject = await api(`/api/projects/${projectId}`);
    const beforePreviousNode = beforePreviousProject.payload.nodes.find((node) => node.id === nodeId);
    const beforePreviousLabel = await versionLabel();
    await page.click(previous);
    let waitError = '';
    try {
      await page.waitForFunction(
        (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
          ?.textContent?.trim() === '1/2',
        {timeout: 20_000},
        nodeId,
      );
    } catch (error) {
      waitError = error instanceof Error ? error.message : String(error);
    }
    const afterPreviousLabel = await versionLabel();
    const afterPreviousProject = await api(`/api/projects/${projectId}`);
    const afterPreviousNode = afterPreviousProject.payload.nodes.find((node) => node.id === nodeId);
    const latestSelectionResponse = versionResponses
      .filter((item) => item.url.includes('/select-version'))
      .at(-1);
    if (afterPreviousLabel !== '1/2') {
      const clickDiagnostic = await page.$eval(previous, (button) => {
        const rect = button.getBoundingClientRect();
        const atPoint = document.elementFromPoint(
          rect.left + rect.width / 2,
          rect.top + rect.height / 2,
        );
        return {
          disabled: button.disabled,
          pointerEvents: getComputedStyle(button).pointerEvents,
          visibility: getComputedStyle(button).visibility,
          display: getComputedStyle(button).display,
          rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
          atPoint: atPoint?.outerHTML?.slice(0, 500) || '',
          toast: [...document.querySelectorAll('.toast, .canvas-toast, [role="status"]')]
            .map((item) => item.textContent || '').join(' | '),
        };
      });
      await page.$eval(previous, (button) => button.click());
      await sleep(1500);
      const afterScriptClickLabel = await versionLabel();
      const renderedVersions = await page.evaluate((id) => {
        const shell = document.querySelector(`[data-id="media-${id}"]`);
        return {
          cardHtml: shell?.innerHTML?.slice(0, 4000) || '',
          images: [...(shell?.querySelectorAll('img') || [])].map((image) => ({
            src: image.getAttribute('src'),
            currentSrc: image.currentSrc,
            complete: image.complete,
            naturalWidth: image.naturalWidth,
          })),
        };
      }, nodeId);
      throw new Error(`点击上一版后应为 1/2，实际 ${afterPreviousLabel}，JS点击后 ${afterScriptClickLabel}；等待错误 ${waitError}；诊断 ${JSON.stringify({
        beforePreviousLabel,
        beforePreviousParams: beforePreviousNode?.metadata?.params,
        beforePreviousMedia: beforePreviousNode?.metadata?.content,
        afterPreviousParams: afterPreviousNode?.metadata?.params,
        afterPreviousMedia: afterPreviousNode?.metadata?.content,
        clickDiagnostic,
        latestSelectionResponse,
        versionResponses,
        workspaceSyncEvents: workspaceSyncEvents.slice(-30),
        renderedVersions,
        afterUploadVersions,
      })}`);
    }
    await page.click(next);
    await page.waitForFunction(
      (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
        ?.textContent?.trim() === '2/2',
      {timeout: 20_000},
      nodeId,
    );

    // Twenty clicks is an even count, so coalescing must end on 2/2. More
    // importantly, every accepted request must remain 200; a stale timestamp
    // conflict here is the exact user-visible bug this regression locks down.
    for (let index = 0; index < 20; index += 1) {
      await page.click(next);
    }
    await page.waitForFunction(
      (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
        ?.textContent?.trim() === '2/2',
      {timeout: 30_000},
      nodeId,
    );
    await sleep(1500);

    const afterRapidProject = await api(`/api/projects/${projectId}`);
    const afterRapidNode = afterRapidProject.payload.nodes.find((node) => node.id === nodeId);
    const afterRapidParams = afterRapidNode?.metadata?.params || {};
    if (
      afterRapidParams.mediaVersionIndex !== 1
      || afterRapidParams.activeVersionId !== afterUploadParams.activeVersionId
      || afterRapidNode?.metadata?.content !== uploadedUrl
    ) {
      throw new Error(`快速切换最终指针错误：${JSON.stringify(afterRapidParams)}`);
    }

    await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
    await page.waitForSelector(`[data-id="media-${nodeId}"] .canvas-media-version-nav`, {
      timeout: 60_000,
    });
    await page.waitForFunction(
      (id) => {
        const image = document.querySelector(`[data-id="media-${id}"] .canvas-media-card img`);
        return Boolean(image && image.complete && image.naturalWidth > 0);
      },
      {timeout: 60_000},
      nodeId,
    );
    if (await versionLabel() !== '2/2') {
      throw new Error(`刷新后版本未保持 2/2，实际 ${await versionLabel()}`);
    }
    await page.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
    await sleep(500);
    await page.evaluate(() => { window.localStorage.clear(); });
    await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
    await page.waitForSelector(`[data-id="media-${nodeId}"] .canvas-media-card`, {
      timeout: 60_000,
    });
    await checkVersionHistoryLayout('mobile-390');
    await page.setViewport({width: 1920, height: 1080, deviceScaleFactor: 1});
    await sleep(500);
    await page.evaluate(() => { window.localStorage.clear(); });
    await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
    await page.waitForSelector(`[data-id="media-${nodeId}"] .canvas-media-card`, {
      timeout: 60_000,
    });
    await checkVersionHistoryLayout('desktop-1920');
    const failedVersionResponses = versionResponses.filter((item) => item.status !== 200);
    if (failedVersionResponses.length) {
      throw new Error(`版本接口出现失败响应：${JSON.stringify(failedVersionResponses)}`);
    }
    if (failedApiResponses.length) {
      throw new Error(`浏览器出现失败 API 响应：${JSON.stringify(failedApiResponses)}`);
    }
    if (blockedCostRequests.length) {
      throw new Error(`检测到付费请求尝试：${JSON.stringify(blockedCostRequests)}`);
    }
    if (pageErrors.length || consoleErrors.length) {
      throw new Error(`浏览器错误：${JSON.stringify({pageErrors, consoleErrors})}`);
    }

    console.log(JSON.stringify({
      ok: true,
      projectId,
      nodeId,
      sourceBytes: sourceBytes.length,
      sourceSha256: sourceSha,
      uploadedUrl,
      uploadResponseCount: versionResponses.filter((item) => item.url.includes('/local-version')).length,
      selectionResponseCount: versionResponses.filter((item) => item.url.includes('/select-version')).length,
      finalVersion: await versionLabel(),
      layoutChecks,
      blockedCostRequestCount: blockedCostRequests.length,
      failedApiResponseCount: failedApiResponses.length,
      pageErrors,
      consoleErrors,
    }, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  if (projectId) {
    await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  }
  await fs.unlink(path.join(uploadsDir, initialName)).catch(() => {});
  if (uploadedUrl.startsWith('/uploads/')) {
    await fs.unlink(path.join(uploadsDir, path.basename(uploadedUrl))).catch(() => {});
  }
}
