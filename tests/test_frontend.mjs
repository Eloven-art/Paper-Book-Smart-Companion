import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';

globalThis.location = {search: ''};
const source = await readFile(new URL('../前端/api/service.js', import.meta.url), 'utf8');
const api = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const response = data => new Response(JSON.stringify({status:'ok', data}), {status:200});

test('books map backend pages and media into the reader', async () => {
  globalThis.fetch = async url => url.endsWith('/pages') ? response({pages:[{
    page_no:1, narration:'旁白', page_image_url:'/image.png', video_clip_url:'/clip.mp4',
    narration_audio_url:'/audio.wav', narration_audio:{is_placeholder:true}, objects:[]
  }]}) : response({book_id:'demo', title:'绘本'});
  const book = await api.books.load('demo');
  assert.equal(book.pages[0].image, '/image.png');
  assert.equal(book.pages[0].video, '/clip.mp4');
  assert.equal(book.pages[0].audioPlaceholder, true);
});

test('non-success HTTP and 501 errors remain visible', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({status:'error', error:{code:'NOT_IMPLEMENTED', message:'待实现'}, run_id:'r1'}), {status:501});
  await assert.rejects(api.request('/qa/ask'), e => e.code === 'NOT_IMPLEMENTED' && e.runId === 'r1');
});

test('generation submits narration then video and reloads the book', async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(url);
    if (url.includes('/generate/')) { assert.equal(JSON.parse(options.body).book_id,'demo'); return response({task_id:'task'}); }
    if (url.includes('/tasks/')) return response({status:'succeeded'});
    if (url.endsWith('/pages')) return response({pages:[]});
    return response({book_id:'demo', title:'绘本'});
  };
  await api.story.generate({bookId:'demo', onStage:()=>{}});
  assert.deepEqual(calls.slice(0,4), ['/api/v1/generate/narration','/api/v1/tasks/task','/api/v1/generate/video','/api/v1/tasks/task']);
});

test('cancelled polling makes no new request', async () => {
  const controller = new AbortController(); controller.abort();
  globalThis.fetch = () => { throw Error('不应发起请求'); };
  await assert.rejects(api.tasks.wait('task', {signal:controller.signal}), e => e.name === 'AbortError');
});
