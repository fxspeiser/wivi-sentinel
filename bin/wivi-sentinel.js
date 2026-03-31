#!/usr/bin/env node
// Wi-Vi Sentinel — npm package CLI entry point
// Handles setup, start, and status commands after npm install.

const { execSync, spawn } = require('child_process');
const { existsSync, mkdirSync, writeFileSync, copyFileSync } = require('fs');
const path = require('path');
const readline = require('readline');

const PKG_DIR = path.resolve(__dirname, '..');
const INSTALL_DIR = process.env.WIVI_DIR || path.join(process.env.HOME || '/opt', 'wivi-sentinel');

const CYAN = '\x1b[36m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';
const BOLD = '\x1b[1m';
const DIM = '\x1b[2m';
const NC = '\x1b[0m';

function banner() {
  console.log(`
${CYAN}  ╦ ╦┬ ╦┬  ╦┬  ╔═╗┌─┐┌┐┌┌┬┐┬┌┐┌┌─┐┬
  ║║║│─┤╚╗╔╝│  ╚═╗├┤ │││ │ ││││├┤ │
  ╚╩╝┴ ┴ ╚╝ ┴  ╚═╝└─┘┘└┘ ┴ ┴┘└┘└─┘┴─┘${NC}
  ${DIM}WiFi CSI Biometric Detection System${NC}
`);
}

function log(msg) { console.log(`  ${GREEN}✓${NC} ${msg}`); }
function info(msg) { console.log(`  ${CYAN}→${NC} ${msg}`); }
function warn(msg) { console.log(`  ${YELLOW}!${NC} ${msg}`); }

function run(cmd, opts = {}) {
  return execSync(cmd, { stdio: 'inherit', cwd: INSTALL_DIR, ...opts });
}

function runQuiet(cmd, opts = {}) {
  return execSync(cmd, { encoding: 'utf8', cwd: INSTALL_DIR, ...opts }).trim();
}

// ── Commands ────────────────────────────────────────────────────────────────

const commands = {
  setup() {
    banner();
    console.log(`  ${BOLD}Setting up Wi-Vi Sentinel${NC}`);
    console.log(`  ${DIM}Install directory: ${INSTALL_DIR}${NC}\n`);

    // Create install directory and copy files
    if (!existsSync(INSTALL_DIR)) {
      mkdirSync(INSTALL_DIR, { recursive: true });
    }

    info('Copying application files...');
    const filesToCopy = ['server.py', 'requirements.txt', 'start.sh', '.env.example'];
    for (const f of filesToCopy) {
      const src = path.join(PKG_DIR, f);
      const dst = path.join(INSTALL_DIR, f);
      if (existsSync(src)) copyFileSync(src, dst);
    }

    // Copy directories
    for (const dir of ['engine', 'dist']) {
      const src = path.join(PKG_DIR, dir);
      if (existsSync(src)) {
        execSync(`cp -a "${src}" "${INSTALL_DIR}/"`, { stdio: 'pipe' });
      }
    }

    // Make start.sh executable
    const startSh = path.join(INSTALL_DIR, 'start.sh');
    if (existsSync(startSh)) {
      execSync(`chmod +x "${startSh}"`, { stdio: 'pipe' });
    }

    // Data directory
    const dataDir = path.join(INSTALL_DIR, 'data');
    if (!existsSync(dataDir)) mkdirSync(dataDir, { recursive: true });
    const profiles = path.join(dataDir, 'profiles.json');
    if (!existsSync(profiles)) writeFileSync(profiles, '{}');

    log('Application files installed');

    // .env
    const envFile = path.join(INSTALL_DIR, '.env');
    if (!existsSync(envFile)) {
      const example = path.join(INSTALL_DIR, '.env.example');
      if (existsSync(example)) {
        copyFileSync(example, envFile);
      } else {
        writeFileSync(envFile, [
          'FLASK_PORT=5555',
          'VITE_PORT=3000',
          'CSI_SOURCE=simulated',
          'ESP32_SERIAL_PORT=/dev/ttyUSB0',
          'ESP32_BAUD_RATE=921600',
          'PROBE_IFACE=',
        ].join('\n') + '\n');
      }
      log('Default .env created');
    } else {
      info('Existing .env preserved');
    }

    // Python venv
    info('Setting up Python virtual environment...');
    const venvDir = path.join(INSTALL_DIR, 'venv');
    if (!existsSync(venvDir)) {
      try {
        run('python3 -m venv venv');
        log('Virtual environment created');
      } catch {
        warn('python3 -m venv failed — install python3-venv:');
        warn('  sudo apt install python3-venv   (Debian/Ubuntu)');
        warn('  sudo dnf install python3-virtualenv   (Fedora)');
        process.exit(1);
      }
    }

    info('Installing Python dependencies...');
    try {
      run('./venv/bin/pip install --upgrade pip --quiet');
      run('./venv/bin/pip install -r requirements.txt --quiet');
      log('Python dependencies installed');
    } catch (e) {
      warn('pip install failed — you may need build tools:');
      warn('  sudo apt install python3-dev libgfortran5 libopenblas-dev libpcap-dev');
      process.exit(1);
    }

    // ESP32 detection
    console.log('');
    let esp32Port = '/dev/ttyUSB0';
    try {
      const devices = runQuiet('ls /dev/ttyUSB* /dev/ttyACM* /dev/cu.usbserial-* 2>/dev/null || true', { cwd: '/' });
      if (devices) {
        log(`ESP32 serial device detected: ${devices.split('\n')[0]}`);
        esp32Port = devices.split('\n')[0];
      } else {
        warn('No ESP32 USB serial device found — starting in demo mode');
        warn('Plug in ESP32 later and re-run: npx @fxspeiser/wivi-sentinel setup');
      }
    } catch { /* ignore */ }

    // Update .env with detected port
    const envContent = require('fs').readFileSync(envFile, 'utf8');
    const updatedEnv = envContent
      .replace(/^ESP32_SERIAL_PORT=.*/m, `ESP32_SERIAL_PORT=${esp32Port}`)
      .replace(/^CSI_SOURCE=.*/m, `CSI_SOURCE=${esp32Port.includes('tty') || esp32Port.includes('cu.') ? 'esp32' : 'simulated'}`);
    writeFileSync(envFile, updatedEnv);

    // Summary
    console.log('');
    console.log(`  ${GREEN}${BOLD}Setup complete!${NC}`);
    console.log('');
    console.log(`  ${BOLD}Start the server:${NC}`);
    console.log(`    ${CYAN}npx @fxspeiser/wivi-sentinel start${NC}`);
    console.log('');
    console.log(`  ${BOLD}Or with systemd:${NC}`);
    console.log(`    ${CYAN}cd ${INSTALL_DIR} && ./start.sh${NC}`);
    console.log('');
  },

  start() {
    banner();
    if (!existsSync(path.join(INSTALL_DIR, 'server.py'))) {
      warn('Not set up yet. Run setup first:');
      console.log(`    ${CYAN}npx @fxspeiser/wivi-sentinel setup${NC}`);
      process.exit(1);
    }

    const venvPython = path.join(INSTALL_DIR, 'venv', 'bin', 'python3');
    if (!existsSync(venvPython)) {
      warn('Python venv missing. Re-run setup:');
      console.log(`    ${CYAN}npx @fxspeiser/wivi-sentinel setup${NC}`);
      process.exit(1);
    }

    info('Starting Wi-Vi Sentinel...\n');
    const child = spawn(venvPython, ['server.py'], {
      cwd: INSTALL_DIR,
      stdio: 'inherit',
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    });

    process.on('SIGINT', () => { child.kill('SIGINT'); });
    process.on('SIGTERM', () => { child.kill('SIGTERM'); });
    child.on('exit', (code) => process.exit(code || 0));
  },

  status() {
    banner();
    if (!existsSync(path.join(INSTALL_DIR, 'server.py'))) {
      warn('Not installed. Run setup first.');
      process.exit(1);
    }

    console.log(`  ${BOLD}Install directory:${NC}  ${INSTALL_DIR}`);

    // Check if running
    try {
      const res = execSync(`curl -sf http://127.0.0.1:5555/api/status 2>/dev/null`, { encoding: 'utf8', timeout: 3000 });
      const status = JSON.parse(res);
      log(`Server is ${GREEN}running${NC}`);
      info(`CSI source: ${status.csi_source || 'unknown'}`);
      info(`Profiles: ${status.profile_count || 0}`);
    } catch {
      warn('Server is not running');
    }

    // ESP32
    try {
      const devices = runQuiet('ls /dev/ttyUSB* /dev/ttyACM* /dev/cu.usbserial-* 2>/dev/null || true', { cwd: '/' });
      if (devices) {
        log(`ESP32: ${devices.split('\n')[0]}`);
      } else {
        warn('No ESP32 USB device detected');
      }
    } catch { /* ignore */ }

    console.log('');
  },
};

// ── Main ────────────────────────────────────────────────────────────────────

const cmd = process.argv[2] || 'help';

if (commands[cmd]) {
  commands[cmd]();
} else {
  banner();
  console.log(`  ${BOLD}Usage:${NC}  npx @fxspeiser/wivi-sentinel <command>\n`);
  console.log(`  ${BOLD}Commands:${NC}`);
  console.log(`    ${CYAN}setup${NC}    Install and configure Wi-Vi Sentinel`);
  console.log(`    ${CYAN}start${NC}    Start the server`);
  console.log(`    ${CYAN}status${NC}   Check server and ESP32 status`);
  console.log('');
}
