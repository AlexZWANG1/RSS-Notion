import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, unlinkSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Ensure logs directory exists
const logsDir = join(__dirname, 'logs');
mkdirSync(logsDir, { recursive: true });

const LOCK_FILE = join(logsDir, '.lock');
const today = new Date().toISOString().split('T')[0];
const logFile = join(logsDir, `${today}.log`);

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  process.stdout.write(line);
  writeFileSync(logFile, line, { flag: 'a' });
}

// Concurrent run protection (atomic: 'wx' flag fails if file exists)
try {
  writeFileSync(LOCK_FILE, new Date().toISOString(), { flag: 'wx' });
} catch (err) {
  if (err.code === 'EEXIST') {
    const lockTime = readFileSync(LOCK_FILE, 'utf8').trim();
    log(`ERROR: Lock file exists (created ${lockTime}). Another instance may be running. Exiting.`);
    process.exit(1);
  }
  throw err;
}

// Cleanup lock on exit
function cleanup() {
  try { unlinkSync(LOCK_FILE); } catch {}
}
process.on('exit', cleanup);
process.on('SIGINT', () => { cleanup(); process.exit(130); });
process.on('SIGTERM', () => { cleanup(); process.exit(143); });

// Check FOLO_SESSION_TOKEN
const config = JSON.parse(readFileSync(join(__dirname, 'config.json'), 'utf8'));
const tokenEnv = config.folo.session_token_env;
if (!process.env[tokenEnv]) {
  log(`ERROR: Environment variable ${tokenEnv} is not set. Exiting.`);
  cleanup();
  process.exit(1);
}

// Build prompt from template
let prompt = readFileSync(join(__dirname, 'prompt.md'), 'utf8');
prompt = prompt.replaceAll('{{INBOX_DATA_SOURCE}}', config.notion.inbox_data_source);
prompt = prompt.replaceAll('{{CONFIG_PAGE_ID}}', config.notion.config_page_id);
prompt = prompt.replaceAll('{{RESEARCH_DATA_SOURCE}}', config.notion.research_database_data_source);
prompt = prompt.replaceAll('{{RELEVANCE_THRESHOLD}}', String(config.schedule.relevance_threshold));
prompt = prompt.replaceAll('{{MAX_SELECTED}}', String(config.schedule.max_selected));
prompt = prompt.replaceAll('{{MAX_RSS_ARTICLES}}', String(config.schedule.max_rss_articles));
prompt = prompt.replaceAll('{{TODAY}}', today);

// Read mode: 'false' = only unread (default), pass --read-all flag to include read articles
const readAll = process.argv.includes('--read-all');
prompt = prompt.replaceAll('{{READ_MODE}}', readAll ? 'true' : 'false');

// Determine run ID based on current hour (上午 for morning, 下午 for afternoon/evening)
const currentHour = new Date().getHours();
const runId = currentHour < 14 ? '上午' : '下午';
prompt = prompt.replaceAll('{{RUN_ID}}', runId);

// Write prompt to temp file to avoid shell escaping issues
const promptFile = join(logsDir, `${today}-prompt.txt`);
writeFileSync(promptFile, prompt);

log(`Starting daily digest for ${today}`);
log(`Inbox: ${config.notion.inbox_data_source}`);

// Remove CLAUDECODE env to allow nested claude invocation
const env = { ...process.env };
delete env.CLAUDECODE;

// Call claude CLI via spawn with prompt piped through stdin
const model = config.model || 'claude-sonnet-4-6';
log(`Using model: ${model}`);

const child = spawn('claude', [
  '-p', '-',
  '--dangerously-skip-permissions',
  '--output-format', 'text',
  '--model', model
], {
  timeout: 900000, // 15 minutes
  env,
  shell: true,
  stdio: ['pipe', 'pipe', 'pipe']
});

// Pipe prompt via stdin
child.stdin.write(prompt);
child.stdin.end();

let stdout = '';
let stderr = '';

child.stdout.on('data', (data) => { stdout += data.toString(); });
child.stderr.on('data', (data) => { stderr += data.toString(); });

child.on('close', (code) => {
  if (stdout) {
    log('--- Claude Output ---');
    writeFileSync(logFile, stdout + '\n', { flag: 'a' });
  }
  if (stderr) {
    log('--- Claude Errors ---');
    writeFileSync(logFile, stderr + '\n', { flag: 'a' });
  }
  if (code !== 0) {
    log(`ERROR: Claude CLI exited with code ${code}`);
    process.exitCode = 1;
  } else {
    log('Daily digest completed successfully.');
  }
});

child.on('error', (err) => {
  log(`ERROR: Failed to start Claude CLI: ${err.message}`);
  process.exitCode = 1;
});
