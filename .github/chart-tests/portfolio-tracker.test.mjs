import test from 'node:test';
import assert from 'node:assert/strict';
import {render,container,env,workload,serviceContract,rejects} from './helpers/render.mjs';
test('Portfolio Tracker routes web traffic and supports an entirely external Secret-backed database',()=>{
  serviceContract(render('portfolio-tracker'),'portfolio-tracker',3000);
  const docs=render('portfolio-tracker',['database.postgresql.enabled=false','database.external.enabled=true','database.external.existingSecret=db-runtime','portfolioTracker.secrets.existingSecret=runtime']);
  assert.equal(docs.filter(d=>d.kind==='Deployment').length,1);
  assert.equal(docs.some(d=>['Secret','PersistentVolumeClaim'].includes(d.kind)),false);
  assert.equal(workload(docs,'portfolio-tracker').spec.template.spec.initContainers,undefined);
  assert.equal(env(container(docs,'portfolio-tracker'),'DATABASE_URL').valueFrom.secretKeyRef.name,'db-runtime');
});
test('Portfolio Tracker rejects conflicting or missing database choices',()=>{
  rejects('portfolio-tracker',['database.external.enabled=true'],/Enable only one database/);
  rejects('portfolio-tracker',['database.postgresql.enabled=false'],/requires PostgreSQL/);
});
