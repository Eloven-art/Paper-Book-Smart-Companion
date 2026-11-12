import { cloneVoice } from './api/voice.js';
import { openVoicePanel } from './ui/voice-panel.js';
import { config, books, story, localDemo, bookshelf, narration, conversation, touch, challenge, health } from './api/service.js';
const $ = s => document.querySelector(s), dialog = $('#dialog'), content = $('#content');
const state = { book: null, page: 0, task: null, status: 'idle', history: [], lastAnimal: null, cleanup: null, cloned: null, timer: null, quiz: 0, score: 0, answers: [], target: null, sessionId: 'session-' + Math.random().toString(36).slice(2, 10), backend: null };
const modeLabel = () => config.mode === 'mock' ? '假接口联调' : state.backend === false ? '后端未连接' : '已连接后端';
function el(tag, text, cls) { const e = document.createElement(tag); if (text) e.textContent = text; if (cls) e.className = cls; return e; }
function button(text, action, cls) { const b = el('button', text, cls); b.type = 'button'; b.onclick = action; return b; }
function toast(text) { clearTimeout(state.timer); $('#toast').textContent = text; $('#toast').classList.add('on'); state.timer = setTimeout(() => $('#toast').classList.remove('on'), 3500); }
function close() { dialog.close(); }
function show(kind, title, intro) { state.cleanup?.(); state.cleanup = null; content.dataset.view = ''; content.replaceChildren(); $('#paper').className = 'paper ' + ({ fox: 'scroll', bear: 'voice', owl: 'letter' }[kind] || ''); content.append(el('small', '纸书智伴 · ' + modeLabel()), el('h2', title)); if (intro) content.append(el('p', intro)); if (!dialog.open) dialog.showModal(); }
function actions(...items) { const d = el('div', '', 'actions'); d.append(...items); content.append(d); }
function feedback(id, text) { close(); const a = document.querySelector(`[data-open="${id}"]`); a?.classList.add('selected'); setTimeout(() => a?.classList.remove('selected'), 900); toast(text); }
const errorText = e => e.runId ? `${e.message}（run_id：${e.runId}）` : e.message;

// ---------------------------------------------------------------- 兔子：选书、上传、生成
async function rabbit() {
  if (['waiting', 'running', 'failed', 'success', 'cancelled'].includes(state.status)) return taskView();
  show('rabbit', '今天读哪一本？', '先选一本绘本，再让故事慢慢展开。');
  const list = el('div'); list.append(el('p', '正在读取书架……', 'muted')); content.append(list);
  try {
    const items = await books.list(); state.backend = true; list.replaceChildren();
    if (!items.length) list.append(el('p', '后端还没有绘本，可以在下面上传页面图片。', 'muted'));
    for (const b of items) {
      const c = button('', () => prepare(b), 'choice');
      const tags = [`${b.page_count} 页`, b.video.ready ? '已有视频' : '尚未生成视频'];
      c.append(el('span', '书', 'book-icon'), el('strong', b.title), el('small', tags.join(' · '))); list.append(c);
    }
  } catch (e) {
    state.backend = false; list.replaceChildren(el('p', errorText(e), 'error'));
    list.append(button('', () => prepareLocal(), 'choice'));
    list.lastChild.append(el('span', '书', 'book-icon'), el('strong', '本地演示：雪夜里的一盏灯'), el('small', '不连接后端，只走一遍界面流程'));
    return;
  }
  if (config.mode === 'mock') { content.append(el('p', '当前是假接口联调模式（地址带 ?mock=1），数据是固定示例，不是生成结果。', 'muted')); }
  const form = el('div', '', 'upload');
  const title = document.createElement('input'); title.placeholder = '书名（可不填）'; title.maxLength = 60;
  const label = el('label', '上传绘本页面图片（按选择顺序编页码）', 'field'), input = document.createElement('input');
  input.type = 'file'; input.accept = 'image/png,image/jpeg,image/webp'; input.multiple = true; label.append(input);
  const info = el('p', '', 'error'); info.setAttribute('role', 'alert');
  const send = button('上传', async () => {
    const fs = [...input.files];
    if (!fs.length) { info.textContent = '请先选择图片。'; return; }
    if (fs.some(f => f.size > 20 * 1024 * 1024)) { info.textContent = '单张图片请不超过 20 MB。'; return; }
    send.disabled = true; info.textContent = `正在上传 ${fs.length} 张……`;
    try { const r = await books.upload(fs, title.value.trim()); toast(`已上传《${r.title}》，共 ${r.page_count} 页。`); rabbit(); }
    catch (e) { info.textContent = errorText(e); send.disabled = false; }
  });
  form.append(label, title, send, info);
  content.append(el('p', '只能上传组委会授权的绘本或队伍自制的模拟绘本。上传后需要内容理解模块识别文字，才能生成旁白。', 'muted'), form);
}

function prepare(summary) {
  show('rabbit', summary.title, `${summary.page_count} 页。生成会依次做：旁白 → 配音 → 视频片段 → 拼接整本。`);
  content.append(el('p', `朗读声音：${state.cloned ? state.cloned.name + '（克隆音色，阅读时使用）' : '使用后端生成的旁白配音'}`));
  actions(button('换个声音', () => bear(() => prepare(summary))));
  const force = el('label', '', 'muted'), forceBox = document.createElement('input'); forceBox.type = 'checkbox';
  force.append(forceBox, document.createTextNode(' 忽略缓存重新生成（会重新调用收费服务）'));
  if (config.mode === 'real') content.append(force);
  let failBox = null;
  if (config.mode === 'mock') { const f = el('label', '', 'muted'); failBox = document.createElement('input'); failBox.type = 'checkbox'; f.append(failBox, document.createTextNode(' 联调：模拟一次失败')); content.append(f); }
  actions(
    button('开始生成', () => startTask(summary.book_id, { force: forceBox.checked, fail: !!failBox?.checked }), 'primary'),
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
    state.book = local ? await localDemo.generate({ signal: state.task.signal, onStage }) : await story.generate({ bookId, force, fail, signal: state.task.signal, onStage });
    state.status = 'success'; if (dialog.open && content.dataset.view === 'task') taskView(); else toast('故事准备好了，点兔子翻开绘本。');
  } catch (e) { state.status = e.name === 'AbortError' ? 'cancelled' : 'failed'; state.error = errorText(e); if (dialog.open && content.dataset.view === 'task') taskView(); }
}

function taskView() {
  show('rabbit', state.status === 'success' ? '故事准备好了' : state.status === 'failed' ? '这次没有完成' : state.status === 'cancelled' ? '已停止' : '让故事慢慢展开'); content.dataset.view = 'task';
  if (['waiting', 'running'].includes(state.status)) {
    const steps = el('div', '', 'steps'); for (let i = 0; i < 4; i++) steps.append(el('span', '', i <= state.stage ? 'done' : ''));
    content.append(steps, el('p', state.stageText, 'status-line'));
    actions(button('停止', () => state.task.abort()), button('回小屋等一会儿', close));
  } else if (state.status === 'success') {
    const b = state.book;
    if (b?.source === 'backend') {
      const notes = [];
      if (b.pages.some(p => p.videoFallback)) notes.push('视频是本地降级片段（静态页图缓慢推近），不是 AI 生成的视频');
      if (b.pages.some(p => p.audioPlaceholder)) notes.push('配音是占位提示音，不是真实语音');
      if (b.pages.some(p => p.narrationMethod && p.narrationMethod !== 'llm')) notes.push('旁白直接使用了原文');
      if (notes.length) content.append(el('p', '注意：' + notes.join('；') + '。原因是外部服务未配置或调用失败，详见后端日志。', 'muted'));
    }
    actions(button('翻开绘本', () => { feedback('rabbit', '故事准备好了。'); openBook(state.book); }, 'primary'), button('重新选书', () => { state.status = 'idle'; rabbit(); }));
  } else {
    content.append(el('p', state.status === 'failed' ? state.error : '已停止。正在进行的那一页处理完后后端才会停下。', 'status-line'));
    actions(button('重试', () => state.retry(), 'primary'), button('返回选书', () => { state.status = 'idle'; rabbit(); }));
  }
}

// ---------------------------------------------------------------- 狐狸：本机书架
function fox() {
  show('fox', '我的书架', '喜欢的故事，留着下次再读。'); const list = bookshelf.list();
  if (!list.length) content.append(el('p', '书架还空着。和兔子一起读一本，再把它放在这里。'));
  for (const item of list) {
    const row = el('div', '', 'choice'); row.append(el('strong', item.title),
      button('继续阅读', async () => {
        try { const book = item.source === 'backend' ? await books.load(item.id) : item; close(); openBook(book, item.progress); }
        catch (e) { toast(e.code === 'BOOK_NOT_FOUND' ? '后端已经没有这本书了，可以把它从书架移除。' : errorText(e)); }
      }),
      button('移除', () => { if (confirm('从本机书架移除这本绘本？')) { try { bookshelf.remove(item.id); fox(); } catch { toast('保存失败，请检查浏览器存储权限。'); } } }));
    content.append(row);
  }
  content.append(el('p', '书架只在这台设备的浏览器里记下书名和读到第几页；书的内容每次从后端读取。', 'muted'));
}

// ---------------------------------------------------------------- 熊熊：声音
function bear(returnTo = null) {
  show('bear', '留住一个熟悉的声音'); content.querySelector('small').textContent = '纸书智伴 · 音色服务';
  openVoicePanel({ content, toast, close, onSelected: chosen => { state.cloned = chosen; if (returnTo) setTimeout(returnTo, 0); }, onClosed: cleanup => { state.cleanup = cleanup; } });
}

// ---------------------------------------------------------------- 猫头鹰：小测验（目前只有本地演示书有题）
function owl() {
  const has = state.book?.id === challenge.bookId;
  show('owl', '故事小挑战', has ? '还记得故事里的事吗？来试试这几道小题吧。' : state.book ? '这本书还没有题目。题库会由评测模块提供。' : '先读一个故事，再来找我吧。');
  if (has) actions(button('试一试', () => { state.quiz = 0; state.score = 0; state.answers = []; quiz(); }, 'primary'), button('下次再来', close)); else actions(button(state.book ? '回到故事' : '去找兔子', state.book ? close : rabbit));
}
function quiz() { const q = challenge.questions[state.quiz]; show('owl', `第 ${state.quiz + 1} 题 / ${challenge.questions.length}`, q.question); const opts = el('div', '', 'question-options'); q.options.forEach((option, i) => opts.append(button(option, () => { const correct = i === q.answer; state.score += Number(correct); state.answers.push({ q: q.question, explanation: q.explanation, correct }); opts.querySelectorAll('button').forEach(b => b.disabled = true); content.append(el('p', (correct ? '答对了。' : '再读一遍就能发现。') + q.explanation)); actions(button(state.quiz === 2 ? '看看结果' : '下一题', () => { state.quiz++; if (state.quiz < challenge.questions.length) quiz(); else { show('owl', '又发现了一些故事里的事', `这次答对了 ${state.score} 道，共 ${challenge.questions.length} 道。`); state.answers.forEach(a => content.append(el('p', `${a.correct ? '✓' : '○'} ${a.q} ${a.explanation}`))); actions(button('回到故事', () => { feedback('owl', '带着新发现，再翻一页吧。'); openBook(state.book, state.page); }, 'primary')); } }, 'primary')); }))); content.append(opts); }

// ---------------------------------------------------------------- 提问
function ask() {
  const backendBook = state.book?.source === 'backend';
  show('bear', '故事里有什么想问的？', backendBook ? `会结合第 ${state.page + 1} 页${playTime() !== null ? '和视频当前位置' : ''}来回答。` : '本地演示书不连接后端，不能提问。');
  const history = el('div', '', 'history'); state.history.forEach(item => history.append(item)); content.append(history);
  if (!backendBook) { actions(button('知道了', close)); return; }
  const label = el('label', '我的问题', 'field'), input = document.createElement('textarea'); input.rows = 3; input.maxLength = 500; label.append(input);
  const error = el('p', '', 'error'); error.setAttribute('role', 'alert'); content.append(label, error);
  const send = button('交给熊熊', async () => {
    const text = input.value.trim(); if (!text) { error.textContent = '先写下你想问的问题吧。'; input.focus(); return; }
    send.disabled = true;
    try {
      const turn = await conversation.ask({ bookId: state.book.id, pageNo: state.book.pages[state.page].no, question: text, playTime: playTime(), sessionId: state.sessionId });
      const q = el('p', '我：' + text), a = el('p', '熊熊：' + turn.answer_text);
      const jumps = el('div', '', 'actions');
      for (const ev of turn.evidence || []) {
        if (ev.type === 'page') jumps.append(button(`跳到第 ${ev.page_no} 页`, () => { close(); goToPage(ev.page_no); }));
        if (ev.type === 'video') jumps.append(button(`看视频 ${ev.timestamp.toFixed(1)} 秒处`, () => { close(); goToPage(ev.page_no, ev.timestamp); }));
      }
      state.history.push(q, a); if (jumps.childNodes.length) state.history.push(jumps); ask();
    } catch (e) {
      error.textContent = e.code === 'NOT_IMPLEMENTED' ? '问答功能的后端还没有接入（由内容理解与检索模块提供）。界面联调可以在地址后加 ?mock=1 使用假接口。' : errorText(e);
      send.disabled = false;
    }
  }, 'primary');
  actions(send, button('语音提问', () => toast('录音与语音识别尚未接入。')));
}

// ---------------------------------------------------------------- 阅读页
const img = () => $('#illustration img'), video = () => $('#page-video');
function playTime() {
  const v = video(), p = state.book?.pages[state.page];
  if (!v || v.hidden || !p?.timeRange) return null;
  return Math.round((p.timeRange[0] + v.currentTime) * 100) / 100;
}
function stopAll() { narration.stop(); cloneVoice.stop(); const v = video(); if (v) v.pause(); }
function openBook(book, progress = 0) { stopAll(); if (state.book?.id !== book.id) { state.history = []; state.sessionId = 'session-' + crypto.randomUUID(); } state.book = book; state.page = Math.max(0, Math.min(progress || 0, book.pages.length - 1)); $('#home').hidden = true; $('#reader').hidden = false; renderPage(); window.scrollTo(0, 0); }
function goToPage(pageNo, bookTime = null) {
  const index = state.book.pages.findIndex(p => p.no === pageNo); if (index < 0) return;
  state.page = index; renderPage();
  const p = state.book.pages[index];
  if (bookTime !== null && p.video && p.timeRange) { showVideo(true); const v = video(); const seek = () => { v.currentTime = Math.max(0, bookTime - p.timeRange[0]); v.pause(); }; if (v.readyState >= 1) seek(); else v.addEventListener('loadedmetadata', seek, { once: true }); }
}
function showVideo(on) {
  const p = state.book.pages[state.page], v = video();
  v.hidden = !on; img().hidden = on; $('#toggle-video').textContent = on ? '看图' : '看视频';
  if (on && v.getAttribute('src') !== p.video) v.src = p.video;
  if (!on) v.pause();
  $('#object-box').hidden = true; $('#object-result').hidden = true;
}
function renderPage() {
  stopAll(); $('#narrate').textContent = '朗读'; $('#audio-status').textContent = '';
  const book = state.book, p = book.pages[state.page], backendBook = book.source === 'backend';
  $('#reading-title').textContent = book.title; $('#page-count').textContent = `第 ${state.page + 1} 页 / ${book.pages.length} 页`;
  $('#page-title').textContent = p.title; $('#page-text').textContent = p.text || '（这一页还没有文字。内容理解模块识别出原文后，才能生成旁白。）';
  $('#prev').disabled = state.page === 0; $('#next').disabled = state.page === book.pages.length - 1;
  $('#reader-label').textContent = backendBook ? (config.mode === 'mock' ? '假接口示例数据' : '后端数据') : '本地演示 · 非生成内容';
  const notes = [];
  if (backendBook) {
    if (p.narrationMethod && p.narrationMethod !== 'llm' && p.narrationMethod !== 'provided') notes.push('旁白直接使用原文（大模型未配置或未通过检查）');
    if (p.needsReview || p.safety === 'pending_review') notes.push('内容待人工复核');
    if (p.videoFallback) notes.push('视频为本地降级片段');
    if (p.audioPlaceholder) notes.push('配音为占位提示音');
    if (!p.objectCount) notes.push('这一页还没有物体标注，点击识物暂时没有结果');
  } else notes.push('这是原创演示文字，画面复用小屋素材；未调用任何服务。');
  $('#page-note').textContent = notes.join('；');
  // 画面
  const pic = img(); pic.classList.toggle('page-image', backendBook);
  pic.src = backendBook && p.image ? p.image : 'assets/asset-0.png';
  pic.alt = backendBook ? `${book.title} 第 ${state.page + 1} 页` : '冬夜的小屋与壁炉';
  $('#identify').hidden = backendBook; $('#toggle-video').hidden = !(backendBook && p.video);
  video().removeAttribute('src'); showVideo(false);
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
  if (onVideo && !v.paused) { toast('先暂停视频，再点画面里的东西。'); return; }
  const d = displayedRect(media, natW, natH), x = (event.clientX - d.left) / d.scale, y = (event.clientY - d.top) / d.scale;
  if (x < 0 || y < 0 || x > natW || y > natH) return;
  try {
    const r = await touch.identify({ bookId: book.id, pageNo: p.no, x: Math.round(x), y: Math.round(y), playTime: onVideo ? playTime() : null });
    if (state.book !== book || state.book.pages[state.page] !== p || onVideo !== !video().hidden) return;
    const box = $('#object-box'), result = $('#object-result');
    if (!r.hit) { box.hidden = true; result.hidden = true; toast(r.reason || '这里没有识别到东西。'); return; }
    const sx = d.w / r.frame_size.width, sy = d.h / r.frame_size.height, frame = $('#illustration').getBoundingClientRect(), [x1, y1, x2, y2] = r.object.bbox;
    Object.assign(box.style, { left: `${d.left - frame.left + x1 * sx}px`, top: `${d.top - frame.top + y1 * sy}px`, width: `${(x2 - x1) * sx}px`, height: `${(y2 - y1) * sy}px` });
    box.hidden = false; result.textContent = `${r.object.name} · ${r.object.explanation}`; result.hidden = false;
  } catch (e) { toast(errorText(e)); }
}

async function narrate() {
  if (narration.playing || cloneVoice.playing) { stopAll(); $('#narrate').textContent = '朗读'; $('#audio-status').textContent = '已停止朗读'; return; }
  const p = state.book.pages[state.page];
  if (!p.text) { toast('这一页还没有文字可以朗读。'); return; }
  const done = () => { $('#audio-status').textContent = '朗读已结束'; $('#narrate').textContent = '朗读'; };
  try {
    if (state.cloned) {
      $('#audio-status').textContent = '正在用所选音色合成……';
      await cloneVoice.speak(state.cloned.id, p.text, done); $('#audio-status').textContent = '克隆音色：' + state.cloned.name;
    } else if (p.audio) {
      await narration.play(p.audio, done); $('#audio-status').textContent = p.audioPlaceholder ? '占位提示音（TTS 未配置，不是真实配音）' : '后端生成的旁白配音';
    } else { toast('这一页还没有配音。先让兔子生成，或在小熊那里选一个克隆音色。'); return; }
    $('#narrate').textContent = '停止朗读';
  } catch (e) { $('#audio-status').textContent = e.message; toast(e.message); }
}

$('#close').onclick = close; dialog.addEventListener('click', e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close(); } }); dialog.addEventListener('close', () => { state.cleanup?.(); state.cleanup = null; content.dataset.view = ''; state.lastAnimal?.focus(); });
for (const b of document.querySelectorAll('[data-open]')) b.onclick = () => { state.lastAnimal = b; ({ rabbit, fox, bear, owl }[b.dataset.open])(); };
$('#motion').onclick = () => { const reduced = document.body.classList.toggle('reduce'); $('#motion').setAttribute('aria-pressed', String(reduced)); $('#motion').textContent = reduced ? '恢复动态' : '减少动态'; };
$('#back-home').onclick = () => { stopAll(); $('#home').hidden = false; $('#reader').hidden = true; };
$('#prev').onclick = () => { state.page--; renderPage(); }; $('#next').onclick = () => { state.page++; renderPage(); };
$('#narrate').onclick = narrate; $('#reader-ask').onclick = ask;
$('#toggle-video').onclick = () => showVideo(video().hidden);
img().addEventListener('click', identifyAt); video().addEventListener('click', identifyAt);
$('#identify').onclick = () => { $('#object-result').textContent = '壁炉 · 冬夜里的温暖火光（本地演示的固定标注）'; $('#object-result').hidden = false; };
$('#save-book').onclick = () => { try { bookshelf.save(state.book, state.page); toast('已放入本机书架。'); } catch { toast('保存失败，浏览器存储空间不足或权限受限。'); } };
window.addEventListener('keydown', e => { if (dialog.open || $('#reader').hidden || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return; if (e.key === 'ArrowRight' && !$('#next').disabled) $('#next').click(); if (e.key === 'ArrowLeft' && !$('#prev').disabled) $('#prev').click(); });

health().then(() => { state.backend = true; }).catch(() => { state.backend = false; }).finally(() => { $('#mode-label').textContent = modeLabel(); });
cloneVoice.list().then(data => { const selected = data.voices.find(v => v.id === data.selected_voice_id && v.status === 'ready'); if (selected) state.cloned = { id: selected.id, name: selected.name }; }).catch(() => {});
