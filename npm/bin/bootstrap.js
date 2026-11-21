'use strict';

/**
 * Bootstrap logic for the @natarmr/opennote launcher.
 *
 * OpenNote is a Python application; npm/bun install JavaScript, so this thin
 * wrapper pip-installs the app into a versioned cache directory on first run
 * and then execs `python -m opennote` with the user's arguments. Subsequent
 * runs reuse the cache. Kept free of side effects at require-time so the
 * helpers can be unit-tested with `node --test`.
 */
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const pkg = require('../package.json');

const DEFAULT_TARBALL_URL =
  'https://github.com/natarmr/OpenNote/archive/refs/heads/main.tar.gz';

/** Source URL for the Python app. Overridable for forks / pinned tags. */
function tarballUrl(env = process.env) {
  return env.OPENNOTE_TARBALL_URL || DEFAULT_TARBALL_URL;
}

/** Python binary candidates, in priority order. */
function pythonCandidates(env = process.env) {
  const override = (env.OPENNOTE_PYTHON || '').trim();
  return override ? [override] : ['python3', 'python', 'py'];
}

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, { encoding: 'utf8', ...opts });
}

/** True when *cmd* is a Python >= 3.10 executable. */
function pythonWorks(cmd) {
  const r = run(
    cmd,
    ['-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'],
    { stdio: 'ignore' }
  );
  return r.status === 0;
}

/** First working Python >= 3.10, or null. */
function findPython(env = process.env, candidates = pythonCandidates(env)) {
  for (const cmd of candidates) {
    if (pythonWorks(cmd)) return cmd;
  }
  return null;
}

/** Root directory holding all cached installs. */
function cacheRoot(env = process.env, platform = process.platform) {
  if (env.OPENNOTE_NPM_CACHE) return path.resolve(env.OPENNOTE_NPM_CACHE);
  const base =
    platform === 'win32' && env.LOCALAPPDATA
      ? env.LOCALAPPDATA
      : path.join(os.homedir(), '.cache');
  return path.join(base, 'opennote', 'npm');
}

/**
 * Install directory, keyed by the launcher version and a hash of the source
 * URL so a new npm release or a different source invalidates the cache.
 */
function installDir(env = process.env, platform = process.platform) {
  const hash = crypto
    .createHash('sha1')
    .update(tarballUrl(env))
    .digest('hex')
    .slice(0, 8);
  return path.join(cacheRoot(env, platform), `v${pkg.version}-${hash}`);
}

function siteDir(env = process.env, platform = process.platform) {
  return path.join(installDir(env, platform), 'site');
}

function markerPath(env = process.env, platform = process.platform) {
  return path.join(installDir(env, platform), '.installed.json');
}

function readMarker(env = process.env, platform = process.platform, fsImpl = fs) {
  try {
    return JSON.parse(fsImpl.readFileSync(markerPath(env, platform), 'utf8'));
  } catch {
    return null;
  }
}

/** A marker is valid only for this launcher version, source URL and Python override. */
function markerMatches(marker, env = process.env) {
  return (
    !!marker &&
    marker.version === pkg.version &&
    marker.url === tarballUrl(env) &&
    marker.python === (env.OPENNOTE_PYTHON || '').trim()
  );
}

/**
 * Install the Python app into the cache. Returns { python, site }.
 * Installer output goes to stderr so stdout stays clean for shell use.
 */
function install(env = process.env, platform = process.platform, fsImpl = fs) {
  const python = findPython(env);
  if (!python) {
    throw new Error(
      'Python 3.10+ is required. Install it, or point OPENNOTE_PYTHON at an executable.'
    );
  }
  const dir = installDir(env, platform);
  const site = siteDir(env, platform);
  fsImpl.mkdirSync(dir, { recursive: true });
  fsImpl.rmSync(site, { recursive: true, force: true });

  const req = `opennote @ ${tarballUrl(env)}`;
  console.error(`OpenNote: installing ${req} (first run, this can take a few minutes)...`);
  const result = run(
    python,
    ['-m', 'pip', 'install', '--target', site, '--upgrade', req],
    { stdio: 'inherit' }
  );
  if (result.status !== 0) {
    throw new Error(`pip install failed (exit ${result.status})`);
  }

  fsImpl.writeFileSync(
    markerPath(env, platform),
    JSON.stringify(
      {
        version: pkg.version,
        url: tarballUrl(env),
        python: (env.OPENNOTE_PYTHON || '').trim(),
        installedAt: new Date().toISOString(),
      },
      null,
      2
    )
  );
  return { python, site };
}

/** Return the cached { python, site }, installing on first use or invalidation. */
function ensureInstalled(env = process.env, platform = process.platform, fsImpl = fs) {
  const marker = readMarker(env, platform, fsImpl);
  if (
    markerMatches(marker, env) &&
    fsImpl.existsSync(siteDir(env, platform))
  ) {
    const python = findPython(env);
    if (python) return { python, site: siteDir(env, platform) };
  }
  return install(env, platform, fsImpl);
}

module.exports = {
  DEFAULT_TARBALL_URL,
  tarballUrl,
  pythonCandidates,
  findPython,
  cacheRoot,
  installDir,
  siteDir,
  markerPath,
  readMarker,
  markerMatches,
  install,
  ensureInstalled,
};
