import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { zstdDecompressSync } from 'node:zlib';

const root = join(process.env.USERPROFILE, '.dsh', 'sessions', '--C-Users-admin-.ao-data-worktrees-mh-pilot-mh-pilot-10--', '40f26615-250d-4e31-ba78-37dcdcd2f9c5');
const log = zstdDecompressSync(readFileSync(join(root, 'session.jsonl.zstd'))).toString('utf8');
const marker = 'DSH_AO_TIANI_READY';
const lines = log.split(/\r?\n/).filter(Boolean);
console.log(`dsh_log_lines=${lines.length} marker_present=${log.includes(marker)}`);
for (const line of lines) {
  let record;
  try { record = JSON.parse(line); } catch { continue; }
  const fields = Object.keys(record).join(',');
  const safe = { type: record.type ?? record.event ?? record.kind ?? 'unknown', keys: fields.slice(0, 160) };
  const text = JSON.stringify(record);
  safe.marker = text.includes(marker);
  if (record.role) safe.role = record.role;
  if (record.stopReason) safe.stopReason = record.stopReason;
  if (record.errorCode) safe.errorCode = record.errorCode;
  if (record.error && typeof record.error === 'object' && record.error.code) safe.errorCode = record.error.code;
  console.log(`record ${JSON.stringify(safe)}`);
}
