const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('widget/ui/index.html', 'utf8');
const elements = { cli: {}, req: { value: 'pedido' }, log: {} };
const context = vm.createContext({
  $: id => elements[id], cur: { providers: ['codex'] }, lead: 'claude', sel: '',
  flash: null, wasRunning: true, setTimeout: () => {}, grow: () => {}, open_panel: () => {},
});

async function main() {
  const poll = html.slice(html.indexOf('async function poll()'), html.indexOf('setInterval(draw'));
  vm.runInContext(poll, context);
  for (const status of ['done', 'failed', 'partial', 'stopped', 'interrupted']) {
    context.wasRunning = true;
    context.invoke = async () => ({ providers: [], runActive: false, last: status });
    await context.poll();
    assert.equal(context.flash, status);
  }
  context.wasRunning = false;
  context.flash = null;
  context.invoke = async () => ({ providers: ['codex'], runActive: false, last: null });
  await context.poll();
  context.invoke = async () => ({ providers: [], runActive: false, last: null });
  await context.poll();
  assert.equal(context.flash, null);
  const handler = html.slice(html.indexOf('$("cli").onclick ='), html.indexOf('$("req").onkeydown'));
  vm.runInContext(handler, context);
  context.cur = { providers: ['codex'] };
  context.invoke = async () => { throw new Error('falhou'); };
  await elements.cli.onclick();
  assert.equal(elements.req.value, 'pedido');
  context.invoke = async () => {};
  await elements.cli.onclick();
  assert.equal(elements.req.value, '');
  elements.req.value = 'outro pedido';
  context.cur.providers = ['kimi'];
  await elements.cli.onclick();
  assert.equal(elements.req.value, 'outro pedido');
  assert.match(html, /input:focus-visible/);
  assert.doesNotMatch(html, /\.opts input\s*\{\s*display\s*:\s*none/);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
