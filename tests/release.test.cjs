const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { PACKAGE_NAMES, prepareRelease, publishRelease } = require('../scripts/release.cjs');

function fixture(t) {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'neo-release-'));
  t.after(() => fs.rmSync(cwd, { recursive: true, force: true }));
  const git = (...args) => execFileSync('git', args, { cwd, encoding: 'utf8',
    env: { ...process.env, GIT_AUTHOR_DATE: '2026-10-13T12:00:00Z', GIT_COMMITTER_DATE: '2026-10-13T12:00:00Z' },
    stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git('init', '-b', 'main');
  git('config', 'user.name', 'Release Test');
  git('config', 'user.email', 'release@example.invalid');
  git('config', 'commit.gpgsign', 'false');
  const commit = message => { git('commit', '--allow-empty', '-m', message); return git('rev-parse', 'HEAD'); };
  const before = commit('旧提交：不应出现在备注中');
  const assetDir = 'packages';
  fs.mkdirSync(path.join(cwd, assetDir));
  for (const name of PACKAGE_NAMES) fs.writeFileSync(path.join(cwd, assetDir, name), name);
  const options = { cwd, before, assetDir, repository: 'owner/repo',
    serverUrl: 'https://github.com', runUrl: 'https://github.com/owner/repo/actions/runs/1' };
  return { cwd, git, commit, options };
}

test('merge notes include all new commit titles/bodies; tags and checksums are deterministic', t => {
  const { git, commit, options, cwd } = fixture(t);
  git('switch', '-c', 'dev');
  commit('新功能\n\n中文正文，含 `代码` 和 ``` 围栏；$(touch injected)');
  commit('修复说明');
  git('switch', 'main');
  git('merge', '--no-ff', 'dev', '-m', '合并功能\n\n## 版本亮点\n\n- 新增中文功能\n- 改进桌面体验');
  const sha = git('rev-parse', 'HEAD');
  const plan = prepareRelease({ ...options, sha });
  assert.equal(plan.tag, `v20261013T120000Z-${sha.slice(0, 12)}`);
  for (const text of ['新功能', '中文正文', '修复说明', '合并功能', '$(touch injected)']) assert.ok(plan.body.includes(text));
  assert.ok(!plan.body.includes('旧提交'));
  assert.ok(plan.body.includes('合并功能\n\n## 版本亮点\n\n- 新增中文功能'));
  assert.ok(plan.body.indexOf('## 版本亮点') < plan.body.indexOf('<details>'));
  assert.equal(fs.existsSync(path.join(cwd, 'injected')), false);
  assert.deepEqual(prepareRelease({ ...options, sha }), plan);
  assert.equal(plan.assets.length, 4);
  assert.ok(plan.body.includes(`/archive/${sha}.zip`));
  assert.ok(plan.body.includes(`/blob/${sha}/SOURCE_CODE.md`));
  assert.ok(plan.body.includes('GPL-3.0-only'));
  const sum = crypto.createHash('sha256').update(PACKAGE_NAMES[0]).digest('hex');
  assert.ok(fs.readFileSync(plan.assets[3].file, 'utf8').includes(`${sum}  ${PACKAGE_NAMES[0]}`));
});

test('first main push includes head only; missing or empty platform package blocks preparation', t => {
  const { options, commit, cwd } = fixture(t);
  const sha = commit('首次 main 发布');
  const args = { ...options, sha, before: '0'.repeat(40) };
  const plan = prepareRelease(args);
  assert.ok(plan.body.includes('首次 main 发布'));
  assert.ok(!plan.body.includes('旧提交'));
  fs.writeFileSync(path.join(cwd, options.assetDir, PACKAGE_NAMES[1]), '');
  assert.throws(() => prepareRelease(args), /为空/);
  fs.unlinkSync(path.join(cwd, options.assetDir, PACKAGE_NAMES[1]));
  assert.throws(() => prepareRelease(args), /ENOENT/);
});

test('very long notes keep a complete-change link and remain within the API body limit', t => {
  const { options, commit } = fixture(t);
  const sha = commit('x'.repeat(56000));
  const plan = prepareRelease({ ...options, sha });
  assert.ok(plan.body.includes('备注过长'));
  assert.ok(plan.body.includes(`/compare/${options.before}...${sha}`));
  assert.ok(plan.body.length < 60000);
  assert.throws(() => prepareRelease({ ...options, sha: '--help' }), /SHA/);
});

function fakeApi() {
  const state = { release: null, assets: [], published: 0, created: 0, failUpload: false, tagSha: null, error: null };
  const missing = () => { throw Object.assign(new Error('not found'), { status: 404 }); };
  const repos = {
    getCommit: async () => {
      if (!state.tagSha) throw Object.assign(new Error('No commit found for SHA'), { status: 422 });
      return { data: { sha: state.tagSha } };
    },
    getReleaseByTag: async () => state.release ? { data: state.release } : missing(),
    createRelease: async args => { state.created++; state.release = { ...args, id: 10, html_url: 'https://example.invalid/release' }; return { data: state.release }; },
    listReleaseAssets: async () => ({ data: state.assets }),
    deleteReleaseAsset: async ({ asset_id }) => { state.assets = state.assets.filter(a => a.id !== asset_id); },
    uploadReleaseAsset: async args => {
      if (state.failUpload && state.assets.length === 1) throw new Error('upload failed');
      state.assets.push({ id: state.assets.length + 1, name: args.name, data: args.data });
    },
    updateRelease: async args => { state.published++; Object.assign(state.release, args); return { data: state.release }; },
  };
  const git = { getRef: async () => {
    if (state.error) throw state.error;
    return state.tagSha ? { data: { object: { sha: state.tagSha } } } : missing();
  } };
  return { state, github: { rest: { repos, git }, paginate: async (method, args) => (await method(args)).data } };
}

test('upload failure leaves draft; retry repairs all assets before publishing; published rerun is a no-op', async t => {
  const { options, commit } = fixture(t);
  const plan = prepareRelease({ ...options, sha: commit('发布') });
  const { state, github } = fakeApi();
  const args = { github, repo: { owner: 'owner', repo: 'repo' }, plan };
  state.failUpload = true;
  await assert.rejects(publishRelease(args), /upload failed/);
  assert.equal(state.release.draft, true);
  assert.equal(state.published, 0);
  assert.equal(state.assets.length, 1);
  state.failUpload = false;
  await publishRelease(args);
  assert.equal(state.created, 1);
  assert.equal(state.release.draft, false);
  assert.deepEqual(state.assets.map(a => a.name), plan.assets.map(a => a.name));
  state.tagSha = plan.sha;
  await publishRelease(args);
  assert.equal(state.created, 1);
  assert.equal(state.published, 1);
});

test('API permission failure and conflicting tags stop publication without creating a release', async t => {
  const { options, commit } = fixture(t);
  const plan = prepareRelease({ ...options, sha: commit('发布') });
  const { state, github } = fakeApi();
  const args = { github, repo: { owner: 'owner', repo: 'repo' }, plan };
  state.error = Object.assign(new Error('forbidden'), { status: 403 });
  await assert.rejects(publishRelease(args), /forbidden/);
  state.error = null;
  state.tagSha = 'a'.repeat(40);
  await assert.rejects(publishRelease(args), /标签指向其他提交/);
  state.tagSha = null;
  state.release = { target_commitish: 'b'.repeat(40), draft: true };
  await assert.rejects(publishRelease(args), /源码提交不匹配/);
  assert.equal(state.created, 0);
  assert.equal(state.published, 0);
});
