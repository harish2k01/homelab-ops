import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { updateVersion, validateInputs } from './update-version.mjs';

test('rejects path traversal, expressions, malformed tags and digests', () => {
  for (const app of ['../portfolio', 'a/b', 'a;cmd', '', undefined])
    assert.throws(() => validateInputs(app, 'v1.0.0'));
  for (const tag of ['v1;cmd', 'a'.repeat(129), '$(cmd)', '', 'a\nb'])
    assert.throws(() => validateInputs('portfolio-next', tag));
  assert.throws(() => validateInputs('portfolio-next', 'v1.0.0', 'sha256:wrong'));
  validateInputs('portfolio-next', 'v1.0.0', 'sha256:' + 'a'.repeat(64));
});

test('same-version blog refresh changes only digest; identical updates are byte-for-byte no-ops', () => {
  const root = mkdtempSync(join(tmpdir(), 'portfolio-gitops-'));
  const folder = join(root, 'charts', 'portfolio-next');
  mkdirSync(folder, { recursive: true });
  const values = join(folder, 'values.yaml');
  writeFileSync(values, '# keep this comment\nimage:\n  repository: ghcr.io/harish2k01/portfolio-next\n  tag: v0.1.0\n  digest: ""\nhttpRoutes:\n  external:\n    hostnames: [harish2k01.xyz]\n');
  const update = digest => updateVersion({root, app:'portfolio-next', tag:'v0.1.0', digest, yq:process.env.YQ_BINARY || 'yq'});
  try {
    update('sha256:' + 'a'.repeat(64));
    const first = readFileSync(values,'utf8');
    update('sha256:' + 'a'.repeat(64));
    assert.equal(readFileSync(values,'utf8'),first);
    update('sha256:' + 'b'.repeat(64));
    const second = readFileSync(values,'utf8');
    assert.equal(second,first.replace('a'.repeat(64),'b'.repeat(64)));
    assert.match(second,/# keep this comment/);
    assert.match(second,/hostnames: \[harish2k01.xyz\]/);
    assert.ok(!readFileSync(values,'utf8').includes('appVersion'));
    // Local charts also update appVersion when callers intentionally deploy by tag.
    writeFileSync(join(folder,'Chart.yaml'),'apiVersion: v2\nname: portfolio-next\nversion: 0.1.0\nappVersion: v0.1.0\n');
    updateVersion({root,app:'portfolio-next',tag:'0.14.2',yq:process.env.YQ_BINARY || 'yq'});
    assert.ok(!readFileSync(values,'utf8').includes('digest:'));
    assert.match(readFileSync(join(folder,'Chart.yaml'),'utf8'),/appVersion: 0.14.2/);
  } finally { rmSync(root,{recursive:true,force:true}); }
});


test('personal local charts update image identity and appVersion without changing chart version', () => {
  for (const app of ['portfolio-next', 'portfolio-tracker']) {
    const root = mkdtempSync(join(tmpdir(), 'local-chart-image-'));
    try {
      const folder = join(root, 'charts', app);
      mkdirSync(folder, { recursive: true });
      writeFileSync(join(folder, 'values.yaml'), `image:\n  repository: ghcr.io/harish2k01/${app}\n  tag: v1.0.0\n  digest: ""\nhttpRoutes:\n  internal:\n    enabled: true\n    hostnames: [app.internal.example.com]\n`);
      writeFileSync(join(folder, 'Chart.yaml'), `apiVersion: v2\nname: ${app}\nversion: 0.3.0\nappVersion: v1.0.0\n`);
      const digest = 'sha256:' + 'c'.repeat(64);
      updateVersion({ root, app, tag: 'v1.1.0', digest, yq: process.env.YQ_BINARY || 'yq' });
      const metadata = readFileSync(join(folder, 'Chart.yaml'), 'utf8');
      const values = readFileSync(join(folder, 'values.yaml'), 'utf8');
      assert.match(metadata, /version: 0.3.0/);
      assert.match(metadata, /appVersion: v1.1.0/);
      assert.match(values, /tag: v1.1.0/);
      assert.ok(values.includes(digest));
      assert.match(values, /hostnames: \[app.internal.example.com\]/);
    } finally { rmSync(root, { recursive: true, force: true }); }
  }
});
