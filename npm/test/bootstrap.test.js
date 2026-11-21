'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const b = require('../bin/bootstrap');

test('tarballUrl defaults to the GitHub main tarball and is overridable', () => {
  assert.match(b.tarballUrl({}), /github\.com\/natarmr\/OpenNote.*main\.tar\.gz/);
  assert.equal(
    b.tarballUrl({ OPENNOTE_TARBALL_URL: 'https://example.com/x.tar.gz' }),
    'https://example.com/x.tar.gz'
  );
});

test('pythonCandidates honours OPENNOTE_PYTHON', () => {
  assert.deepEqual(b.pythonCandidates({}), ['python3', 'python', 'py']);
  assert.deepEqual(b.pythonCandidates({ OPENNOTE_PYTHON: 'C:/py.exe' }), ['C:/py.exe']);
});

test('cacheRoot honours the explicit override', () => {
  assert.equal(
    b.cacheRoot({ OPENNOTE_NPM_CACHE: '/tmp/custom' }, 'linux'),
    path.resolve('/tmp/custom')
  );
});

test('cacheRoot uses LOCALAPPDATA on Windows, ~/.cache elsewhere', () => {
  const win = b.cacheRoot({ LOCALAPPDATA: 'C:/Users/x/AppData/Local' }, 'win32');
  assert.equal(win, path.join('C:/Users/x/AppData/Local', 'opennote', 'npm'));
  const linux = b.cacheRoot({}, 'linux');
  assert.match(linux, /opennote[\\/]npm$/);
});

test('installDir is keyed by launcher version and source hash', () => {
  const a = b.installDir({ OPENNOTE_NPM_CACHE: '/tmp/c' }, 'linux');
  const b2 = b.installDir({ OPENNOTE_NPM_CACHE: '/tmp/c' }, 'linux');
  assert.equal(a, b2);
  const different = b.installDir(
    { OPENNOTE_NPM_CACHE: '/tmp/c', OPENNOTE_TARBALL_URL: 'https://example.com/x' },
    'linux'
  );
  assert.notEqual(a, different);
});

test('markerPath and siteDir live under installDir', () => {
  const env = { OPENNOTE_NPM_CACHE: '/tmp/c' };
  assert.equal(path.dirname(b.markerPath(env, 'linux')), b.installDir(env, 'linux'));
  assert.equal(path.dirname(b.siteDir(env, 'linux')), b.installDir(env, 'linux'));
});

test('markerMatches requires version, url and python override to agree', () => {
  const env = {};
  const good = {
    version: require('../package.json').version,
    url: b.tarballUrl(env),
    python: '',
  };
  assert.equal(b.markerMatches(good, env), true);
  assert.equal(b.markerMatches(null, env), false);
  assert.equal(b.markerMatches({ ...good, version: '0.0.0' }, env), false);
  assert.equal(b.markerMatches({ ...good, url: 'other' }, env), false);
  assert.equal(b.markerMatches(good, { OPENNOTE_PYTHON: 'py' }), false);
});

test('readMarker returns null on missing or malformed marker', () => {
  const fsStub = {
    readFileSync() {
      throw new Error('ENOENT');
    },
  };
  assert.equal(b.readMarker({}, 'linux', fsStub), null);
});
