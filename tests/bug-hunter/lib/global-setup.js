const fs = require('fs');
const { getBugHunterConfig, safeConfigForReport } = require('./env');
const { resetReportDir } = require('./reporting');

async function globalSetup() {
  const config = getBugHunterConfig();
  resetReportDir();
  fs.writeFileSync(
    config.metadataFile,
    JSON.stringify({
      startedAt: new Date().toISOString(),
      config: safeConfigForReport(config),
      github: {
        sha: process.env.GITHUB_SHA || '',
        ref: process.env.GITHUB_REF || '',
        runId: process.env.GITHUB_RUN_ID || '',
      },
    }, null, 2),
    'utf8',
  );
}

module.exports = globalSetup;
