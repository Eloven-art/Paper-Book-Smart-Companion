import { loadLocalBooks, importLocalBook, saveLocalBook, renderLocalPage } from './local-library.js';
// 只供 ?preview=1 和在线前端试用使用。固定数据明确标为预演，不代替真实 API。
let libraryPromise;
const memory = { books: null, tasks: new Map(), turns: new Map() };
const copy = value => structuredClone(value);
const error = (message, code = 'PREVIEW_UNAVAILABLE') => Object.assign(new Error(message), { code });
async function library() {
  if (!libraryPromise) libraryPromise=(async()=>{
    const r=await fetch('assets/preview/demo-book.json');if(!r.ok)throw error('示例素材没有载入，请刷新后重试。');
    const book=await r.json(),store=new Map([[book.book_id,book]]);
    try { for(const local of await loadLocalBooks())store.set(local.book_id,local); } catch(e) { console.info('本机书库暂不可用：',e.message); }
    memory.books=store;return store;
  })().catch(e=>{libraryPromise=null;throw e;});
  return libraryPromise;
}
function summary(book) {
  return { book_id: book.book_id, title: book.title, page_count: book.pages.length, age_range: book.age_range,
    local_upload:!!book.local_upload,persistent:!!book.persistent,supporting_files:(book.supporting_files||[]).map(item=>({...item})),has_narration: book.pages.some(p => p.narration), video: { ready: !!book.video?.url, ...book.video } };
}
export async function previewRequest(path, { body, signal } = {}) {
  signal?.throwIfAborted();
  const url = new URL(path, 'https://preview.local');
  if (url.pathname === '/health') return { mode: 'preview', frontend_only: true, services: {} };
  const store = await library(); signal?.throwIfAborted();
  if (url.pathname === '/books' && !(body instanceof FormData)) return { books: [...store.values()].map(summary) };
  if (url.pathname === '/books' && body instanceof FormData) {
    const files=body.getAll('pages');if(!files.length)throw error('请选择整本 PDF 或页面图片。');
    const book=await importLocalBook(files,String(body.get('title')||''),signal);store.set(book.book_id,book);
    return {book_id:book.book_id,title:book.title,page_count:book.pages.length,preview:true,persistent:true};
  }
  const assetPath=url.pathname.match(/^\/preview\/page-asset\/([^/]+)\/(\d+)$/);
  if(assetPath){const book=store.get(decodeURIComponent(assetPath[1]));if(!book)throw error('找不到这本绘本。');return copy(await renderLocalPage(book,Number(assetPath[2])));}
  const bookPath = url.pathname.match(/^\/books\/([^/]+)(?:\/pages(?:\/(\d+))?)?$/);
  if (bookPath) {
    const book = store.get(decodeURIComponent(bookPath[1])); if (!book) throw error('本机试用素材已失效，请重新导入图片。', 'BOOK_NOT_FOUND');
    if (bookPath[2]) return copy(book.pages.find(p => p.page_no === Number(bookPath[2])));
    if (url.pathname.endsWith('/pages')) return { pages: copy(book.pages) };
    return { ...summary(book), characters: book.characters || [], local_upload: !!book.local_upload };
  }
  if(url.pathname==='/preview/supporting'){
    const book=store.get(body.book_id);if(!book)throw error('找不到这本书。');
    const item={id:crypto.randomUUID(),name:body.name,type:body.type,file:body.file};
    book.supporting_files=book.supporting_files||[];book.supporting_files.push(item);
    try{await saveLocalBook(book);}catch(e){book.supporting_files.pop();throw e;}
    item.url=URL.createObjectURL(item.file);return {item,persistent:!!book.persistent};
  }
  if(url.pathname==='/preview/supporting/remove'){
    const book=store.get(body.book_id);if(!book)throw error('找不到这本书。');
    const old=book.supporting_files||[];book.supporting_files=old.filter(f=>f.id!==body.id);
    try{await saveLocalBook(book);}catch(e){book.supporting_files=old;throw e;}return {removed:true};
  }
  if (url.pathname === '/preview/page-text') {
    const book = store.get(body.book_id); const page = book?.pages.find(p => p.page_no === body.page_no);
    if (!page) throw error('找不到要编辑的页面。');
    page.ocr_text = { full_text: body.text, blocks: [] }; page.narration = ''; page.scene_summary = body.scene || '';
    page.narration_audio_url = null; page.narration_audio = null;
    if(book.video)book.video.stale=true; if(page.video_meta)page.video_meta.stale=true;
    await saveLocalBook(book);
    return { saved: true, note: '手工录入，没有运行 OCR；原图文字没有被改动。' };
  }
  if (/^\/generate\/(narration|video)$/.test(url.pathname)) {
    const id = 'preview-' + crypto.randomUUID(); memory.tasks.set(id, { started: Date.now(), cancelled: false, kind: url.pathname.split('/').pop() });
    return { task_id: id, preview: true };
  }
  const taskPath = url.pathname.match(/^\/tasks\/([^/]+)(\/cancel)?$/);
  if (taskPath) {
    const task = memory.tasks.get(taskPath[1]); if (!task) throw error('找不到预演任务。');
    if (taskPath[2]) task.cancelled = true;
    const progress = Math.min(1, (Date.now() - task.started) / 1500);
    return { task_id: taskPath[1], status: task.cancelled ? 'cancelled' : progress >= 1 ? 'succeeded' : 'running', progress,
      message: `前端流程预演：${task.kind === 'video' ? '视频任务状态' : '旁白任务状态'}（没有生成新素材）` };
  }
  if (url.pathname === '/asr') throw error('语音识别尚未接 API。可以试听录音，并在下方手工填写转写文字，继续预演提问。', 'NOT_CONNECTED');
  if (url.pathname === '/tts') throw error('回答朗读尚未接 API，暂时不会生成语音。', 'NOT_CONNECTED');
  if (url.pathname === '/qa/ask') {
    const book = store.get(body.book_id); if (!book) throw error('找不到这本绘本。');
    const fixtures = [
      { keywords: ['水里','影子','掉','小河'], page: 2, answer: '月亮没有掉进水里，朵朵看见的是水里的倒影。' },
      { keywords: ['围巾','颜色'], page: 1, answer: '朵朵的围巾是蓝色的。' },
      { keywords: ['最后','山坡'], page: 3, answer: '最后，朵朵和乌龟爷爷一起在山坡上看月亮。' },
    ];
    const match = body.book_id === 'demo_book' && fixtures.find(f => f.keywords.some(k => body.question.includes(k)));
    const page = match && book.pages.find(p => p.page_no === match.page);
    const evidence = page ? [{ type: 'page', page_no: page.page_no, quote: page.ocr_text.full_text, page_image_url: page.page_image_url }] : [];
    if (page?.time_range && page.video_clip_url) evidence.push({ type: 'video', page_no: page.page_no, timestamp: page.time_range[0], time_range: page.time_range });
    const turn = { turn_id: crypto.randomUUID(), session_id: body.session_id, created_at: new Date().toISOString(),
      book_id: book.book_id, page_no: body.page_no, play_time: body.play_time, question_text: body.question,
      answer_text: match ? match.answer : '这条问题没有预设回答。接入问答 API 后，才能根据绘本内容检索并作答。',
      answer_audio_url: null, evidence, grounded: !!match, preview: true, snapshot_url: book.pages.find(p => p.page_no === body.page_no)?.page_image_url };
    const turns = memory.turns.get(body.session_id) || []; turns.push(turn); memory.turns.set(body.session_id, turns);
    return turn;
  }
  if (url.pathname === '/history') return { turns: copy(memory.turns.get(url.searchParams.get('session_id')) || []) };
  if (url.pathname === '/touch/identify') {
    const page = store.get(body.book_id)?.pages.find(p => p.page_no === body.page_no);
    const hits = (page?.objects || []).filter(o => body.x >= o.bbox[0] && body.x <= o.bbox[2] && body.y >= o.bbox[1] && body.y <= o.bbox[3]);
    hits.sort((a,b) => (a.bbox[2]-a.bbox[0])*(a.bbox[3]-a.bbox[1])-(b.bbox[2]-b.bbox[0])*(b.bbox[3]-b.bbox[1]));
    return { hit: !!hits.length, object: hits[0] || null, frame_size: page?.image_size, reason: '这里还没有物体标注。', audio_url: null };
  }
  throw error('这个操作尚未接入前端预演。');
}
