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
  writeFileSync(values, '# keep this comment\nimage:\n  repository: ghcr.io/harish2k01/portfolio-next\n  tag: v0.1.0\n  digest: ""\nhttpRoute:\n  hostnames: [harish2k01.xyz]\n');
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
    // Old Portfolio callers supply tags only and have a local Chart.yaml.
    writeFileSync(join(folder,'Chart.yaml'),'apiVersion: v2\nname: portfolio-next\nversion: 0.1.0\nappVersion: v0.1.0\n');
    updateVersion({root,app:'portfolio-next',tag:'0.14.2',yq:process.env.YQ_BINARY || 'yq'});
    assert.ok(!readFileSync(values,'utf8').includes('digest:'));
    assert.match(readFileSync(join(folder,'Chart.yaml'),'utf8'),/appVersion: 0.14.2/);
  } finally { rmSync(root,{recursive:true,force:true}); }
});
