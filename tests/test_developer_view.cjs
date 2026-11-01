'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

const source = readFileSync(join(__dirname, '..', 'web', 'app.js'), 'utf8');
const begin = source.indexOf('window.PanxuDeveloperView = (() => {');
const end = source.indexOf('function initializePanxu()', begin);
assert.ok(begin !== -1 && end > begin);
const window = {};
runInNewContext(source.slice(begin, end), { window });
const viewer = window.PanxuDeveloperView;

function entry(kind, protocol, payload) {
  // This is the real DeveloperTrace.record() envelope, not only the native body.
  return { kind, seq: 1, payload: { provider: 'fixture', protocol,
    endpoint: 'http://127.0.0.1:1234/v1', payload } };
}

const context = JSON.stringify({ goal: '调查测试目录', facts: { total: 2, items: [{ summary: '历史事实' }] },
  observations: [{ summary: '历史观察' }], user_choices: [{ choice: 'keep', name: '保留资料' }] });

function request(protocol) {
  if (protocol === 'chat_completions') return { model: 'fixture', messages: [
    { role: 'system', content: '系统提示原文' }, { role: 'user', content: context }, { role: 'user', content: '用户的自然语言补充' },
  ], tools: [{ type: 'function', function: { name: 'inspect_items' } }] };
  if (protocol === 'responses') return { model: 'fixture', input: [
    { role: 'system', content: '系统提示原文' }, { role: 'user', content: [{ type: 'input_text', text: context }] },
    { role: 'user', content: '用户的自然语言补充' },
  ], tools: [{ type: 'function', name: 'inspect_items' }] };
  if (protocol === 'anthropic') return { model: 'fixture', system: '系统提示原文', messages: [
    { role: 'user', content: [{ type: 'text', text: context }, { type: 'text', text: '用户的自然语言补充' }] },
  ], tools: [{ name: 'inspect_items', input_schema: {} }] };
  return { model: 'fixture', system_instruction: { parts: [{ text: '系统提示原文' }] }, contents: [
    { role: 'user', parts: [{ text: context }, { text: '用户的自然语言补充' }] },
  ], tools: [{ functionDeclarations: [{ name: 'inspect_items' }] }] };
}

function reply(protocol) {
  if (protocol === 'chat_completions') return { choices: [{ message: { role: 'assistant', content: '助手公开正文',
    reasoning_content: 'private-canary', tool_calls: [{ id: 'call-1', function: { name: 'ask_user', arguments: JSON.stringify({
      question: '还需要离线使用吗？', options: [{ id: 'yes', label: '需要', description: '保留离线副本' }],
    }) } }] } }] };
  if (protocol === 'responses') return { output: [
    { type: 'reasoning', encrypted_content: 'private-canary' },
    { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: '助手公开正文' }] },
  ] };
  if (protocol === 'anthropic') return { role: 'assistant', content: [
    { type: 'thinking', thinking: 'private-canary', signature: 'signature-canary' }, { type: 'text', text: '助手公开正文' },
  ] };
  return { candidates: [{ content: { role: 'model', parts: [
    { thought: true, text: 'private-canary', thoughtSignature: 'signature-canary' }, { text: '助手公开正文' },
  ] } }] };
}

for (const protocol of ['chat_completions', 'responses', 'anthropic', 'gemini']) {
  test(`${protocol}: real wrapped request separates prose, system, task context, facts and definitions`, () => {
    const view = viewer.describe(entry('request', protocol, request(protocol)));
    assert.ok(view.messages.some((item) => item.role === 'user' && item.text === '调查测试目录'));
    assert.ok(view.messages.some((item) => item.text === '用户的自然语言补充'));
    assert.ok(view.sections.some((item) => item.kind === 'system' && item.data === '系统提示原文'));
    assert.ok(view.sections.some((item) => item.title === '任务上下文'));
    assert.ok(view.sections.some((item) => item.kind === 'facts'));
    assert.ok(view.sections.some((item) => item.title === '工具定义' && item.kind === 'tools'));
    assert.equal(view.meta.protocol, protocol);
    assert.equal(view.meta.model, 'fixture');
    assert.ok(!view.messages.some((item) => item.text.startsWith('{')), 'task JSON is not presented as a chat paragraph');
  });

  test(`${protocol}: real wrapped reply preserves public prose but not private reasoning or signatures`, () => {
    const record = entry('response', protocol, reply(protocol)), view = viewer.describe(record);
    assert.ok(view.messages.some((item) => item.role === 'assistant' && item.text === '助手公开正文'));
    assert.ok(!JSON.stringify(view.messages).includes('private-canary'));
    assert.ok(!viewer.raw(record).includes('private-canary'));
    assert.ok(!viewer.raw(record).includes('signature-canary'));
    if (protocol === 'chat_completions') {
      const question = view.messages.find((item) => item.publicCall === 'ask_user');
      assert.equal(question.text, '还需要离线使用吗？');
      assert.equal(question.options[0].description, '保留离线副本');
    }
  });
}

test('wrapped skill results retain skill category and provenance instead of falling into other tools', () => {
  const record = { kind: 'tool_result', payload: { payload: { name: 'read_skill', result: {
    content: '技能指导正文', version: 'fixture-v1', content_fingerprint: 'fixture-sha256',
    sources: [{ title: '官方资料', url: 'https://example.invalid/source' }],
  } } } };
  assert.equal(viewer.category(record), 'skills');
  const section = viewer.describe(record).sections[0];
  assert.equal(section.kind, 'skills'); assert.equal(section.data.content, '技能指导正文');
  assert.equal(section.data.version, 'fixture-v1');
  assert.equal(section.data.sources[0].title, '官方资料');
  assert.equal(viewer.category({ kind: 'tool_result', payload: { payload: { name: 'inspect_items' } } }), 'tools');
});

test('unknown and capture-truncated records state their limits without inventing conversation', () => {
  const unknown = viewer.describe({ kind: 'response', payload: { unsupported: { field: 'data' } } });
  assert.equal(unknown.messages.length, 0);
  assert.ok(unknown.notes.some((note) => note.includes('尚未识别')));
  const truncated = viewer.describe({ kind: 'response', payload: { truncated: true, preview: 'partial JSON' } });
  assert.equal(truncated.messages.length, 0);
  assert.ok(truncated.notes.some((note) => note.includes('采集体积上限截断')));
});

test('turn_end is readable model-receive status with safe layered fields, never task completion', () => {
  const record=entry('turn_end','chat_completions',{turn_id:'turn-fixture',delivery:'sse',status:'completed',
    text_chars:42,tool_count:2,usage:{prompt_tokens:15,completion_tokens:8,total_tokens:23,
      reasoning_content:'usage-private-canary',untrusted_description:'not-a-token-count'},
    thinking:'private-canary',signature:'signature-canary',text:'do-not-invent-a-second-reply'});
  const view=viewer.describe(record);
  assert.equal(view.messages.length,1);
  assert.equal(view.messages[0].role,'system');
  assert.equal(view.messages[0].text,'本轮接收完成');
  assert.ok(view.notes.some(note=>note.includes('一轮模型回复')&&note.includes('任务记录和核验结果')));
  assert.ok(!view.notes.some(note=>note.includes('尚未识别')));
  const detail=view.sections.find(section=>section.title==='本轮接收详情').data;
  assert.equal(detail['接收方式'],'流式接收（SSE）');
  assert.equal(detail['正文字符数'],42);assert.equal(detail['工具调用数'],2);
  assert.equal(view.sections.find(section=>section.title==='服务商报告的用量').data.total_tokens,23);
  const serialized=JSON.stringify(view);
  for(const canary of ['private-canary','signature-canary','usage-private-canary','not-a-token-count','do-not-invent-a-second-reply'])assert.ok(!serialized.includes(canary));
});

test('turn_end explains JSON fallback and omits absent or invalid counters instead of guessing zero', () => {
  for(const delivery of ['json_fallback','disabled']){
    const view=viewer.describe(entry('turn_end','responses',{turn_id:'turn-2',delivery,status:'completed',tool_count:0}));
    assert.ok(!view.notes.some(note=>note.includes('尚未识别')));
    const detail=view.sections.find(section=>section.title==='本轮接收详情').data;
    assert.equal(detail['工具调用数'],0);assert.equal(Object.hasOwn(detail,'正文字符数'),false);
    assert.ok(!view.sections.some(section=>section.title==='服务商报告的用量'));
    assert.ok(detail['接收方式'].includes(delivery==='json_fallback'?'完整 JSON':'未启用'));
  }
  const invalid=viewer.describe(entry('turn_end','gemini',{delivery:'private-unknown-delivery',status:'error',text_chars:-1,tool_count:'4',usage:{input_tokens:'secret'}}));
  assert.equal(invalid.messages[0].text,'本轮接收未完成');
  assert.ok(!invalid.sections.some(section=>section.title==='本轮接收详情'||section.title==='服务商报告的用量'));
  assert.ok(!JSON.stringify(invalid).includes('private-unknown-delivery'));
});

test('raw JSON stays optional, sanitized and bounded, including JSON inside tool argument strings', () => {
  const record = entry('response', 'chat_completions', { choices: [{ message: { tool_calls: [{ function: {
    name: 'present_findings', arguments: JSON.stringify({ summary: '公开结论', reasoning_content: 'nested-private-canary' }),
  } }] } }], authorization: 'credential-canary', large: 'x'.repeat(100000) });
  const raw = viewer.raw(record);
  assert.ok(!raw.includes('nested-private-canary')); assert.ok(!raw.includes('credential-canary'));
  assert.ok(raw.includes('已截断')); assert.ok(raw.length < 60500);
  const config = viewer.clean({ thinking: { type: 'enabled', budget_tokens: 1000 } });
  assert.equal(config.thinking.type, 'enabled', 'public configuration is distinct from private reasoning');
});

test('long transcripts and advice lists expose explicit presentation limits', () => {
  const view = viewer.describe(entry('request', 'chat_completions', { messages: Array.from({ length: 50 }, (_, i) => ({ role: 'user', content: `message-${i}` })) }));
  assert.equal(view.messages.length, 12); assert.ok(view.notes.some((note) => note.includes('前 12 条')));
  assert.ok(view.notes.some((note) => note.includes('前 20 条')));
  const advice = viewer.describe(entry('response', 'anthropic', { content: [{ type: 'tool_use', name: 'record_advice', input: {
    items: Array.from({ length: 30 }, (_, i) => ({ id: `item-${i}`, purpose: '已观察用途', reason: '保留依据' })),
  } }] }));
  assert.equal(advice.messages[0].items.length, 12); assert.equal(advice.messages[0].omittedItems, 18);
});
