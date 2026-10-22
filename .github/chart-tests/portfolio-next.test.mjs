import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';

const chart = 'charts/portfolio-next';
const render = (...args) => execFileSync('helm', ['template', 'portfolio-next', chart, '--namespace', 'portfolio', '--set', 'httpRoutes.internal.enabled=false,httpRoutes.external.enabled=false,image.tag=v0.1.0,image.digest=', ...args], { encoding:'utf8' });

test('route opt-out serves port 8080 behind port 80 with health probes and no public route', () => {
  const manifest = render();
  assert.match(manifest,/image: "ghcr.io\/harish2k01\/portfolio-next:v0.1.0"/);
  assert.match(manifest,/containerPort: 8080/);
  assert.match(manifest,/port: 80/);
  assert.equal((manifest.match(/path: \/healthz/g) || []).length,2);
  assert.match(manifest,/automountServiceAccountToken: false/);
  assert.match(manifest,/readOnlyRootFilesystem: true/);
  assert.match(manifest,/mountPath: \/tmp/);
  assert.doesNotMatch(manifest,/kind: (HTTPRoute|Ingress|PersistentVolumeClaim)/);
});

test('digest overrides mutable tags and a blog-only digest change changes the pod image', () => {
  const digestA = 'sha256:' + 'a'.repeat(64);
  const digestB = 'sha256:' + 'b'.repeat(64);
  const first = render('--set',`image.tag=v9.0.0,image.digest=${digestA}`);
  assert.ok(first.includes(`image: "ghcr.io/harish2k01/portfolio-next@${digestA}"`));
  assert.ok(!first.includes(':v9.0.0'));
  assert.equal(first,render('--set',`image.tag=v9.0.0,image.digest=${digestA}`));
  const second = render('--set',`image.tag=v9.0.0,image.digest=${digestB}`);
  assert.equal(second,first.replace(digestA,digestB));
});

test('Gateway API and Ingress render distinct correctly targeted routes', () => {
  const gateway = render('--set','httpRoutes.main.enabled=true,httpRoutes.main.parentRefs[0].name=test-gateway,httpRoutes.main.parentRefs[0].namespace=edge,httpRoutes.main.hostnames[0]=portfolio.example.com');
  assert.match(gateway,/kind: HTTPRoute/);
  assert.match(gateway,/name: test-gateway/);
  assert.match(gateway,/portfolio.example.com/);
  assert.match(gateway,/backendRefs:\s+- name: portfolio-next\s+port: 80/);
  const ingress = render('--set','ingress.enabled=true,ingress.className=nginx,replicaCount=3');
  assert.match(ingress,/kind: Ingress/);
  assert.match(ingress,/ingressClassName: nginx/);
  assert.match(ingress,/replicas: 3/);
});

test('invalid digests, missing Gateway attachment and conflicting routes fail before deployment', () => {
  for (const settings of ['image.digest=sha256:invalid','httpRoutes.main.enabled=true','httpRoutes.main.enabled=true,ingress.enabled=true','replicaCount=0']) {
    const result = spawnSync('helm',['template','portfolio-next',chart,'--set',settings],{encoding:'utf8'});
    assert.notEqual(result.status,0,settings);
    assert.match(result.stderr,/Error/);
  }
});
