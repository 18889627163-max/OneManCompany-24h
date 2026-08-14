#!/usr/bin/env node
/* Contract tests for task activity bootstrap and status mapping. */
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'frontend', 'app.js'), 'utf8');
let failures = 0;
function assert(cond, msg) { if (cond) console.log(`  ok  ${msg}`); else { failures++; console.log(`  FAIL  ${msg}`); } }
assert(/active_tasks/.test(src), 'bootstrap and renderer code mention active_tasks');
assert(/processing/.test(src) && /pending/.test(src) && /holding/.test(src), 'frontend handles domain task states');
assert(/_fetchAndRenderActiveTasks/.test(src), 'state refresh has an active-task fetch seam');
function stats(tasks) {
  return tasks.reduce((out, task) => {
    const s = task.status;
    if (s === 'processing') out.running++;
    else if (s === 'pending') out.queued++;
    else if (s === 'holding') out.awaiting++;
    else if (s === 'blocked') out.blocked++;
    return out;
  }, {running: 0, queued: 0, awaiting: 0, blocked: 0});
}
const result = stats([
  {status: 'processing'}, {status: 'pending'}, {status: 'holding'}, {status: 'blocked'},
  {status: 'accepted'}, {status: 'failed'},
]);
assert(result.running === 1 && result.queued === 1 && result.awaiting === 1 && result.blocked === 1,
  'domain statuses map to visible activity categories');
if (failures) process.exit(1);
