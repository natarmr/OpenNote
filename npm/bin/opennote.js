#!/usr/bin/env node
'use strict';

/** Executable entry point: bootstrap the Python app, then forward all args. */
const path = require('node:path');
const { spawn } = require('node:child_process');
const bootstrap = require('./bootstrap');

function main() {
  let installed;
  try {
    installed = bootstrap.ensureInstalled();
  } catch (err) {
    console.error(`OpenNote: ${err.message}`);
    process.exitCode = 1;
    return;
  }

  const env = { ...process.env };
  // --target installs do not create console scripts; PYTHONPATH lets
  // `python -m opennote` import the cached app directly.
  env.PYTHONPATH = installed.site + path.delimiter + (env.PYTHONPATH || '');

  const child = spawn(
    installed.python,
    ['-m', 'opennote', ...process.argv.slice(2)],
    { stdio: 'inherit', env, shell: false }
  );

  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => child.kill(sig));
  }
  child.on('error', (err) => {
    console.error(`OpenNote: could not start Python: ${err.message}`);
    process.exitCode = 1;
  });
  child.on('exit', (code, signal) => {
    if (signal) {
      process.exitCode = signal === 'SIGINT' ? 130 : 143;
    } else {
      process.exitCode = code == null ? 1 : code;
    }
  });
}

main();
