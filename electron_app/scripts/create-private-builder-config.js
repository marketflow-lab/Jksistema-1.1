'use strict';

const fs = require('fs');
const path = require('path');

function fail(message) {
  process.stderr.write(`PRIVATE_BUILDER_CONFIG_ERROR: ${message}\n`);
  process.exit(1);
}

const appDir = path.resolve(__dirname, '..');
const packagePath = path.join(appDir, 'package.json');
const bundlePath = path.resolve(process.argv[2] || path.join(appDir, 'private_build', 'credentials.bundle.json'));
const outputDir = path.resolve(process.argv[3] || path.join(appDir, '..', 'private-release'));
const configPath = path.resolve(process.argv[4] || path.join(appDir, 'private_build', 'electron-builder.private.json'));

let bundleStat;
try {
  bundleStat = fs.statSync(bundlePath);
} catch (_err) {
  fail('cofre criptografado ausente');
}
if (!bundleStat.isFile() || bundleStat.size <= 0 || bundleStat.size > 6 * 1024 * 1024) {
  fail('cofre criptografado invalido');
}

const packageJson = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
const build = JSON.parse(JSON.stringify(packageJson.build || {}));
build.directories = { ...(build.directories || {}), output: path.relative(appDir, outputDir).replace(/\\/g, '/') };
build.artifactName = `JK-Sistema-Cliente-Privado-Setup-${packageJson.version}.\${ext}`;
build.win = {
  ...(build.win || {}),
  artifactName: `JK-Sistema-Cliente-Privado-Setup-${packageJson.version}.\${ext}`
};
build.publish = [];
build.extraResources = [
  ...(Array.isArray(build.extraResources) ? build.extraResources : []),
  {
    from: path.relative(appDir, bundlePath).replace(/\\/g, '/'),
    to: 'private_bootstrap/credentials.bundle.json'
  }
];

fs.mkdirSync(path.dirname(configPath), { recursive: true });
fs.writeFileSync(configPath, `${JSON.stringify(build, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
process.stdout.write(JSON.stringify({ version: packageJson.version, bundleBytes: bundleStat.size }) + '\n');
