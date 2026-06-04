const { generateBugHunterReport } = require('./report');
const { redact } = require('./reporting');

class BugHunterReporter {
  constructor() {
    this.startedAt = Date.now();
    this.tests = [];
  }

  onTestEnd(test, result) {
    this.tests.push({
      title: test.titlePath().join(' > '),
      status: result.status,
      expectedStatus: result.expectedStatus,
      durationMs: result.duration,
      retry: result.retry,
      error: result.error ? redact(result.error.message || String(result.error)) : '',
    });
  }

  async onEnd(result) {
    generateBugHunterReport({
      tests: this.tests,
      result: {
        status: result.status,
        durationMs: Date.now() - this.startedAt,
      },
    });
  }
}

module.exports = BugHunterReporter;
