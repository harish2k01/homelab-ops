import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { parseAllDocuments } from 'yaml';

const render = (...args) =>
  parseAllDocuments(execFileSync('helm', ['template', 'firefly-mcp', 'charts/firefly-mcp', '--namespace', 'firefly-iii', ...args], { encoding: 'utf8' }))
    .map(d => d.toJSON())
    .filter(Boolean);

test('firefly-mcp: one locked-down pod with the token from an external Secret', () => {
  const docs = render();
  const dep = docs.find(d => d.kind === 'Deployment');
  assert.equal(dep.spec.replicas, 1);
  assert.equal(dep.spec.strategy.type, 'Recreate');
  const pod = dep.spec.template.spec;
  assert.equal(pod.automountServiceAccountToken, false);
  const c = pod.containers[0];
  assert.equal(c.securityContext.readOnlyRootFilesystem, true);
  assert.deepEqual(c.envFrom, [{ secretRef: { name: 'firefly-mcp-secrets' } }]);
  assert.ok(!c.env.some(e => /TOKEN|PASSWORD/.test(e.name)), 'secrets come only from the Secret');
  assert.deepEqual(pod.imagePullSecrets, [{ name: 'ghcr-pull' }]);
  assert.ok(docs.every(d => d.kind !== 'Secret'), 'the chart never creates the Secret');
});

test('firefly-mcp: state survives app deletion', () => {
  const pvc = render().find(d => d.kind === 'PersistentVolumeClaim');
  assert.equal(pvc.metadata.annotations['helm.sh/resource-policy'], 'keep');
  assert.equal(pvc.metadata.annotations['argocd.argoproj.io/sync-options'], 'Delete=false');
});

test('firefly-mcp: network policy admits only the gateway and reaches only DNS and Firefly', () => {
  const np = render().find(d => d.kind === 'NetworkPolicy');
  assert.deepEqual(np.spec.policyTypes, ['Ingress', 'Egress']);
  assert.equal(np.spec.ingress.length, 1);
  assert.equal(np.spec.ingress[0].from[0].namespaceSelector.matchLabels['kubernetes.io/metadata.name'], 'traefik');
  const targets = np.spec.egress.map(e => e.to[0].namespaceSelector.matchLabels['kubernetes.io/metadata.name']);
  assert.deepEqual(targets, ['kube-system', 'firefly-iii']);
});
