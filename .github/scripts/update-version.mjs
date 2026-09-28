import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export function validateInputs(app, tag, digest = '') {
  if (typeof app !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(app)) throw new Error('Invalid app name');
  if (typeof tag !== 'string' || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(tag)) throw new Error('Invalid image tag');
  if (typeof digest !== 'string' || (digest && !/^sha256:[a-f0-9]{64}$/.test(digest))) throw new Error('Invalid image digest');
}

export function updateVersion({ root = '.', app, tag, digest = '', yq = 'yq' }) {
  validateInputs(app, tag, digest);
  const values = resolve(root, 'charts', app, 'values.yaml');
  const chart = resolve(root, 'charts', app, 'Chart.yaml');
  if (!existsSync(values)) throw new Error(`No values file for ${app}`);
  const env = { ...process.env, IMAGE_TAG: tag, IMAGE_DIGEST: digest };
  const run = (...args) => execFileSync(yq, args, { env, encoding: 'utf8' });
  const image = JSON.parse(run('-o=json', '.image', values));
  if (!image || typeof image.repository !== 'string') throw new Error('Values must define image.repository');
  // strenv treats input strings as data, never as yq expressions.
  // Clear an old digest for legacy callers that intentionally deploy only a tag.
  const expression = '.image.tag = strenv(IMAGE_TAG) | ' +
    (digest ? '.image.digest = strenv(IMAGE_DIGEST)' : 'del(.image.digest)');
  run('-i', expression, values);
  if (existsSync(chart)) run('-i', '.appVersion = strenv(IMAGE_TAG)', chart);
  return { values, chart: existsSync(chart) ? chart : null };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  updateVersion({ app: process.env.APP_NAME, tag: process.env.IMAGE_TAG, digest: process.env.IMAGE_DIGEST || '' });
}
