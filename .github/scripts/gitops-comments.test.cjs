const {test} = require('node:test');
const assert = require('node:assert/strict');
const publish = require('./gitops-comments.cjs');

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

test('invalidates old preview and removes historical app and apply comments', async () => {
  const {writes, args} = fixture();
  await publish(args);
  assert.equal(writes.length, 3);
  assert.equal(writes[0].comment_id, 2);
  assert.match(writes[0].body, /Previous output is stale/);
  assert.doesNotMatch(writes[0].body, /Rendering revision|`head`/);
  assert.deepEqual(writes[1], {type: 'delete', owner: 'owner', repo: 'ops', comment_id: 3});
  assert.deepEqual(writes[2], {type: 'delete', owner: 'owner', repo: 'ops', comment_id: 4});
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

test('unsupported modes cannot publish deployment results', async () => {
  for (const mode of ['deploy', 'apply', undefined]) {
    const {writes, args} = fixture(mode);
    if (mode === undefined) delete process.env.GITOPS_COMMENT_MODE;
    await assert.rejects(publish(args), /Only preview comment modes/);
    assert.equal(writes.length, 0);
  }
});
