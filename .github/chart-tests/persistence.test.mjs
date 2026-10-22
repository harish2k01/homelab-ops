import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, cpSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { parse, parseAllDocuments, stringify } from 'yaml';

test('local tracker chart-version changes preserve complete PostgreSQL claim templates', () => {
  const dir = mkdtempSync(join(tmpdir(), 'local-tracker-claims-'));
  try {
    const chart = join(dir, 'portfolio-tracker');
    cpSync('charts/portfolio-tracker', chart, { recursive: true });
    const render = () => parseAllDocuments(execFileSync('helm', ['template', 'portfolio-tracker', chart, '--namespace', 'portfolio-tracker'], { encoding: 'utf8' })).map(d => d.toJSON()).filter(Boolean);
    const claims = docs => docs.find(d => d.kind === 'StatefulSet').spec.volumeClaimTemplates;
    const before = claims(render());
    assert.ok(before.length);
    const metadata = parse(readFileSync(join(chart, 'Chart.yaml'), 'utf8'));
    metadata.version = '99.0.0';
    writeFileSync(join(chart, 'Chart.yaml'), stringify(metadata));
    assert.deepEqual(claims(render()), before);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
