import {spawnSync} from 'node:child_process';
import {mkdirSync} from 'node:fs';
import {resolve} from 'node:path';

const cacheDir = resolve('.tmp-build');
mkdirSync(cacheDir, {recursive: true});

const result = spawnSync(
  process.execPath,
  [resolve('node_modules/vite/bin/vite.js'), 'build'],
  {
    stdio: 'inherit',
    env: {...process.env, TEMP: cacheDir, TMP: cacheDir},
  },
);

if (result.error) throw result.error;
process.exit(result.status ?? 1);
