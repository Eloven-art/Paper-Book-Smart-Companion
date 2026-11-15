import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';

globalThis.location = {search: ''};
const api = await import(new URL('../前端/api/service.js', import.meta.url));
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


test('reader preserves original text separately from adapted narration and object contours', async () => {
  const object = {name:'月亮',bbox:[0,0,10,10],contours:[[[0,0],[10,0],[5,10]]]};
  globalThis.fetch = async url => response(url.endsWith('/pages') ? {pages:[{page_no:1,ocr_text:{full_text:'原书文字'},narration:'改写旁白',objects:[object]}]} : {book_id:'demo',title:'绘本'});
  const book = await api.books.load('demo');
  assert.equal(book.pages[0].text,'原书文字');
  assert.equal(book.pages[0].narrationText,'改写旁白');
  assert.deepEqual(book.pages[0].objects,[object]);
});


test('question cancellation reaches the request signal', async () => {
  const controller = new AbortController(); controller.abort();
  globalThis.fetch = async (url, options) => { assert.equal(options.signal.aborted, true); throw new DOMException('stopped','AbortError'); };
  await assert.rejects(api.conversation.ask({bookId:'demo',pageNo:1,question:'问题',sessionId:'s',signal:controller.signal}), e => e.name === 'AbortError');
});


test('narration pauses and resumes the same audio frame', async () => {
  const instances=[];globalThis.Audio=class{constructor(url){this.url=url;this.currentTime=0;this.paused=true;instances.push(this)}play(){this.paused=false;return Promise.resolve()}pause(){this.paused=true}};
  await api.narration.play('/one.wav');instances[0].currentTime=4.5;api.narration.pause();assert.equal(api.narration.playing,false);await api.narration.play('/one.wav');assert.equal(instances.length,1);assert.equal(instances[0].currentTime,4.5);assert.equal(api.narration.playing,true);await api.narration.play('/two.wav');assert.equal(instances.length,2);assert.equal(instances[0].paused,true);api.narration.stop();
});
