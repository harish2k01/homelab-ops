import assert from 'node:assert/strict';
import {execFileSync, spawnSync} from 'node:child_process';
import {parseAllDocuments} from 'yaml';

export function render(chart, settings = []) {
  const args = settings.flatMap(value => ['--set', value]);
  const docs = parseAllDocuments(execFileSync('helm', ['template', 'contract', `charts/${chart}`, '--namespace', 'checks', '--set', 'httpRoutes.internal.enabled=false,httpRoutes.external.enabled=false', ...args], {encoding: 'utf8'}));
  for (const doc of docs) assert.equal(doc.errors.length, 0);
  return docs.map(doc => doc.toJSON()).filter(Boolean);
}
export const workload = (docs, chart) => {
  const result = docs.find(doc => doc.kind === 'Deployment' && doc.spec.template.spec.containers.some(c => c.name === chart));
  assert.ok(result, `application deployment ${chart}`);
  return result;
};
export const container = (docs, chart) => workload(docs, chart).spec.template.spec.containers.find(c => c.name === chart);
export const env = (c, name) => c.env?.find(item => item.name === name);
export function rejects(chart, settings, message) {
  const result = spawnSync('helm', ['template', 'contract', `charts/${chart}`, ...settings.flatMap(value => ['--set', value])], {encoding:'utf8'});
  assert.equal(result.error, undefined);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, message);
}
// Assert end-to-end Service -> pod selector -> container port and probe wiring.
export function serviceContract(docs, chart, expectedPort) {
  const deployment = workload(docs, chart), c = container(docs, chart);
  const services = docs.filter(doc => doc.kind === 'Service' && Object.entries(doc.spec.selector ?? {}).every(([key,value]) => deployment.spec.template.metadata.labels[key] === value));
  assert.ok(services.length, 'application has a selecting Service');
  assert.ok(c.ports.some(port => port.containerPort === expectedPort), `listener ${expectedPort}`);
  for (const service of services) for (const port of service.spec.ports) {
    assert.ok(c.ports.some(p => (typeof port.targetPort === 'string' ? p.name === port.targetPort : p.containerPort === port.targetPort) && (p.protocol ?? 'TCP') === (port.protocol ?? 'TCP')), `unresolved service target ${port.targetPort}`);
  }
  for (const probe of [c.startupProbe,c.livenessProbe,c.readinessProbe].filter(Boolean)) {
    const port = probe.httpGet?.port ?? probe.tcpSocket?.port;
    if (port !== undefined) assert.ok(c.ports.some(p => typeof port === 'string' ? p.name === port : p.containerPort === port), `unresolved probe ${port}`);
  }
  assert.equal(docs.some(d => ['Ingress','HTTPRoute'].includes(d.kind)),false,'public routes opt in');
}
export function existingClaim(docs, chart, mountPath) {
  const pod = workload(docs, chart).spec.template.spec;
  assert.equal(docs.some(d => d.kind === 'PersistentVolumeClaim'), false, 'no replacement claim');
  const mount = container(docs, chart).volumeMounts.find(m => m.mountPath === mountPath);
  assert.ok(mount, `persisted ${mountPath}`);
  assert.equal(pod.volumes.find(v => v.name === mount.name).persistentVolumeClaim.claimName, 'operator-data');
}
