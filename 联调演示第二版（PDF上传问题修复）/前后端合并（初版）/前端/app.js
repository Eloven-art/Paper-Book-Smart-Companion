import { createReadingTools } from './ui/reading-tools.js';
import { cloneVoice } from './api/voice.js';
import { openVoicePanel } from './ui/voice-panel.js';
import { config, books, pdfImports, story, localDemo, bookshelf, narration, conversation, touch, challenge, health, interaction, readingProgress } from './api/service.js';
const $ = s => document.querySelector(s), dialog = $('#dialog'), content = $('#content');
const state = { book: null, page: 0, task: null, status: 'idle', history: [], lastAnimal: null, cleanup: null, cloned: null, timer: null, quiz: 0, score: 0, answers: [], target: null, sessionId: 'session-' + Math.random().toString(36).slice(2, 10), backend: null };
const modeLabel = () => config.mode === 'preview' ? '前端试用 · 尚未接 API' : config.mode === 'mock' ? '假接口联调' : state.backend === false ? '后端未连接' : '已连接后端';
function el(tag, text, cls) { const e = document.createElement(tag); if (text) e.textContent = text; if (cls) e.className = cls; return e; }
function button(text, action, cls) { const b = el('button', text, cls); b.type = 'button'; b.onclick = action; return b; }
function toast(text) { clearTimeout(state.timer); $('#toast').textContent = text; $('#toast').classList.add('on'); state.timer = setTimeout(() => $('#toast').classList.remove('on'), 3500); }
function close() { dialog.close(); }
function show(kind, title, intro) { state.cleanup?.(); state.cleanup = null; content.dataset.view = ''; content.replaceChildren(); $('#paper').className = 'paper ' + ({ fox: 'scroll', bear: 'voice', owl: 'letter' }[kind] || ''); content.append(el('small', '纸书智伴 · ' + modeLabel()), el('h2', title)); if (intro) content.append(el('p', intro)); if (!dialog.open) dialog.showModal(); }
function actions(...items) { const d = el('div', '', 'actions'); d.append(...items); content.append(d); }
function feedback(id, text) { close(); const a = document.querySelector(`[data-open="${id}"]`); a?.classList.add('selected'); setTimeout(() => a?.classList.remove('selected'), 900); toast(text); }
let readerTools;
readerTools = createReadingTools({
  root: $('#reading-workbench'), getContext: () => ({ book: state.book, pageIndex: state.page, sessionId: state.sessionId, selectedObject: state.selectedObject, playTime }),
  mainVideo: $('#page-video'), showVideo, isVideoMode: () => state.mediaMode === 'video',
  navigate: goToPage, highlight: highlightOriginal, pause: stopAll, notice: toast,
  saveText: async (text, scene) => { const id = state.book.id, no = state.book.pages[state.page].no; await interaction.savePreviewText(id, no, text, scene); state.book = await books.load(id); renderPage(); },
});
async function openReadingTool(tool) {
  try {
    if (!state.book) { const list = await books.list(); if (!list.length) { rabbit(); return; } const first = await books.load(list[0].book_id); openBook(first); }
    else if ($('#reader').hidden) { $('#home').hidden = true; $('#reader').hidden = false; renderPage(); }
    else { stopAll(); readerTools.update(); }
    $('#reading-workbench').hidden=false; readerTools.select(tool === 'voice' ? 'question' : tool);
    if (tool === 'voice') toast('这里录的是孩子的问题；熟悉的声音请到小熊的“声音工坊”设置。');
  } catch (e) { toast(e.message); }
}
const errorText = e => e.runId ? `${e.message}（run_id：${e.runId}）` : e.message;

// ---------------------------------------------------------------- 兔子：选书、上传、生成
async function rabbit(options = {}) {
  const managing=options.manage===true;
  if (managing && ['waiting', 'running', 'failed', 'success', 'cancelled'].includes(state.status)) return taskView();
  show('rabbit', managing?'大人准备绘本':'今天读哪一本？', managing?'整本准备一次，孩子以后从书架直接打开。':'选一本故事，我们接着上次的地方读。');
  const list = el('div'); list.append(el('p', '正在读取书架……', 'muted')); content.append(list);
  try {
    const items = await books.list(); state.backend = true; list.replaceChildren();
    if (!items.length) list.append(el('p', '书架还没有绘本，可以在下面上传。', 'muted'));
    for (const b of items) {
      const c = button('', async () => { if(managing){prepare(b);return;} try{const book=await books.load(b.book_id);close();openBook(book);}catch(e){toast(errorText(e));} }, 'choice book-choice');
      const progress=readingProgress.get(b.book_id);
      const tags = [`${b.page_count} 页`, progress?`上次读到第 ${progress.page+1} 页`:'还没读过'];
      c.append(el('span', '书', 'book-icon'), el('strong', b.title), el('small', tags.join(' · '))); list.append(c);
    }
  } catch (e) {
    state.backend = false; list.replaceChildren(el('p', errorText(e), 'error'));
    list.append(button('', () => prepareLocal(), 'choice'));
    list.lastChild.append(el('span', '书', 'book-icon'), el('strong', '本地演示：雪夜里的一盏灯'), el('small', '不连接后端，只走一遍界面流程'));
    return;
  }
  if(!managing){actions(button('大人准备绘本',()=>rabbit({manage:true})));return;}
  if(config.mode==='preview')content.append(el('p','支持整本 PDF，或一次选齐全部页面图片。保存在这个浏览器的书架中，刷新后仍可继续阅读。图片原文识别和物体分割仍等待 API。','muted'));
  if (config.mode === 'mock') { content.append(el('p', '当前是假接口联调模式（地址带 ?mock=1），数据是固定示例，不是生成结果。', 'muted')); }
  if (config.mode === 'real') {
    actions(button('垃圾桶', showPdfTrash));
    const pending = el('div', '', 'actions'); content.append(pending);
    pdfImports.active().then(items => items.forEach(item =>
      pending.append(button('继续上传：' + item.name, () => showPdfUpload(item)))
    )).catch(e => pending.append(el('p', errorText(e), 'error')));
  }
  const form = el('div', '', 'upload');
  const title = document.createElement('input'); title.placeholder = '书名（可不填）'; title.maxLength = 60;
  const label = el('label', config.mode==='mock'?'上传全部绘本页面图片（按选择顺序编页码）':'选择一本 PDF 或全部页面图片（图片按选择顺序）', 'field'), input = document.createElement('input');
  input.type = 'file'; input.accept = config.mode==='mock'?'image/png,image/jpeg,image/webp':'application/pdf,image/png,image/jpeg,image/webp'; input.multiple = true; label.append(input);
  const info = el('p', '', 'error'); info.setAttribute('role', 'alert');
  const send = button('保存到书架', async () => {
    const fs = [...input.files];
    if (!fs.length) { info.textContent = '请先选择整本 PDF 或页面图片。'; return; }
    if (fs.some(f => f.type!=='application/pdf' && !/\.pdf$/i.test(f.name) && f.size > 20 * 1024 * 1024)) { info.textContent = '单张图片请不超过 20 MB。'; return; }
    const pdfs = fs.filter(f => f.type === 'application/pdf' || /\.pdf$/i.test(f.name));
    if (config.mode === 'real' && pdfs.length) {
      if (pdfs.length !== 1 || fs.length !== 1) { info.textContent = '一次请选择一本 PDF；图片可单独批量上传。'; return; }
      send.disabled = true; info.textContent = '正在上传';
      try { const item = await pdfImports.start(pdfs[0], title.value.trim()); if (dialog.open && content.contains(form)) showPdfUpload(item); }
      catch (e) { info.textContent = errorText(e); }
      finally { send.disabled = false; }
      return;
    }
    send.disabled = true; info.textContent = '正在整理整本绘本……';
    const showProgress=e=>info.textContent=e.detail.message;window.addEventListener('pbsc-import-progress',showProgress);
    try { const r=await books.upload(fs,title.value.trim());toast(r.task_id?`《${r.title}》已放入书架，共 ${r.page_count} 页；文字识别已开始。`:`《${r.title}》已放入书架，共 ${r.page_count} 页。`);if(config.mode==='preview'){const book=await books.load(r.book_id);close();openBook(book);}else rabbit({manage:true}); }
    catch(e){info.textContent=errorText(e);}
    finally{send.disabled=false;window.removeEventListener('pbsc-import-progress',showProgress);}
  });
  form.append(label, title, send, info);
  content.append(el('p', '只能上传组委会授权的绘本或队伍自制的模拟绘本。上传后会用本地 RapidOCR 自动识别文字；人物和画面理解仍需后续模块。', 'muted'), form);
}

async function showPdfUpload(item) {
  if (item.status === 'completed') {
    try { const book = await books.load(item.book_id); close(); openBook(book); }
    catch (e) { toast(errorText(e)); rabbit({manage:true}); }
    return;
  }
  if (item.status === 'trash') {
    show('rabbit', '上传结束', item.reason);
    actions(button('查看垃圾桶', showPdfTrash), button('返回', () => rabbit({manage:true})));
    return;
  }
  const paused = item.status === 'paused';
  show('rabbit', paused ? '第 ' + item.page + ' 页上传失败' : '正在上传', paused ? item.reason : null);
  const controller = new AbortController();
  state.cleanup = () => controller.abort();
  const error = el('p', '', 'error'); error.setAttribute('role', 'alert'); content.append(error);
  let replacement;
  if (paused) {
    const label = el('label', '替换当前失败页（可选）', 'field');
    replacement = document.createElement('input');
    replacement.type = 'file'; replacement.accept = 'image/png,image/jpeg,image/webp';
    label.append(replacement); content.append(label);
  }
  let submitting = false;
  const decide = async action => {
    if (submitting || controller.signal.aborted) return;
    submitting = true;
    try {
      const next = await pdfImports.decide(item, action, action === 'retry' ? replacement?.files[0] : undefined);
      if (!controller.signal.aborted) showPdfUpload(next);
    } catch (e) { error.textContent = errorText(e); }
    finally { submitting = false; }
  };
  const cancel = button('取消上传', () => decide('cancel'));
  cancel.disabled = item.status === 'cancelling';
  if (paused) actions(button('跳过', () => decide('skip')), button('重传', () => decide('retry'), 'primary'), cancel);
  else {
    actions(cancel);
    try {
      const next = await pdfImports.wait(item, controller.signal);
      if (!controller.signal.aborted && !submitting) showPdfUpload(next);
    } catch (e) {
      if (!controller.signal.aborted) {
        error.textContent = errorText(e);
        actions(button('重新查看', () => showPdfUpload(item)));
      }
    }
  }
}

async function showPdfTrash() {
  show('rabbit', '垃圾桶', '失败记录和可保留的原文件保存 7 天。');
  const list = el('div'); content.append(list);
  actions(button('返回上传', () => rabbit({manage:true})));
  try {
    const items = await pdfImports.trash();
    if (!items.length) list.append(el('p', '垃圾桶为空。'));
    for (const item of items) {
      const row = el('div', '', 'status-line');
      row.append(el('strong', item.name), el('p', item.reason),
        el('p', '保留至 ' + new Date(item.expires_at * 1000).toLocaleString(), 'muted'));
      if (item.has_source) {
        const link = el('a', '下载原文件');
        link.href = '/api/v1/pdf-trash/' + encodeURIComponent(item.id) + '/original';
        row.append(link, document.createTextNode(' '));
      }
      row.append(button('立即删除', async () => {
        try { await pdfImports.remove(item.id); row.remove(); }
        catch (e) { toast(errorText(e)); }
      }));
      list.append(row);
    }
  } catch (e) { list.append(el('p', errorText(e), 'error')); }
}

function prepare(summary) {
  show('rabbit', summary.title, config.mode === 'preview' ? `${summary.page_count} 页。可直接阅读已有素材；流程预演不会生成新的旁白、音轨或视频。` : `${summary.page_count} 页。生成会依次做：旁白 → 配音 → 视频片段 → 拼接整本。`);
  content.append(el('p', `朗读声音：${state.cloned ? state.cloned.name + '（克隆音色，阅读时使用）' : '使用后端生成的旁白配音'}`));
  actions(button('换个声音', () => bear(() => prepare(summary))));
  const force = el('label', '', 'muted'), forceBox = document.createElement('input'); forceBox.type = 'checkbox';
  force.append(forceBox, document.createTextNode(' 忽略缓存重新生成（会重新调用收费服务）'));
  if (config.mode === 'real') content.append(force);
  let failBox = null;
  if (config.mode === 'mock') { const f = el('label', '', 'muted'); failBox = document.createElement('input'); failBox.type = 'checkbox'; f.append(failBox, document.createTextNode(' 联调：模拟一次失败')); content.append(f); }
  actions(
    button(config.mode === 'preview' ? '预演生成流程' : '开始生成', () => startTask(summary.book_id, { force: forceBox.checked, fail: !!failBox?.checked }), 'primary'),
    button('直接阅读', async () => { try { const book = await books.load(summary.book_id); feedback('rabbit', '翻开绘本。'); openBook(book); } catch (e) { toast(errorText(e)); } }),
    button('返回选书', rabbit));
}

function prepareLocal() {
  show('rabbit', '本地演示', '没有连接后端，下面只演示界面流程，不做任何真实处理。');
  actions(button('开始演示', () => startTask(null, { local: true }), 'primary'), button('返回选书', rabbit));
}

async function startTask(bookId, { force = false, fail = false, local = false } = {}) {
  state.task = new AbortController(); state.status = 'waiting'; state.stage = 0; state.stageText = '等待开始'; state.retry = () => startTask(bookId, { force, fail: false, local }); taskView();
  const onStage = (n, text) => { state.status = n ? 'running' : 'waiting'; state.stage = n; state.stageText = text; if (dialog.open && content.dataset.view === 'task') taskView(); };
  try {
    state.preparedBook = local ? await localDemo.generate({ signal: state.task.signal, onStage }) : await story.generate({ bookId, force, fail, signal: state.task.signal, onStage });
    state.status = 'success'; if (dialog.open && content.dataset.view === 'task') taskView(); else toast('故事准备好了，点兔子翻开绘本。');
  } catch (e) { state.status = e.name === 'AbortError' ? 'cancelled' : 'failed'; state.error = errorText(e); if (dialog.open && content.dataset.view === 'task') taskView(); }
}

function taskView() {
  show('rabbit', state.status === 'success' ? (config.mode === 'preview' ? '前端流程已走完' : '故事准备好了') : state.status === 'failed' ? '这次没有完成' : state.status === 'cancelled' ? '已停止' : '让故事慢慢展开'); content.dataset.view = 'task';
  if (['waiting', 'running'].includes(state.status)) {
    const steps = el('div', '', 'steps'); for (let i = 0; i < 4; i++) steps.append(el('span', '', i <= state.stage ? 'done' : ''));
    content.append(steps, el('p', state.stageText, 'status-line'));
    actions(button('停止', () => state.task.abort()), button('回小屋等一会儿', close));
  } else if (state.status === 'success') {
    const b = state.preparedBook;
    if (config.mode === 'preview') content.append(el('p', '这里只验证提交、进度、停止和完成后的阅读入口。没有调用模型，也没有生成新素材；下面仍是原有绘本内容。', 'status-line'));
    if (b?.source === 'backend') {
      const notes = [];
      if (b.pages.some(p => p.videoFallback)) notes.push('视频是本地降级片段（静态页图缓慢推近），不是 AI 生成的视频');
      if (b.pages.some(p => p.audioPlaceholder)) notes.push('配音是占位提示音，不是真实语音');
      if (b.pages.some(p => p.narrationMethod && p.narrationMethod !== 'llm')) notes.push('旁白直接使用了原文');
      if (notes.length) content.append(el('p', '注意：' + notes.join('；') + '。原因是外部服务未配置或调用失败，详见后端日志。', 'muted'));
    }
    actions(button('翻开绘本', () => { feedback('rabbit', '故事准备好了。'); openBook(state.preparedBook); }, 'primary'), button('重新选书', () => { state.status = 'idle'; rabbit(); }));
  } else {
    content.append(el('p', state.status === 'failed' ? state.error : '已停止。正在进行的那一页处理完后后端才会停下。', 'status-line'));
    actions(button('重试', () => state.retry(), 'primary'), button('返回选书', () => { state.status = 'idle'; rabbit(); }));
  }
}

// ---------------------------------------------------------------- 狐狸：本机书架
async function fox() { await rabbit(); }

// ---------------------------------------------------------------- 熊熊：声音
function bear(returnTo = null) {
  show('bear', '留住一个熟悉的声音'); content.querySelector('small').textContent = '纸书智伴 · 音色服务';
  openVoicePanel({ content, toast, close, onSelected: chosen => { state.cloned = chosen; if (returnTo) setTimeout(returnTo, 0); }, onClosed: cleanup => { state.cleanup = cleanup; } });
}

// ---------------------------------------------------------------- 猫头鹰：小测验（目前只有本地演示书有题）
function owl() {
  state.quizQuestions = challenge.forBook(state.book?.id);
  const has = state.quizQuestions.length > 0;
  show('owl', '故事小挑战', has ? '还记得故事里的事吗？来试试这几道小题吧。' : state.book ? '这本书还没有题目。题库会由评测模块提供。' : '先读一个故事，再来找我吧。');
  if (has) actions(button('试一试', () => { state.quiz = 0; state.score = 0; state.answers = []; quiz(); }, 'primary'), button('下次再来', close)); else actions(button(state.book ? '回到故事' : '去找兔子', state.book ? close : rabbit));
}
function quiz() { const q = state.quizQuestions[state.quiz]; show('owl', `第 ${state.quiz + 1} 题 / ${challenge.questions.length}`, q.question); const opts = el('div', '', 'question-options'); q.options.forEach((option, i) => opts.append(button(option, () => { const correct = i === q.answer; state.score += Number(correct); state.answers.push({ q: q.question, explanation: q.explanation, correct }); opts.querySelectorAll('button').forEach(b => b.disabled = true); content.append(el('p', (correct ? '答对了。' : '再读一遍就能发现。') + q.explanation)); actions(button(state.quiz === 2 ? '看看结果' : '下一题', () => { state.quiz++; if (state.quiz < state.quizQuestions.length) quiz(); else { show('owl', '又发现了一些故事里的事', `这次答对了 ${state.score} 道，共 ${challenge.questions.length} 道。`); state.answers.forEach(a => content.append(el('p', `${a.correct ? '✓' : '○'} ${a.q} ${a.explanation}`))); actions(button('回到故事', () => { feedback('owl', '带着新发现，再翻一页吧。'); openBook(state.book, state.page); }, 'primary')); } }, 'primary')); }))); content.append(opts); }

// ---------------------------------------------------------------- 提问
function ask() { openReadingTool('question'); }

// ---------------------------------------------------------------- 阅读页
const img = () => $('#illustration img'), video = () => $('#page-video');
let hintsEnabled = true;
function renderHints() {
  const svg = $('#object-hints'), p = state.book?.pages[state.page];
  svg.replaceChildren(); svg.setAttribute("hidden", ""); $('#object-box').hidden = true;
  if (!p || $('#reader').hidden) return;
  const objects = p.objects || [], onVideo = !video().hidden;
  $('#hint-toggle').hidden = !objects.length || onVideo;
  $('#explore-tip').textContent = onVideo ? '想探索画面？切回图片，点一点发光的地方。' : objects.length ? (hintsEnabled ? '点一点发光的地方，发现故事里的小秘密。' : '探索提示已隐藏，仍可以点画面找一找。') : '这一页暂时没有探索提示。';
  if (!hintsEnabled || onVideo || !objects.length || !img().complete || !img().naturalWidth) return;
  const w = p.size?.width || img().naturalWidth, h = p.size?.height || img().naturalHeight;
  const d = displayedRect(img(), w, h), frame = $('#illustration').getBoundingClientRect();
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  Object.assign(svg.style, {left:`${d.left-frame.left}px`, top:`${d.top-frame.top}px`, width:`${d.w}px`, height:`${d.h}px`});
  // Smaller objects are painted last, so nested targets (e.g. scarf) remain reachable.
  const sorted = [...objects].sort((a,b) => ((b.bbox[2]-b.bbox[0])*(b.bbox[3]-b.bbox[1]))-((a.bbox[2]-a.bbox[0])*(a.bbox[3]-a.bbox[1])));
  for (const object of sorted) {
    if (!Array.isArray(object.bbox) || object.bbox.length !== 4) continue;
    const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    group.classList.add('explore-object'); group.setAttribute('tabindex', '0'); group.setAttribute('role', 'button'); group.setAttribute('aria-label', `探索${object.name}`);
    const contours = (object.contours || []).filter(points => Array.isArray(points) && points.length >= 3);
    const paths = contours.length ? contours : [null];
    for (const points of paths) {
      const shape = document.createElementNS('http://www.w3.org/2000/svg', points ? 'polygon' : 'rect');
      if (points) shape.setAttribute('points', points.map(p => p.join(',')).join(' '));
      else { const [x1,y1,x2,y2] = object.bbox; for (const [k,v] of Object.entries({x:x1,y:y1,width:x2-x1,height:y2-y1,rx:12})) shape.setAttribute(k,v); shape.classList.add('region-hint'); }
      shape.setAttribute('vector-effect','non-scaling-stroke'); group.append(shape);
    }
    const select = () => {
      stopAll();readerTools?.stopCapture();state.selectedObject={object_id:object.object_id,name:object.name};
      svg.querySelectorAll('.selected').forEach(g => g.classList.remove('selected')); group.classList.add('selected');
      $('#object-box').hidden = true; $('#object-result').textContent = `${object.name} · ${object.explanation || '发现了一个故事里的朋友。'}`; $('#object-result').hidden = false;
    };
    group.addEventListener('click', select); group.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
    svg.append(group);
  }
  svg.removeAttribute("hidden");
}

function setControlLabel(selector,text){const b=$(selector),caption=b.querySelector('.control-caption');if(caption)caption.textContent=text;else b.textContent=text;}
function playTime(){const v=video(),p=state.book?.pages[state.page];if(!v||v.hidden)return null;return readerTools?.videoScope()==='book'?v.currentTime:p?.timeRange?p.timeRange[0]+v.currentTime:null;}
function stopAll(except=null){state.audioEpoch=(state.audioEpoch||0)+1;narration.pause();cloneVoice.pause();readerTools?.pauseMedia({except:except==='page'?video():null});const v=video();if(v&&except!=='page')v.pause();}
function saveReadingPosition(){if(state.book)readingProgress.save(state.book,state.page,state.mediaMode||'image',playTime()??0);}
function openBook(book,progress){stopAll();narration.stop();cloneVoice.stop();readerTools.leave();$('#reading-workbench').hidden=true;if(state.book?.id!==book.id){state.history=[];state.sessionId='session-'+crypto.randomUUID();}const saved=readingProgress.get(book.id);state.book=book;state.page=Math.max(0,Math.min(progress??saved?.page??0,book.pages.length-1));state.mediaMode=saved?.media==='video'?'video':'image';state.resumeTime=saved?.time;$('#home').hidden=true;$('#reader').hidden=false;renderPage();window.scrollTo(0,0);}
function goToPage(pageNo, bookTime = null) {
  const index = state.book.pages.findIndex(p => p.no === pageNo); if (index < 0) return;
  state.page = index; renderPage();
  const p = state.book.pages[index];
  if (bookTime !== null && p.video && p.timeRange) { showVideo(true); const v = video(); const seek = () => { v.currentTime = Math.max(0, bookTime - p.timeRange[0]); v.pause(); }; if (v.readyState >= 1) seek(); else v.addEventListener('loadedmetadata', seek, { once: true }); }
}
function showVideo(on,source=null){
  const p=state.book?.pages[state.page],v=video();if(!p)return;
  const url=source||readerTools?.videoSource()||p.video;
  if(on&&!url){toast('这本书还没有故事视频，可以先看绘本。');on=false;}
  state.mediaMode=on?'video':'image';v.hidden=!on;img().hidden=on;setControlLabel('#toggle-video',on?'看绘本':'看故事');
  if(on&&v.getAttribute('src')!==url){v.src=url;v.load();}if(!on)v.pause();
  $('#object-box').hidden=true;$('#object-result').hidden=true;renderHints();renderTextHighlights();saveReadingPosition();
}
let pageRenderEpoch=0;
function renderPage(options={}){
  const fromVideo=!!options.fromVideo,wasPlaying=!video().paused,mode=state.mediaMode||'image';
  if(!fromVideo){stopAll();narration.stop();cloneVoice.stop();readerTools?.stopCapture();}setControlLabel('#narrate',mode==='video'?'播放':'听一听');$('#audio-status').textContent='';
  const book=state.book,p=book.pages[state.page],backendBook=book.source==='backend';state.selectedObject=null;
  $('#reading-title').textContent=book.title;$('#page-count').textContent=`第 ${state.page+1} 页 / ${book.pages.length} 页`;$('#reader-position').textContent=`${state.page+1} / ${book.pages.length}`;
  $('#narration-details').hidden=!p.narrationText||p.narrationText===p.text;$('#narration-text').textContent=p.narrationText||'';$('#page-title').textContent=p.title;
  $('#page-text').textContent=p.importSkipped ? '此页上传时已跳过。' : p.text||'这页的文字就在画面里。自动文字识别尚未接入。';$('#prev').disabled=state.page===0;$('#next').disabled=state.page===book.pages.length-1;
  $('#reader-label').textContent=modeLabel();
  const notes=[];if(p.importSkipped)notes.push('此页上传时已跳过。');if(state.book.reviewPending)notes.push('生成旁白与视频等待人工复核');if(p.audioPlaceholder)notes.push('此示例音轨为占位提示音');if(p.videoFallback)notes.push('此示例视频采用本地推近效果');if(!p.objectCount)notes.push('这一页尚无物体标注');if(p.needsReview)notes.push('内容待核对');$('#page-note').textContent=notes.join('；');
  const pic=img();pic.classList.toggle('page-image',backendBook);pic.alt=`${book.title} 第 ${state.page+1} 页`;$('#identify').hidden=backendBook;
  const epoch=++pageRenderEpoch;
  if(p.image){pic.src=p.image;$('#page-loading').hidden=true;}
  else if(config.mode==='preview'&&book.localUpload){pic.removeAttribute('src');$('#page-loading').hidden=false;$('#page-loading').textContent='正在翻开这一页……';books.pageAsset(book.id,p.no).then(asset=>{p.image=asset.page_image_url;if(epoch!==pageRenderEpoch||state.book!==book)return;pic.src=p.image;$('#page-loading').hidden=true;renderHints();renderTextHighlights();}).catch(e=>{if(epoch===pageRenderEpoch){$('#page-loading').textContent=e.message;$('#page-loading').hidden=false;}});}
  else{pic.src='assets/asset-0.png';$('#page-loading').hidden=true;}
  $('#toggle-video').hidden=!(p.video||book.video?.url);state.highlight='';renderTextHighlights();readerTools?.update({preservePlayback:fromVideo});
  if(!fromVideo){showVideo(mode==='video');if(state.mediaMode==='video'){
    const time=state.resumeTime??(readerTools.videoScope()==='book'?p.timeRange?.[0]:0)??0;state.resumeTime=null;
    const seek=()=>{if(epoch!==pageRenderEpoch)return;video().currentTime=time;if(wasPlaying)video().play().catch(()=>{});};if(video().readyState>=1)seek();else video().addEventListener('loadedmetadata',seek,{once:true});
  }}
  renderHints();saveReadingPosition();
}

// 点击画面识物：把屏幕坐标换算成原图（或视频画面）像素坐标再请求后端。
function displayedRect(media, natW, natH) {
  const r = media.getBoundingClientRect(), scale = Math.min(r.width / natW, r.height / natH);
  const w = natW * scale, h = natH * scale;
  return { left: r.left + (r.width - w) / 2, top: r.top + (r.height - h) / 2, scale, w, h };
}
async function identifyAt(event) {
  const book = state.book; if (book?.source !== 'backend') return;
  const p = book.pages[state.page], v = video(), onVideo = !v.hidden;
  const media = onVideo ? v : img();
  const natW = onVideo ? v.videoWidth : (p.size?.width || img().naturalWidth), natH = onVideo ? v.videoHeight : (p.size?.height || img().naturalHeight);
  if (!natW || !natH) return;
  if(onVideo&&!v.paused)v.pause();
  const d = displayedRect(media, natW, natH), x = (event.clientX - d.left) / d.scale, y = (event.clientY - d.top) / d.scale;
  if (x < 0 || y < 0 || x > natW || y > natH) return;
  try {
    const r = await touch.identify({ bookId: book.id, pageNo: p.no, x: Math.round(x), y: Math.round(y), playTime: onVideo ? playTime() : null });
    if (state.book !== book || state.book.pages[state.page] !== p || onVideo !== !video().hidden) return;
    const box = $('#object-box'), result = $('#object-result');
    if (!r.hit) { box.hidden = true; result.hidden = true; toast(r.reason || '这里没有识别到东西。'); return; }
    const sx = d.w / r.frame_size.width, sy = d.h / r.frame_size.height, frame = $('#illustration').getBoundingClientRect(), [x1, y1, x2, y2] = r.object.bbox;
    Object.assign(box.style, { left: `${d.left - frame.left + x1 * sx}px`, top: `${d.top - frame.top + y1 * sy}px`, width: `${(x2 - x1) * sx}px`, height: `${(y2 - y1) * sy}px` });
    stopAll();state.selectedObject={object_id:r.object.object_id,name:r.object.name};
    box.hidden = false; result.textContent = `${r.object.name} · ${r.object.explanation}`; result.hidden = false;
  } catch (e) { toast(errorText(e)); }
}

async function narrate() {
  if(state.mediaMode==='video'){if(video().paused){await readerTools.playVideo();setControlLabel('#narrate','暂停');}else{video().pause();setControlLabel('#narrate','继续');}return;}
  if (narration.playing || cloneVoice.playing) { stopAll(); setControlLabel('#narrate','继续'); $('#audio-status').textContent = '已暂停朗读，可以从这里继续。'; return; }
  const p = state.book.pages[state.page];
  if(p.audioPlaceholder&&!state.cloned){toast('这本书的讲述声音还没准备好，先看绘本或故事视频吧。');return;}
  if (!p.text) { toast('这一页还没有文字可以朗读。'); return; }
  let requestEpoch=null;const activeBook=state.book,activePage=p;
  const done = () => { if(state.book!==activeBook||state.book.pages[state.page]!==activePage)return; $('#audio-status').textContent = '朗读已结束'; setControlLabel('#narrate','听一听'); };
  try {
    stopAll(); requestEpoch=state.audioEpoch;readerTools.stopCapture();
    if (state.cloned) {
      $('#audio-status').textContent = '正在用所选音色合成……';
      await cloneVoice.speak(state.cloned.id, p.text, done); $('#audio-status').textContent = '克隆音色：' + state.cloned.name;
    } else if (p.audio) {
      await narration.play(p.audio, done); $('#audio-status').textContent = p.audioPlaceholder ? '占位提示音（TTS 未配置，不是真实配音）' : '后端生成的旁白配音';
    } else { toast('这一页还没有配音。先让兔子生成，或在小熊那里选一个克隆音色。'); return; }
    setControlLabel('#narrate','暂停');
  } catch (e) { if(requestEpoch!==state.audioEpoch||state.book!==activeBook)return;$('#audio-status').textContent = e.message; toast(e.message); }
}

$('#close').onclick = close; dialog.addEventListener('click', e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close(); } }); dialog.addEventListener('close', () => { state.cleanup?.(); state.cleanup = null; content.dataset.view = ''; state.lastAnimal?.focus(); });
for (const b of document.querySelectorAll('[data-open]')) b.onclick = () => { state.lastAnimal = b; ({ rabbit:continueReading, fox, bear, owl }[b.dataset.open])(); };
$('#motion').onclick = () => { const reduced = document.body.classList.toggle('reduce'); $('#motion').setAttribute('aria-pressed', String(reduced)); $('#motion').textContent = reduced ? '恢复动态' : '减少动态'; };
$('#back-home').onclick = () => { saveReadingPosition();stopAll();$('#reading-workbench').hidden=true; readerTools.leave(); $('#home').hidden = false; $('#reader').hidden = true; };
$('#prev').onclick = () => { state.page--; renderPage(); }; $('#next').onclick = () => { state.page++; renderPage(); };
$('#narrate').onclick = narrate; $('#reader-ask').onclick = ask;
$('#reader-voice').onclick = () => openReadingTool('voice');
$('#reader-search').onclick = () => openReadingTool('search');
$('#reader-video').onclick = () => openReadingTool('video');
$('#reader-history').onclick = () => openReadingTool('history');
$('#prepare-library').onclick=()=>rabbit({manage:true});
$('#reader-settings').onclick=()=>openReadingTool('material');
for (const entry of document.querySelectorAll('[data-route]')) entry.onclick = () => { const route=entry.dataset.route; if(route==='library') rabbit(); else if(route==='sound') bear(); else openReadingTool(route); };
$('#toggle-video').onclick = () => showVideo(video().hidden);
img().addEventListener('load', renderHints);
new ResizeObserver(() => { renderHints(); renderTextHighlights(); }).observe($('#illustration'));
img().addEventListener('load', renderTextHighlights);
$('#hint-toggle').onclick = () => { hintsEnabled = !hintsEnabled; $('#hint-toggle').setAttribute('aria-pressed', String(hintsEnabled)); setControlLabel('#hint-toggle',hintsEnabled?'找朋友':'显示提示'); renderHints(); };
img().addEventListener('click', identifyAt); video().addEventListener('click', identifyAt);
video().addEventListener('play',()=>{stopAll('page');setControlLabel('#narrate','暂停');});
video().addEventListener('pause',()=>{if(state.mediaMode==='video')setControlLabel('#narrate','继续');});
let lastProgressWrite=0;video().addEventListener('timeupdate',()=>{if(!state.book||video().hidden)return;if(readerTools.videoScope()==='book'){const index=state.book.pages.findIndex(p=>p.timeRange&&video().currentTime>=p.timeRange[0]&&video().currentTime<p.timeRange[1]);if(index>=0&&index!==state.page){state.page=index;renderPage({fromVideo:true});}}if(Date.now()-lastProgressWrite>1500){saveReadingPosition();lastProgressWrite=Date.now();}});
$('#identify').onclick = () => { $('#object-result').textContent = '壁炉 · 冬夜里的温暖火光（本地演示的固定标注）'; $('#object-result').hidden = false; };
$('#save-book').onclick = () => { try { bookshelf.save(state.book, state.page); toast('已放入本机书架。'); } catch { toast('保存失败，浏览器存储空间不足或权限受限。'); } };
window.addEventListener('keydown', e => { if (dialog.open || $('#reader').hidden || /INPUT|TEXTAREA|SELECT|BUTTON|AUDIO|VIDEO/.test(e.target.tagName)) return; if (e.key === 'ArrowRight' && !$('#next').disabled) $('#next').click(); if (e.key === 'ArrowLeft' && !$('#prev').disabled) $('#prev').click(); });

health().then(() => { state.backend = true; }).catch(() => { state.backend = false; }).finally(() => { $('#mode-label').textContent = modeLabel(); });
cloneVoice.list().then(data => { const selected = data.voices.find(v => v.id === data.selected_voice_id && v.status === 'ready'); if (selected) state.cloned = { id: selected.id, name: selected.name }; }).catch(() => {});


function highlightOriginal(query, pageNo) {
  if (!state.book) return;
  if (state.book.pages[state.page].no !== pageNo) goToPage(pageNo);
  state.highlight = query; renderTextHighlights();
}
function renderTextHighlights() {
  const layer = $('#text-highlights'), p = state.book?.pages[state.page];
  layer.replaceChildren(); layer.setAttribute('hidden', ''); if (!p) return;
  const text = p.importSkipped ? '此页上传时已跳过。' : p.text || '', query = state.highlight || '', target = $('#page-text');
  if (!text) return;
  target.replaceChildren();
  if (!query) target.textContent = text;
  else { let from = 0, index; const lower = text.toLocaleLowerCase(), word = query.toLocaleLowerCase(); while ((index = lower.indexOf(word, from)) >= 0) { target.append(document.createTextNode(text.slice(from,index))); target.append(el('mark',text.slice(index,index+query.length))); from = index + query.length; } target.append(document.createTextNode(text.slice(from))); }
  if (!query || !video().hidden || !img().naturalWidth) return;
  const w = p.size?.width || img().naturalWidth, h = p.size?.height || img().naturalHeight, d = displayedRect(img(),w,h), frame = $('#illustration').getBoundingClientRect();
  const blocks = (p.textBlocks || []).filter(block => block.text?.toLocaleLowerCase().includes(query.toLocaleLowerCase()) && Array.isArray(block.bbox) && block.bbox.length===4);
  if (!blocks.length) return;
  layer.setAttribute('viewBox',`0 0 ${w} ${h}`); Object.assign(layer.style,{left:`${d.left-frame.left}px`,top:`${d.top-frame.top}px`,width:`${d.w}px`,height:`${d.h}px`});
  for (const block of blocks) { const rect = document.createElementNS('http://www.w3.org/2000/svg','rect'); const [x1,y1,x2,y2]=block.bbox; for(const [k,v] of Object.entries({x:x1,y:y1,width:x2-x1,height:y2-y1})) rect.setAttribute(k,v); layer.append(rect); }
  layer.removeAttribute('hidden');
}
if (config.mode === 'preview') { $('#frontend-status').hidden = false; $('#frontend-status').textContent = '前端试用：原小屋完整流程 · 生成、语音识别和智能问答尚未接 API；固定示例均有标注。'; }

window.addEventListener('pagehide',()=>{saveReadingPosition();narration.pause();cloneVoice.pause();});

async function continueReading(){const id=readingProgress.last();if(id){try{openBook(await books.load(id));return;}catch{}}rabbit();}
