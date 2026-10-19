const {test} = require('node:test');
const assert = require('node:assert/strict');
const publish = require('./gitops-comments.cjs');
const fs = require('node:fs');
const path = require('node:path');

function fixture(mode = 'pending', sha = 'head') {
  process.env.GITOPS_COMMENT_MODE = mode;
  process.env.GITOPS_HEAD_SHA = 'head';
  const writes = [];
  const comments = [
    {id: 1, user: {login: 'human'}, body: '<!-- friday-pa:argocd-diff --> human text'},
    {id: 2, user: {login: 'friday-pa[bot]'}, body: '<!-- friday-pa:argocd-diff --> old preview'},
    {id: 3, user: {login: 'friday-pa[bot]'}, body: '<!-- friday-pa:argocd-new-app:demo --> old app'},
    {id: 4, user: {login: 'friday-pa[bot]'}, body: '<!-- friday-pa:argocd-apply --> approval requested'},
  ];
  const github = {rest: {
    pulls: {get: async () => ({data: {head: {sha}, state: 'open'}})},
    issues: {listComments: 'comments',
      updateComment: async args => writes.push({type: 'update', ...args}),
      deleteComment: async args => writes.push({type: 'delete', ...args}),
      createComment: async args => writes.push({type: 'create', ...args}),
    },
  }, paginate: async () => comments};
  return {writes, args: {github, context: {repo: {owner: 'owner', repo: 'ops'}, issue: {number: 1}, serverUrl: 'https://github.com', runId: 2}, core: {notice() {}}}};
}

test('invalidates old preview and deployment instructions; removes old app output', async () => {
  const {writes, args} = fixture();
  await publish(args);
  assert.equal(writes.length, 3);
  assert.equal(writes[0].comment_id, 2);
  assert.match(writes[0].body, /Previous output is stale/);
  assert.doesNotMatch(writes[0].body, /Rendering revision|`head`/);
  assert.deepEqual(writes[1], {type: 'delete', owner: 'owner', repo: 'ops', comment_id: 3});
  assert.match(writes[2].body, /No pre-merge deployment approval is pending/);
  assert.ok(writes.every(write => write.comment_id !== 1));
});

test('superseded run never overwrites comments', async () => {
  const {writes, args} = fixture('pending', 'new-head');
  await publish(args);
  assert.equal(writes.length, 0);
});

test('setup failure publishes failure instead of leaving green or pending output', async () => {
  const {writes, args} = fixture('preview');
  await publish(args);
  assert.equal(writes.length, 1);
  assert.match(writes[0].body, /Failed before a complete preview/);
});

test('older same-head attempt cannot overwrite a newer successful preview', async () => {
  const {writes, args} = fixture('preview');
  args.github.paginate = async () => [{id: 2, user: {login: 'friday-pa[bot]'},
    body: '<!-- friday-pa:argocd-diff --> successful preview\n<!-- gitops-run:3:1 -->'}];
  await publish(args);
  assert.equal(writes.length, 0);
});

test('older rerun attempt cannot overwrite a newer attempt of the same workflow', async () => {
  const {writes, args} = fixture('pending');
  args.github.paginate = async () => [{id: 2, user: {login: 'friday-pa[bot]'},
    body: '<!-- friday-pa:argocd-diff --> successful preview\n<!-- gitops-run:2:2 -->'}];
  await publish(args);
  assert.equal(writes.length, 0);
});

function deploymentFixture(currentHead = 'head') {
  const fixtureState = fixture('deploy');
  const {github, core} = fixtureState.args;
  process.env.GITOPS_BASE_SHA = 'base';
  process.env.GITOPS_DEPLOY_RESULT = 'failure';
  github.rest.repos = {compareCommitsWithBasehead: 'compare', listPullRequestsAssociatedWithCommit: 'associated'};
  github.rest.git = {getRef: async () => ({data: {object: {sha: currentHead}}})};
  github.paginate = async (endpoint, request, map) => {
    if (endpoint === 'compare') return map({data: {commits: [{sha: 'merge-one'}, {sha: 'merge-two'}]}});
    if (endpoint === 'associated') return [{number: request.commit_sha === 'merge-one' ? 1 : 2,
      merged_at: '2026-10-19', base: {ref: 'main'}}];
    return [];
  };
  core.summary = {addRaw() {}, async write() {}};
  return fixtureState;
}

test('setup failure is reported on every PR included in a coalesced deployment', async () => {
  const {writes, args} = deploymentFixture();
  await publish(args);
  assert.deepEqual(writes.map(write => write.issue_number), [1, 2]);
  for (const write of writes) {
    assert.match(write.body, /Setup or runner execution failed/);
    assert.match(write.body, /Workflow result:\*\* failure/);
  }
});

test('superseded deployment leaves PR comments untouched while keeping workflow summary', async () => {
  const {writes, args} = deploymentFixture('newer-head');
  let summaryWritten = false;
  args.core.summary.write = async () => { summaryWritten = true; };
  await publish(args);
  assert.equal(writes.length, 0);
  assert.equal(summaryWritten, true);
});

// Exercise the exact resolver executed by github-script with API responses,
// rather than testing a second implementation of its cursor rules.
const workflow = fs.readFileSync(path.join(__dirname, '../workflows/gitops-reconcile.yaml'), 'utf8');
const resolverSource = workflow.split('          script: |\n')[1].split('\n      - name: Recover')[0]
  .split('\n').map(line => line.slice(12)).join('\n');
const resolver = new (Object.getPrototypeOf(async function () {}).constructor)('github', 'context', 'core', resolverSource);
async function resolveCursor({success = [], history = [], artifacts = [], before, inputs = {}, runId = 20} = {}) {
  const outputs = {};
  await resolver({rest: {git: {getRef: async () => ({data: {object: {sha: 'f'.repeat(40)}}})},
    actions: {listWorkflowRuns: 'runs', listArtifactsForRepo: 'artifacts'}},
    paginate: async (endpoint, request) => endpoint === 'artifacts' ? artifacts : request.status === 'success' ? success : history},
  {repo: {owner: 'owner', repo: 'ops'}, ref: 'refs/heads/main', runId, payload: {before, inputs}},
  {setOutput: (key, value) => { outputs[key] = value; }});
  return outputs;
}

test('first push reconciles only its cumulative change range, leaving staged apps alone', async () => {
  const outputs = await resolveCursor({before: 'a'.repeat(40)});
  assert.equal(outputs.base, 'a'.repeat(40));
  assert.equal(outputs.head, 'f'.repeat(40));
  assert.equal(outputs.all_apps, 'false');
});

test('manual first run requires an explicit baseline or full reconciliation', async () => {
  await assert.rejects(resolveCursor(), /No successful reconciliation cursor/);
  await assert.rejects(resolveCursor({before: '0'.repeat(40)}), /No successful reconciliation cursor/);
  const outputs = await resolveCursor({inputs: {initial_base: 'b'.repeat(40)}});
  assert.equal(outputs.base, 'b'.repeat(40));
  assert.equal(outputs.all_apps, 'false');
});

test('a failed first run cannot silently discard its pending changes on the next push', async () => {
  await assert.rejects(resolveCursor({before: 'c'.repeat(40), history: [{id: 19, status: 'completed'}]}),
    /earlier reconciliation did not succeed/);
});

test('failed first run recovers its earliest durable baseline without activating staged Applications', async () => {
  const outputs = await resolveCursor({history: [{id: 19, status: 'completed'}, {id: 18, status: 'completed'}], artifacts: [
    {id: 102, created_at: '2026-10-19T12:00:00Z', expired: false, workflow_run: {id: 19, head_branch: 'main'}},
    {id: 101, created_at: '2026-10-19T11:00:00Z', expired: false, workflow_run: {id: 18, head_branch: 'main'}},
    {id: 99, created_at: '2026-10-18T11:00:00Z', expired: true, workflow_run: {id: 17, head_branch: 'main'}},
  ]});
  assert.equal(outputs.recovery_run, 18);
  assert.equal(outputs.recovery_artifact, 101);
  assert.equal(outputs.all_apps, 'false');
});

test('latest successful event wins regardless of API rerun ordering or this runs previous attempt', async () => {
  const outputs = await resolveCursor({success: [
    {id: 10, run_number: 1, head_sha: 'old'}, {id: 20, run_number: 3, head_sha: 'self'},
    {id: 15, run_number: 2, head_sha: 'latest'},
  ]});
  assert.equal(outputs.base, 'latest');
});

test('full reconciliation is explicit even when no cursor exists', async () => {
  const outputs = await resolveCursor({inputs: {reconcile_all: 'true'}, history: [{id: 19, status: 'completed'}]});
  assert.equal(outputs.all_apps, 'true');
});
