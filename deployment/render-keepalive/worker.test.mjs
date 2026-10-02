import assert from 'node:assert/strict';
import test from 'node:test';
import {isOfficeTime, healthUrl, checkServices} from './worker.mjs';

test('Nepal office-hour boundaries use the 45-minute offset', () => {
  assert.equal(isOfficeTime('2026-10-02T03:14:00Z'), false);
  assert.equal(isOfficeTime('2026-10-02T03:15:00Z'), true);
  assert.equal(isOfficeTime('2026-10-02T12:14:00Z'), true);
  assert.equal(isOfficeTime('2026-10-02T12:15:00Z'), false);
});
test('only public Render health endpoints can be fetched', () => {
  assert.throws(() => healthUrl('http://localhost/api/health', '/api/health'));
  assert.throws(() => healthUrl('https://service.onrender.com.evil.test/api/health', '/api/health'));
  assert.throws(() => healthUrl('https://secret@service.onrender.com/api/health', '/api/health'));
  assert.throws(() => healthUrl('https://service.onrender.com/api/health?secret=value', '/api/health'));
});
test('disabled and after-hours schedules send no requests', async () => {
  const request = () => {throw new Error('Unexpected request');};
  assert.deepEqual(await checkServices(Date.now(), {ENABLED:'false'}, request), []);
  assert.deepEqual(await checkServices('2026-10-02T13:00:00Z', {ENABLED:'true'}, request), []);
});
test('both services checked even when one fetch fails', async () => {
  const urls = [];
  const result = await checkServices('2026-10-02T04:00:00Z', {ENABLED:'true', CONSMAN_HEALTH_URL:'https://consman.onrender.com/api/v1/health/', GATEWAY_HEALTH_URL:'https://gateway.onrender.com/api/health'}, async url => {
    urls.push(url);
    if(url.includes('consman.')) throw new Error('Unavailable');
    return new Response('ok');
  });
  assert.equal(urls.length, 2);
  assert.equal(result[0].ok, false);
  assert.equal(result[1].ok, true);
});
