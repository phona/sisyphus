const http = require('http');
const { spawn } = require('child_process');
const fs = require('fs');

const PORT = parseInt(process.env.WRAPPER_PORT || '3005');
const DEFAULT_CWD = process.env.DEFAULT_CWD || '/projects/ttpos-server-go';
const DEFAULT_TIMEOUT = parseInt(process.env.DEFAULT_TIMEOUT || '300000');

// 确保有非 root 用户
try { require('child_process').execSync('grep -q developer /etc/passwd || adduser -D -s /bin/sh developer'); } catch(e) {}
try { fs.mkdirSync('/data', {recursive:true}); fs.mkdirSync('/projects', {recursive:true}); } catch(e) {}
try { require('child_process').execSync('chown -R developer:developer /data /projects 2>/dev/null || true'); } catch(e) {}

function jsonResponse(res, statusCode, body) {
  res.writeHead(statusCode, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

function runClaude(prompt, cwd, timeout) {
  return new Promise((resolve, reject) => {
    const tmpFile = '/tmp/claude-prompt-' + Date.now() + '.txt';
    fs.writeFileSync(tmpFile, prompt);

    const cmd = `/bin/busybox su -s /bin/bash developer -c "HOME=/root claude -p --dangerously-skip-permissions < ${tmpFile}"`;
    const child = spawn('/bin/sh', ['-c', cmd], {
      cwd,
      env: {
        ...process.env,
        ANTHROPIC_BASE_URL: process.env.ANTHROPIC_BASE_URL || '',
        ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || '',
        ANTHROPIC_MODEL: process.env.ANTHROPIC_MODEL || 'claude-sonnet-4-6-20250514'
      },
      stdio: ['ignore', 'pipe', 'pipe']
    });

    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let responded = false;

    // Use 80% of the requested timeout to ensure we respond before n8n's HTTP timeout
    const safeTimeout = Math.floor(timeout * 0.8);
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGTERM');
      setTimeout(() => {
        if (!child.killed) child.kill('SIGKILL');
      }, 3000);
    }, safeTimeout);

    // Safety timeout: force respond after timeout + 10s grace
    const safetyTimer = setTimeout(() => {
      if (!responded) {
        responded = true;
        try { child.kill('SIGKILL'); } catch(e) {}
        try { fs.unlinkSync(tmpFile); } catch(e) {}
        resolve({
          exitCode: -1,
          output: stdout.trim(),
          stderr: stderr.trim() || 'Process timed out and was force killed',
          timedOut: true
        });
      }
    }, timeout + 10000);

    child.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    child.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    child.on('close', (code) => {
      clearTimeout(timer);
      clearTimeout(safetyTimer);
      if (!responded) {
        responded = true;
        try { fs.unlinkSync(tmpFile); } catch(e) {}
        resolve({
          exitCode: code,
          output: stdout.trim(),
          stderr: stderr.trim(),
          timedOut
        });
      }
    });

    child.on('error', (err) => {
      clearTimeout(timer);
      clearTimeout(safetyTimer);
      if (!responded) {
        responded = true;
        try { fs.unlinkSync(tmpFile); } catch(e) {}
        reject(err);
      }
    });
  });
}

const server = http.createServer(async (req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.end();
  }

  // Health check
  if (req.method === 'GET' && req.url === '/health') {
    return jsonResponse(res, 200, {
      status: 'ok',
      claude: 'available'
    });
  }

  // Execute
  if (req.method === 'POST' && req.url === '/execute') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', async () => {
      try {
        const { prompt, cwd, timeout } = JSON.parse(body);

        if (!prompt) {
          return jsonResponse(res, 400, { error: 'prompt is required' });
        }

        console.log(`[execute] prompt length: ${prompt.length}, cwd: ${cwd || DEFAULT_CWD}`);

        const result = await executeClaude(
          prompt,
          cwd || DEFAULT_CWD,
          timeout || DEFAULT_TIMEOUT
        );

        console.log(`[execute] done, exitCode: ${result.exitCode}`);

        return jsonResponse(res, 200, {
          success: result.exitCode === 0 && !result.timedOut,
          exitCode: result.exitCode,
          output: result.output,
          stderr: result.stderr,
          timedOut: result.timedOut
        });
      } catch (err) {
        console.error('[execute] error:', err.message);
        return jsonResponse(res, 500, {
          success: false,
          error: err.message
        });
      }
    });
    return;
  }

  jsonResponse(res, 404, { error: 'Not Found' });
});

server.listen(PORT, () => {
  console.log(`[wrapper] Claude Code HTTP wrapper listening on :${PORT}`);
  console.log(`[wrapper] DEFAULT_CWD=${DEFAULT_CWD}, DEFAULT_TIMEOUT=${DEFAULT_TIMEOUT}ms`);
});
