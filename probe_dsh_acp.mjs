import { spawn } from 'node:child_process';

const command = `${process.env.APPDATA}\\npm\\dsh.cmd`;
const processHandle = spawn(command, ['--profile', 'acp'], {
  cwd: process.env.TEMP,
  env: process.env,
  stdio: ['pipe', 'pipe', 'ignore'],
  shell: true,
});

let buffer = Buffer.alloc(0);
let nextId = 1;
const pending = new Map();
const receivedText = [];
let framingError = false;

function writeMessage(message) {
  processHandle.stdin.write(`${JSON.stringify(message)}\n`);
}

function request(method, params, timeoutMs = 90000) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error('timeout'));
    }, timeoutMs);
    pending.set(id, (message) => {
      clearTimeout(timer);
      if (message.error) {
        const detail = String(message.error.message ?? '');
        let safeDetail = detail;
        const key = process.env.TIANYI_API_KEY;
        if (key) safeDetail = safeDetail.split(key).join('[REDACTED]');
        safeDetail = safeDetail
          .replace(/Bearer\s+\S+/gi, 'Bearer [REDACTED]')
          .replace(/(?:api[_-]?key|token|secret|password)(\s*[:=]\s*)[^,;\s]+/gi, '$1[REDACTED]')
          .replace(/\b[A-Za-z0-9_-]{24,}\b/g, '[REDACTED]')
          .replace(/[\r\n\t]+/g, ' ')
          .slice(0, 160);
        const category = /401|403|invalid.*key|unauthorized|forbidden/i.test(detail) ? 'auth-rejected'
          : /missing|unset|not configured|environment|credential/i.test(detail) ? 'credential-missing'
          : /api.?key|auth/i.test(detail) ? 'auth-other'
          : /404|model|provider|route/i.test(detail) ? 'model-route'
          : /timeout|timed out|fetch failed/i.test(detail) ? 'transport'
          : 'other';
        reject(Object.assign(new Error(`protocol-error-${message.error.code ?? 'unknown'}`), { category, safeDetail }));
      } else resolve(message.result);
    });
    writeMessage({ jsonrpc: '2.0', id, method, params });
  });
}

processHandle.stdout.on('data', (chunk) => {
  buffer = Buffer.concat([buffer, chunk]);
  while (true) {
    const newline = buffer.indexOf('\n');
    if (newline < 0) return;
    const body = buffer.subarray(0, newline).toString('utf8').trim();
    buffer = buffer.subarray(newline + 1);
    if (!body) continue;
    let message;
    try { message = JSON.parse(body); } catch { framingError = true; continue; }
    if (message.id !== undefined && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
    const collectText = (value) => {
      if (Array.isArray(value)) value.forEach(collectText);
      else if (value && typeof value === 'object') {
        for (const [key, nested] of Object.entries(value)) {
          if (key === 'text' && typeof nested === 'string') receivedText.push(nested);
          else collectText(nested);
        }
      }
    };
    collectText(message.params);
  }
});

let stage = 'initialize';
try {
  await request('initialize', {
    protocolVersion: 1,
    clientCapabilities: {},
    clientInfo: { name: 'tianyi-dsh-smoke', version: '1.0' },
  });
  stage = 'session/new';
  const session = await request('session/new', {
    cwd: process.env.TEMP,
    mcpServers: [],
  });
  stage = 'session/prompt';
  await request('session/prompt', {
    sessionId: session.sessionId,
    prompt: [{ type: 'text', text: 'Reply exactly DSH_TIANI_READY' }],
  });
  const text = receivedText.join('\n');
  console.log(`DSH ACP probe: initialized=true session=true marker=${text.includes('DSH_TIANI_READY')} framing_error=${framingError}`);
  process.exitCode = text.includes('DSH_TIANI_READY') && !framingError ? 0 : 4;
} catch (error) {
  const reason = error.message === 'timeout' ? 'timeout' : error.message.startsWith('protocol-error-') ? error.message : 'protocol';
  const category = error.category ? ` category=${error.category}` : '';
  const detail = error.safeDetail ? ` detail=${error.safeDetail}` : '';
  console.log(`DSH ACP probe: failed stage=${stage} reason=${reason}${category}${detail}`);
  process.exitCode = 5;
} finally {
  processHandle.stdin.end();
  processHandle.kill();
}
