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

function prepareRelease({ sha, before, repository, serverUrl, runUrl, assetDir, cwd = process.cwd() }) {
  if (!/^[a-f0-9]{40}$/.test(sha) || !/^[a-f0-9]{40}$/.test(before)) {
    throw new Error('发布需要 push 事件的完整 before/head SHA');
  }
  const git = (...args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trimEnd();
  const timestamp = Number(git('show', '-s', '--format=%ct', sha));
  const date = new Date(timestamp * 1000).toISOString().replace(/[-:]/g, '').replace('.000Z', 'Z');
  const tag = `v${date}-${sha.slice(0, 12)}`;
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
    `本项目自身代码采用 GPL-3.0-only；第三方组件保留各自许可。` +
    `[对应提交完整源码 ZIP](${baseUrl}/archive/${sha}.zip)、` +
    `[第三方声明](${baseUrl}/blob/${sha}/THIRD_PARTY_NOTICES.md)、` +
    `[源码获取与重新构建](${baseUrl}/blob/${sha}/SOURCE_CODE.md)。\n\n` +
    `${description}\n\n` +
    `<details>\n<summary>本次推送的完整提交备注</summary>\n\n${notes || '本次推送没有新增提交。\n\n'}</details>\n\n` +
    `[完整变更](${comparison})\n\n` +
    `下载对应平台的附件；SHA256SUMS.txt 用于核对文件完整性。当前构建未配置正式签名或 Apple 公证。\n`;
  return { sha, tag, name: `Neo Resource Unpack Tool ${tag}`, body, assets };
}

async function publishRelease({ github, repo, plan }) {
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
      name: plan.name, body: plan.body, draft: true, prerelease: false })).data;
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
    name: plan.name, body: plan.body, draft: false, make_latest: 'legacy' })).data;
}

module.exports = { PACKAGE_NAMES, prepareRelease, publishRelease };
