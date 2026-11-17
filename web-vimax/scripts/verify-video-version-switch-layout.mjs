import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const webBase = 'http://127.0.0.1:8891';
const password = process.env.CANVAS_PASSWORD || 'TmpCanvas2026!';
const videoA = '/uploads/generated-1793362871-a555b1e3b7.mp4';
const videoB = '/uploads/generated-1793002913-5008b1c5ae.mp4';
const nodeId = 'video-version-regression';
const projectTitle = `视频版本切换回归-${Date.now()}`;

const loginResponse = await fetch(`${apiBase}/api/auth/login`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: 'admin', password, tenant: 'owner'}),
});
if (!loginResponse.ok) throw new Error(`login failed: ${loginResponse.status}`);
const sessionToken = (loginResponse.headers.get('set-cookie') || '')
  .split(';', 1)[0]
  .split('=', 2)[1];
if (!sessionToken) throw new Error('missing canvas session');

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Cookie: `canvas_session=${sessionToken}`,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  }
  return payload;
}

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: projectTitle}),
});
const projectId = created.id;

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      episodeId: 'EP001',
      nodes: [{
        id: nodeId,
        type: 'video',
        title: '视频历史版本回归',
        position: {x: 320, y: 220},
        metadata: {
          prompt: '视频历史版本切换',
          status: 'done',
          content: videoA,
          episodeId: 'EP001',
          source: {type: 'generated'},
          params: {
            ratio: '16:9',
            provider: 'ark',
            model: 'doubao-seedance-2-0',
            activeVersionId: 'video:v1',
            mediaVersionIndex: 0,
            mediaVersions: [
              {
                versionId: 'video:v1',
                url: videoA,
                sourceType: 'generated',
                mediaAvailability: 'available',
              },
              {
                versionId: 'video:v2',
                url: videoB,
                sourceType: 'generated',
                mediaAvailability: 'available',
              },
            ],
          },
        },
      }],
    }),
  });

  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    const versionResponses = [];
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`console: ${message.text()}`);
    });
    page.on('response', async (response) => {
      if (response.url().includes(`/nodes/${nodeId}/select-version`)) {
        versionResponses.push({
          status: response.status(),
          body: await response.text().catch(() => ''),
        });
      }
    });
    await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
    await page.setCookie({
      name: 'canvas_session',
      value: sessionToken,
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    });

    const openProject = async () => {
      await page.goto(`${webBase}/?ep=EP001#${projectId}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60_000,
      });
      await page.waitForSelector(`[data-id="media-${nodeId}"] .canvas-media-version-nav`, {
        timeout: 60_000,
      });
      await new Promise((resolve) => setTimeout(resolve, 500));
    };
    const versionLabel = () => page.$eval(
      `[data-id="media-${nodeId}"] .canvas-media-version-count`,
      (element) => (element.textContent || '').trim(),
    );
    const openMenu = async (label) => {
      await page.click(`[data-id="media-${nodeId}"] .canvas-media-version-count`);
      await page.waitForSelector(`[data-media-version-menu="${nodeId}"]`, {timeout: 10_000});
      const layout = await page.$eval(
        `[data-media-version-menu="${nodeId}"]`,
        (menu) => {
          const rect = menu.getBoundingClientRect();
          return {
            left: rect.left,
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom,
            width: rect.width,
            height: rect.height,
            itemCount: menu.querySelectorAll('.canvas-media-version-item').length,
            activeCount: menu.querySelectorAll('.canvas-media-version-item.is-active').length,
            items: [...menu.querySelectorAll('.canvas-media-version-item')].map((item) => ({
              text: (item.textContent || '').trim(),
              disabled: item.disabled,
              className: item.className,
            })),
            withinViewport: rect.left >= 0
              && rect.top >= 0
              && rect.right <= window.innerWidth
              && rect.bottom <= window.innerHeight,
          };
        },
      );
      if (layout.itemCount !== 2 || layout.activeCount !== 1 || !layout.withinViewport) {
        throw new Error(`${label} version menu invalid: ${JSON.stringify(layout)}`);
      }
      return layout;
    };
    const clickVersion = async (versionNumber) => {
      await page.evaluate(({id, number}) => {
        const items = [...document.querySelectorAll(
          `[data-media-version-menu="${id}"] .canvas-media-version-item`,
        )];
        const target = items.find((item) => (
          (item.textContent || '').includes(`版本 ${number}`)
        ));
        if (!(target instanceof HTMLElement)) {
          throw new Error(`version ${number} item not found`);
        }
        target.click();
      }, {id: nodeId, number: versionNumber});
    };

    await openProject();
    if (await versionLabel() !== '1/2') {
      throw new Error(`initial video version should be 1/2, got ${await versionLabel()}`);
    }
    const desktopMenu = await openMenu('desktop');
    await clickVersion(2);
    try {
      await page.waitForFunction(
        (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
          ?.textContent?.trim() === '2/2',
        {timeout: 20_000},
        nodeId,
      );
    } catch {
      const current = await api(`/api/projects/${projectId}`);
      const currentItem = current.payload.nodes.find((node) => node.id === nodeId);
      throw new Error(`desktop video switch failed: ${JSON.stringify({
        label: await versionLabel(),
        desktopMenu,
        versionResponses,
        metadata: currentItem?.metadata,
      })}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
    const persisted = await api(`/api/projects/${projectId}`);
    const savedNode = persisted.payload.nodes.find((node) => node.id === nodeId);
    if (
      savedNode?.metadata?.params?.activeVersionId !== 'video:v2'
      || savedNode?.metadata?.params?.mediaVersionIndex !== 1
      || savedNode?.metadata?.content !== videoB
    ) {
      throw new Error(`video version not persisted: ${JSON.stringify(savedNode?.metadata)}`);
    }

    await openProject();
    if (await versionLabel() !== '2/2') {
      throw new Error(`video version lost after refresh: ${await versionLabel()}`);
    }
    await page.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
    await new Promise((resolve) => setTimeout(resolve, 500));
    const mobileMenu = await openMenu('mobile');
    await clickVersion(1);
    await page.waitForFunction(
      (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
        ?.textContent?.trim() === '1/2',
      {timeout: 20_000},
      nodeId,
    );
    for (let index = 0; index < 9; index += 1) {
      await page.click(`[data-id="media-${nodeId}"] [aria-label="下一个生成版本"]`);
    }
    await page.waitForFunction(
      (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-count`)
        ?.textContent?.trim() === '2/2',
      {timeout: 30_000},
      nodeId,
    );
    const rapidDeadline = Date.now() + 15_000;
    let rapidNode = null;
    while (Date.now() < rapidDeadline) {
      const afterRapid = await api(`/api/projects/${projectId}`);
      rapidNode = afterRapid.payload.nodes.find((node) => node.id === nodeId);
      if (rapidNode?.metadata?.params?.activeVersionId === 'video:v2') break;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    if (rapidNode?.metadata?.params?.activeVersionId !== 'video:v2') {
      throw new Error(`rapid video version switch ended at ${rapidNode?.metadata?.params?.activeVersionId}: ${JSON.stringify({
        label: await versionLabel(),
        versionResponses,
      })}`);
    }
    if (errors.length) throw new Error(errors.join('; '));

    console.log(JSON.stringify({
      ok: true,
      projectId,
      nodeId,
      desktopMenu,
      mobileMenu,
      finalVersion: await versionLabel(),
      errors,
    }, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
