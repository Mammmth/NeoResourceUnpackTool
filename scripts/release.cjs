// Invoked by actions/github-script after all platform builds pass.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');

const PACKAGE_NAMES = [
  'NeoResourceUnpackTool.exe',
  'NeoResourceUnpackTool-macos-arm64.zip',
  'NeoResourceUnpackTool-macos-x86_64.zip',
];

function parseVersion(tag) {
  const match = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(tag);
  return match ? match.slice(1).map(BigInt) : null;
}

function isPrerelease(tag) {
  const match = /^(v\d+\.\d+\.\d+)-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)$/.exec(tag);
  return Boolean(match && parseVersion(match[1]) &&
    match[2].split('.').every(id => !/^\d+$/.test(id) || id === '0' || !id.startsWith('0')));
}

function validVersion(tag) {
  return Boolean(parseVersion(tag) || isPrerelease(tag));
}

function compareVersions(a, b) {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
  }
  return 0;
}

// Include drafts (which may not have a Git tag yet) when reserving version numbers.
async function selectReleaseVersion({ github, repo, sha, minimumVersion }) {
  const [tags, releases] = await Promise.all([
    github.paginate(github.rest.repos.listTags, { ...repo, per_page: 100 }),
    github.paginate(github.rest.repos.listReleases, { ...repo, per_page: 100 }),
  ]);
  const entries = [
    ...tags.map(t => ({ tag: t.name, sha: t.commit.sha })),
    ...releases.map(r => ({ tag: r.tag_name, sha: r.target_commitish })),
  ].filter(e => validVersion(e.tag));
  const assigned = [...new Set(entries.filter(e => e.sha === sha).map(e => e.tag))];
  if (assigned.length > 1) throw new Error('同一提交存在多个版本标签，请先核对');
  let tag = assigned[0];
  if (!tag && isPrerelease(`v${minimumVersion}`)) {
    // Prereleases are explicit names, never silently promoted or renumbered.
    // The publication checks below reject a tag/draft owned by another commit.
    tag = `v${minimumVersion}`;
  } else if (!tag) {
    const minimum = parseVersion(`v${minimumVersion}`);
    if (!minimum) throw new Error('VERSION 必须为不带 v 的三段版本号，例如 0.1.0');
    const highest = entries.map(e => parseVersion(e.tag)).filter(Boolean)
      .sort((a, b) => compareVersions(b, a))[0];
    const version = !highest || compareVersions(minimum, highest) > 0 ? minimum :
      [highest[0], highest[1], highest[2] + 1n];
    tag = `v${version.join('.')}`;
  }
  // Finishing an older draft must not move "Latest" back from a newer release.
  const prerelease = isPrerelease(tag);
  const makeLatest = !prerelease && !releases.some(r => !r.draft && !r.prerelease &&
    parseVersion(r.tag_name) && compareVersions(parseVersion(r.tag_name), parseVersion(tag)) > 0);
  return { tag, makeLatest, prerelease };
}

function prepareRelease({ sha, before, repository, serverUrl, runUrl, assetDir, cwd = process.cwd() }) {
  if (!/^[a-f0-9]{40}$/.test(sha) || !/^[a-f0-9]{40}$/.test(before)) {
    throw new Error('发布需要 push 事件的完整 before/head SHA');
  }
  const git = (...args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trimEnd();
  const minimumVersion = fs.readFileSync(path.join(cwd, 'VERSION'), 'utf8').trim();
  if (!validVersion(`v${minimumVersion}`)) {
    throw new Error('VERSION 必须为不带 v 的版本号，例如 1.0.0 或 1.0.0-beta');
  }
  const initial = /^0+$/.test(before);
  // A branch's first push has no previous main tip; include its head commit only.
  const commits = initial ? [sha] : git('rev-list', '--reverse', `${before}..${sha}`).split('\n').filter(Boolean);
  const baseUrl = `${serverUrl}/${repository}`;
  const comparison = initial ? `${baseUrl}/commit/${sha}` : `${baseUrl}/compare/${before}...${sha}`;
  // The pushed head (normally the merge commit) is the curated release description.
  const headMessage = git('show', '-s', '--format=%B', sha);
  const description = headMessage.length <= 30000 ? headMessage :
    `${headMessage.slice(0, 30000)}\n\n备注过长，完整内容请查看[发布提交](${baseUrl}/commit/${sha})。`;
  let notes = '';
  for (const commit of commits) {
    const message = git('show', '-s', '--format=%B', commit);
    // Use a longer fence than anything in the commit, keeping text as data.
    const fence = '`'.repeat(Math.max(3, ...Array.from(message.matchAll(/`+/g), m => m[0].length + 1)));
    const entry = `### ${commit.slice(0, 12)}\n\n${fence}text\n${message}\n${fence}\n\n`;
    if (notes.length + entry.length > 25000) {
      notes += '提交备注过长，剩余内容请查看下方完整变更链接。\n\n';
      break;
    }
    notes += entry;
  }
  const assets = PACKAGE_NAMES.map(name => {
    const file = path.resolve(cwd, assetDir, name);
    if (!fs.statSync(file).isFile() || fs.statSync(file).size === 0) {
      throw new Error(`发布包不存在或为空：${name}`);
    }
    return { name, file };
  });
  const checksums = assets.map(({ name, file }) =>
    `${crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}  ${name}\n`).join('');
  const checksumFile = path.resolve(cwd, assetDir, 'SHA256SUMS.txt');
  fs.writeFileSync(checksumFile, checksums);
  assets.push({ name: 'SHA256SUMS.txt', file: checksumFile });
  const body = `main 自动构建：Windows x64、macOS Apple Silicon、macOS Intel。\n\n` +
    `源码提交：\`${sha}\`；[构建与验证](${runUrl})。\n\n` +
    `${description}\n\n` +
    `<details>\n<summary>本次推送的完整提交备注</summary>\n\n${notes || '本次推送没有新增提交。\n\n'}</details>\n\n` +
    `[完整变更](${comparison})\n\n` +
    `下载对应平台的附件；SHA256SUMS.txt 用于核对文件完整性。当前构建未配置正式签名或 Apple 公证。\n`;
  return { sha, minimumVersion, body, assets };
}

async function publishRelease({ github, repo, plan }) {
  const { tag, makeLatest, prerelease } = await selectReleaseVersion({ github, repo, ...plan });
  plan = { ...plan, tag, name: `Neo Resource Unpack Tool ${tag}` };
  const api = github.rest.repos;
  const optional = async operation => {
    try { return (await operation()).data; }
    catch (error) { if (error.status === 404) return null; throw error; }
  };
  // Refuse to publish binaries against a pre-existing tag for another commit.
  // getCommit returns 422 for an absent tag, not 404. Check the ref endpoint first.
  const ref = `tags/${plan.tag}`;
  const existingRef = await optional(() => github.rest.git.getRef({ ...repo, ref }));
  const tagged = existingRef ? (await api.getCommit({ ...repo, ref })).data : null;
  if (tagged && tagged.sha !== plan.sha) throw new Error('同名标签指向其他提交，停止发布');
  let release = await optional(() => api.getReleaseByTag({ ...repo, tag: plan.tag }));
  if (release && release.target_commitish !== plan.sha) throw new Error('同名 Release 的源码提交不匹配');
  if (release && !release.draft) return release; // Never replace published downloads on a rerun.
  if (!release) {
    release = (await api.createRelease({ ...repo, tag_name: plan.tag, target_commitish: plan.sha,
      name: plan.name, body: plan.body, draft: true, prerelease })).data;
  }
  // An interrupted upload stays draft. Reruns replace draft assets and then publish.
  const previousAssets = await github.paginate(api.listReleaseAssets, { ...repo, release_id: release.id });
  for (const asset of previousAssets) {
    await api.deleteReleaseAsset({ ...repo, asset_id: asset.id });
  }
  for (const asset of plan.assets) {
    await api.uploadReleaseAsset({ ...repo, release_id: release.id, name: asset.name,
      data: fs.readFileSync(asset.file), headers: { 'content-type': 'application/octet-stream' } });
  }
  return (await api.updateRelease({ ...repo, release_id: release.id,
    name: plan.name, body: plan.body, draft: false, prerelease,
    make_latest: makeLatest ? 'true' : 'false' })).data;
}

module.exports = { PACKAGE_NAMES, prepareRelease, publishRelease };
