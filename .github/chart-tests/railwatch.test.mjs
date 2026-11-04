import test from 'node:test';
import assert from 'node:assert/strict';
import {render,rejects} from './helpers/render.mjs';

const deployment = (docs, role) => docs.find(d => d.kind === 'Deployment' && d.metadata.name === `railwatch-${role}`);
const main = (docs, role) => deployment(docs, role).spec.template.spec.containers[0];

test('RailWatch keeps the selectors and names of the workloads it adopts', () => {
  const docs = render('railwatch');
  for (const [kind, name, component] of [['Deployment','railwatch-frontend','frontend'],['Deployment','railwatch-backend','backend'],['StatefulSet','railwatch-postgres','database']]) {
    const workload = docs.find(d => d.kind === kind && d.metadata.name === name);
    assert.ok(workload, name);
    assert.deepEqual(workload.spec.selector.matchLabels, {'app.kubernetes.io/component': component, 'app.kubernetes.io/name': 'railwatch'});
  }
  assert.deepEqual(docs.filter(d => d.kind === 'Service').map(d => d.metadata.name).sort(), ['railwatch','railwatch-backend','railwatch-postgres']);
  assert.equal(docs.some(d => ['Secret','PersistentVolumeClaim'].includes(d.kind)), false, 'Secrets and claims stay external');
});

test('RailWatch pins one release image and keeps credentials out of the frontend', () => {
  const docs = render('railwatch');
  const backend = main(docs, 'backend'), frontend = main(docs, 'frontend');
  assert.match(backend.image, /^ghcr\.io\/harish2k01\/railwatch@sha256:[a-f0-9]{64}$/);
  assert.equal(frontend.image, backend.image);
  assert.equal(frontend.envFrom, undefined);
  assert.equal(backend.envFrom.find(e => e.secretRef).secretRef.name, 'railwatch-secrets');
  assert.equal(frontend.env.find(e => e.name === 'BACKEND_URL').value, 'http://railwatch-backend:3001');
  for (const c of [backend, frontend]) assert.equal(c.securityContext.readOnlyRootFilesystem, true);
});

test('RailWatch migrates with the release image before application pods roll out', () => {
  const docs = render('railwatch');
  const job = docs.find(d => d.kind === 'Job' && d.metadata.name === 'railwatch-migrate');
  assert.equal(job.metadata.annotations['argocd.argoproj.io/hook'], 'Sync');
  const wave = d => Number(d.metadata.annotations?.['argocd.argoproj.io/sync-wave'] ?? 0);
  const database = docs.find(d => d.kind === 'StatefulSet');
  assert.ok(wave(database) < wave(job) && wave(job) < wave(deployment(docs, 'backend')));
  const c = job.spec.template.spec.containers[0];
  assert.equal(c.image, main(docs, 'backend').image);
  assert.deepEqual(c.command.slice(-2), ['migrate', 'deploy']);
  assert.equal(job.spec.template.metadata.labels['app.kubernetes.io/component'], 'worker', 'database policy admits worker pods');
});

test('RailWatch optional parts can be turned off and a Secret name is required', () => {
  const docs = render('railwatch', ['migrations.enabled=false', 'backup.enabled=false', 'postgres.enabled=false', 'networkPolicy.enabled=false']);
  assert.deepEqual(docs.map(d => d.kind).filter(k => ['Job','CronJob','StatefulSet','NetworkPolicy'].includes(k)), []);
  rejects('railwatch', ['existingSecret='], /existingSecret must name/);
});
