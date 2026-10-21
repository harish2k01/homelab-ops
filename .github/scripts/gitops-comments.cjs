const fs = require('node:fs');
const previewMarker = '<!-- friday-pa:argocd-diff -->';
const legacyDeploymentMarker = '<!-- friday-pa:argocd-apply -->';
const owned = comment => comment.performed_via_github_app?.slug === 'friday-pa' || comment.user?.login === 'friday-pa[bot]';

module.exports = async ({github, context, core}) => {
  const {owner, repo} = context.repo;
  const head = process.env.GITOPS_HEAD_SHA;
  const mode = process.env.GITOPS_COMMENT_MODE;
  if (!['pending', 'preview'].includes(mode)) throw new Error('Only preview comment modes are supported');
  const runUrl = `${context.serverUrl}/${owner}/${repo}/actions/runs/${context.runId}`;
  const attempt = Number(process.env.GITHUB_RUN_ATTEMPT || 1);
  const provenance = `<!-- gitops-run:${context.runId}:${attempt} -->`;
  const comments = number => github.paginate(github.rest.issues.listComments, {owner, repo, issue_number: number, per_page: 100});
  async function upsert(number, existing, marker, body) {
    const match = existing.find(comment => owned(comment) && comment.body?.includes(marker));
    const recorded = match?.body?.match(/<!-- gitops-run:(\d+):(\d+) -->/);
    if (recorded && (BigInt(recorded[1]) > BigInt(context.runId) ||
        (recorded[1] === String(context.runId) && Number(recorded[2]) > attempt))) {
      core.notice('A newer workflow attempt already published this result');
      return false;
    }
    body += `\n${provenance}\n`;
    if (match) await github.rest.issues.updateComment({owner, repo, comment_id: match.id, body});
    else await github.rest.issues.createComment({owner, repo, issue_number: number, body});
    return true;
  }
  if (mode === 'pending' || mode === 'preview') {
    const number = context.issue.number;
    const {data: pull} = await github.rest.pulls.get({owner, repo, pull_number: number});
    if (pull.head.sha !== head || pull.state !== 'open') {
      core.notice('Superseded preview; leaving the current comment unchanged');
      return;
    }
    const existing = await comments(number);
    if (mode === 'pending') {
      if (!await upsert(number, existing, previewMarker,
        `${previewMarker}\n## GitOps PR Preview\nComparing changes with the live cluster. Previous output is stale.\n[View workflow](${runUrl})\n`)) return;
      for (const comment of existing.filter(owned)) {
        if (comment.body?.includes('<!-- friday-pa:argocd-new-app:')) {
          await github.rest.issues.deleteComment({owner, repo, comment_id: comment.id});
        }
        if (comment.body?.includes(legacyDeploymentMarker)) {
          await github.rest.issues.deleteComment({owner, repo, comment_id: comment.id});
        }
      }
      return;
    }
    const body = fs.existsSync('gitops-preview.md') ? fs.readFileSync('gitops-preview.md', 'utf8') :
      `${previewMarker}\n## GitOps PR Preview\n**Status:** Failed before a complete preview was generated.\n`;
    await upsert(number, existing, previewMarker,
      body.replaceAll('{{GITOPS_PREVIEW_ARTIFACT_URL}}', process.env.GITOPS_PREVIEW_ARTIFACT_URL || runUrl) + `\n[Workflow](${runUrl})\n`);
    return;
  }
};
