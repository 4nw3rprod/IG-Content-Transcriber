#!/usr/bin/env node
'use strict';

/*
 * ReelRecon npx launcher.
 *
 * Finds a suitable Python, provisions a private virtualenv under
 * ~/.reelrecon on first run, then hands stdio over to the Python MCP
 * server (or the transcribe CLI). Everything the launcher prints goes
 * to stderr: when an MCP client spawns us, stdout belongs to the
 * protocol and must stay clean.
 *
 * Environment:
 *   REELRECON_HOME    where the venv lives (default: ~/.reelrecon)
 *   REELRECON_PYTHON  bring-your-own interpreter with deps already
 *                     installed; skips venv provisioning entirely
 */

const { spawn, spawnSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const MIN_PYTHON = [3, 10];
const PREFERRED_PYTHONS = ['python3.11', 'python3.12', 'python3.13', 'python3.10', 'python3', 'python'];

const packageRoot = path.resolve(__dirname, '..');
const requirementsFile = path.join(packageRoot, 'requirements.txt');
const isWindows = process.platform === 'win32';

function log(message) {
  process.stderr.write(`[reelrecon] ${message}\n`);
}

function fail(message) {
  log(`ERROR: ${message}`);
  process.exit(1);
}

function pythonVersion(command) {
  const result = spawnSync(command, ['-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], {
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  if (result.status !== 0 || !result.stdout) {
    return null;
  }
  const [major, minor] = result.stdout.trim().split('.').map(Number);
  if (!Number.isInteger(major) || !Number.isInteger(minor)) {
    return null;
  }
  return [major, minor];
}

function versionOk(version) {
  if (!version) return false;
  const [major, minor] = version;
  return major === MIN_PYTHON[0] && minor >= MIN_PYTHON[1];
}

function findSystemPython() {
  for (const candidate of PREFERRED_PYTHONS) {
    if (versionOk(pythonVersion(candidate))) {
      return candidate;
    }
  }
  return null;
}

function venvPythonPath(venvDir) {
  return isWindows ? path.join(venvDir, 'Scripts', 'python.exe') : path.join(venvDir, 'bin', 'python');
}

function installMarker(home) {
  return path.join(home, '.install-marker');
}

function desiredMarker(basePython) {
  const requirements = fs.readFileSync(requirementsFile, 'utf-8');
  const version = pythonVersion(basePython) || [];
  return crypto.createHash('sha256').update(`${version.join('.')}\n${requirements}`).digest('hex');
}

function run(command, args, description) {
  // stdout is routed to stderr (fd 2): pip and venv chatter must never
  // reach our stdout, which belongs to the MCP stdio framing.
  const result = spawnSync(command, args, { stdio: ['ignore', 2, 2] });
  if (result.error) {
    fail(`${description} failed to start: ${result.error.message}`);
  }
  if (result.status !== 0) {
    fail(`${description} failed with exit code ${result.status}.`);
  }
}

function ensureVenv() {
  const home = process.env.REELRECON_HOME || path.join(os.homedir(), '.reelrecon');
  const venvDir = path.join(home, 'venv');
  const venvPython = venvPythonPath(venvDir);

  const basePython = findSystemPython();
  if (!basePython) {
    fail(
      `No suitable Python found. ReelRecon needs Python >= ${MIN_PYTHON.join('.')} (3.11 recommended). ` +
        'Install it, or point REELRECON_PYTHON at an interpreter that already has the dependencies.'
    );
  }

  const marker = desiredMarker(basePython);
  const markerFile = installMarker(home);
  if (fs.existsSync(venvPython) && fs.existsSync(markerFile) && fs.readFileSync(markerFile, 'utf-8') === marker) {
    return venvPython;
  }

  log(`Setting up the ReelRecon Python environment in ${venvDir}`);
  log('First run downloads Whisper/torch and friends — this can take a few minutes and a few GB.');
  fs.mkdirSync(home, { recursive: true });
  run(basePython, ['-m', 'venv', '--clear', venvDir], 'Creating the virtualenv');
  run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip', '--quiet'], 'Upgrading pip');
  run(venvPython, ['-m', 'pip', 'install', '-r', requirementsFile], 'Installing Python dependencies');
  fs.writeFileSync(markerFile, marker);
  log('Environment ready.');
  return venvPython;
}

function resolvePython() {
  const custom = process.env.REELRECON_PYTHON;
  if (custom) {
    if (!versionOk(pythonVersion(custom))) {
      fail(`REELRECON_PYTHON (${custom}) is not a working Python >= ${MIN_PYTHON.join('.')}.`);
    }
    return custom;
  }
  return ensureVenv();
}

function warnIfNoFfmpeg() {
  const probe = spawnSync('ffmpeg', ['-version'], { stdio: 'ignore' });
  if (probe.error || probe.status !== 0) {
    log('WARNING: ffmpeg was not found on PATH. Transcription will fail until it is installed.');
    log('         Install it with e.g. `apt install ffmpeg` or `brew install ffmpeg`.');
  }
}

function main() {
  const args = process.argv.slice(2);

  let script = path.join(packageRoot, 'mcp_server.py');
  let scriptArgs = args;
  if (args[0] === 'transcribe') {
    script = path.join(packageRoot, 'transcribe_latest_reel.py');
    scriptArgs = args.slice(1);
  } else if (args[0] === '--version') {
    const pkg = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf-8'));
    process.stdout.write(`${pkg.version}\n`);
    return;
  }

  const python = resolvePython();
  warnIfNoFfmpeg();

  const child = spawn(python, [script, ...scriptArgs], {
    stdio: 'inherit',
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });

  const forward = (signal) => {
    if (!child.killed) {
      child.kill(signal);
    }
  };
  process.on('SIGINT', () => forward('SIGINT'));
  process.on('SIGTERM', () => forward('SIGTERM'));

  child.on('error', (error) => fail(`Failed to start Python: ${error.message}`));
  child.on('exit', (code, signal) => {
    process.exit(signal ? 1 : code ?? 0);
  });
}

main();
