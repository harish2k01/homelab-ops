import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { parseAllDocuments } from 'yaml';

for (const [name, namespace, publicHost] of [['portfolio-next', 'portfolio', ['harish2k01.xyz', 'www.harish2k01.xyz']], ['portfolio-tracker', 'portfolio-tracker', null], ['railwatch', 'railwatch', null]]) {
  test(`${name}: local app routes stay on the intended Gateways`, () => {
    const docs = parseAllDocuments(execFileSync('helm', ['template', name, `charts/${name}`, '--namespace', namespace], { encoding: 'utf8' })).map(d => d.toJSON()).filter(Boolean);
    const routes = docs.filter(d => d.kind === 'HTTPRoute');
    // RailWatch keeps its pre-chart route name, so it is the internal route.
    const internal = routes.find(r => r.metadata.name.endsWith('-internal') || r.metadata.name === name);
    assert.ok(internal);
    assert.ok(internal.spec.parentRefs.every(p => p.name === 'traefik-gateway'));
    assert.ok(internal.spec.hostnames.every(h => h.includes('.k8s.')));
    const external = routes.find(r => r.metadata.name.endsWith('-external'));
    if (publicHost) {
      assert.deepEqual(external.spec.hostnames, publicHost);
      assert.ok(external.spec.parentRefs.every(p => p.name === 'traefik-external-gateway'));
    } else assert.equal(external, undefined);
  });
}
