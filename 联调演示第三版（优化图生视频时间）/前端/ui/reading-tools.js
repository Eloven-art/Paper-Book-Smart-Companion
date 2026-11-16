import { config, conversation, interaction } from '../api/service.js';

const node = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
const action = (text, fn, cls = '') => { const e = node('button', text, cls); e.type = 'button'; e.onclick = fn; return e; };
export function createReadingTools({ root, getContext, navigate, highlight, pause, notice, saveText, mainVideo, showVideo, isVideoMode, onRefreshMedia, onReanalyze, onReanalyzeBook }) {
  root.innerHTML = `
    <header class="tools-heading"><div><small>故事里的每一个发现，都有迹可循</small><h2>故事小伙伴</h2></div><button class="close-tools" aria-label="收起，继续看绘本">收起</button><span class="tools-mode" hidden></span></header>
    <nav class="tools-tabs" role="tablist" aria-label="阅读功能">
      <button id="tool-video" role="tab" aria-controls="panel-video" aria-selected="true" data-tool="video">播放设置</button>
      <button id="tool-question" role="tab" aria-controls="panel-question" aria-selected="false" data-tool="question">问一问</button>
      <button id="tool-search" role="tab" aria-controls="panel-search" aria-selected="false" data-tool="search">找一找</button>
      <button id="tool-history" role="tab" aria-controls="panel-history" aria-selected="false" data-tool="history">聊过什么</button>
      <button id="tool-material" role="tab" aria-controls="panel-material" aria-selected="false" data-tool="material">大人设置</button>
    </nav>
    <section id="panel-video" role="tabpanel" aria-labelledby="tool-video" class="tools-panel">
      <div class="tool-title"><h3>让故事动起来</h3><label>播放范围 <select class="video-scope"><option value="page" selected>当前页故事</option><option value="book">整本故事</option></select></label></div>
      <div class="tool-actions"><button class="refresh-media" type="button">刷新素材</button></div>
      <video class="tools-video" controls playsinline preload="metadata" aria-label="故事视频播放器" hidden></video>
      <p class="video-empty empty-note" hidden>这一页/这本书还没有视频。先点上面的「刷新素材」；如果仍然没有，回兔子入口生成。</p>
      <div class="tool-actions"><button class="video-play">播放</button><button class="video-pause">暂停</button><button class="video-resume">继续</button><button class="video-stop">结束播放</button><label>速度 <select class="video-rate"><option value="0.75">0.75 倍</option><option value="1" selected>1 倍</option><option value="1.25">1.25 倍</option><option value="1.5">1.5 倍</option></select></label></div>
      <p class="video-status tool-status" role="status"></p><p class="video-origin muted"></p>
    </section>
    <section id="panel-question" role="tabpanel" aria-labelledby="tool-question" class="tools-panel" hidden>
      <div class="tool-title"><h3>想问什么？</h3><span class="question-context"></span></div>
      <p class="question-service muted"></p>
      <div class="question-layout"><div>
        <fieldset class="voice-question"><legend>说出来，或者选一段提问录音</legend>
          <div class="tool-actions"><button class="record-question">开始录音</button><span class="record-clock" role="status"></span></div>
          <label class="audio-upload">上传提问音频<input class="question-file" type="file" accept="audio/*"></label>
          <audio class="question-audio" controls hidden aria-label="我的提问录音"></audio>
          <div class="tool-actions"><button class="transcribe-question" disabled>转成文字</button><button class="remove-question-audio" hidden>移除录音</button></div>
          <p class="capture-status tool-status" role="status">尚未录音。这里是提问录音，与小熊的音色采样分开。</p>
        </fieldset>
        <form class="question-form"><label class="field">我的问题 / 核对转写文字<textarea class="question-text" rows="3" maxlength="1000" placeholder="例如：月亮真的掉到水里了吗？"></textarea></label>
          <div class="tool-actions"><button class="primary submit-question" type="submit">发送问题</button><button type="button" class="cancel-question" hidden>停止等待</button></div>
        </form><p class="question-error error" role="alert"></p>
      </div><div class="answer-panel" aria-live="polite"><small>故事的回答</small><p class="answer-text">提问后，回答和原文依据会出现在这里。</p><p class="answer-label muted"></p><div class="answer-evidence"></div><div class="tool-actions"><button class="speak-answer" disabled>朗读回答</button><button class="stop-answer" disabled>停止朗读</button></div><audio class="answer-audio" controls hidden aria-label="回答语音"></audio><p class="answer-status tool-status" role="status"></p></div></div>
    </section>
    <section id="panel-search" role="tabpanel" aria-labelledby="tool-search" class="tools-panel" hidden>
      <div class="tool-title"><h3>把刚才那一页找回来</h3></div>
      <form class="search-form"><label class="field">原文词句或情节关键词<input class="search-query" maxlength="200" placeholder="例如：月亮、影子、小河"></label><div class="tool-actions"><label>查找范围 <select class="search-kind"><option value="text">原文文字</option><option value="scene">情节与画面</option></select></label><button class="primary" type="submit">查找</button><button class="clear-highlight" type="button">清除高亮</button></div></form>
      <p class="search-note muted">原文按页面文字匹配；情节按已有的画面描述、事件与物体名称匹配。没有标注的画面不会被猜测出来。图片高亮使用已有的文字行框；手工录入的内容只高亮右侧文字。</p><p class="search-status tool-status" role="status"></p><div class="search-results"></div>
    </section>
    <section id="panel-history" role="tabpanel" aria-labelledby="tool-history" class="tools-panel" hidden>
      <div class="tool-title"><h3>我们刚才聊到哪里了？</h3><div class="tool-actions"><button class="refresh-history">读取本次会话</button><button class="clear-history">清空本机记录</button></div></div><p class="history-note muted">本机只保存这本书的文字记录。录音仅在本次页面中可试听，刷新后不会保留录音；没有上传到云端书架。</p><p class="history-status tool-status" role="status"></p><div class="conversation-list"></div>
    </section>
    <section id="panel-material" role="tabpanel" aria-labelledby="tool-material" class="tools-panel" hidden>
      <div class="tool-title"><h3>这一页的素材</h3></div>
      <div class="tool-actions"><button class="reanalyze-page" type="button">重新识别这一页（点读标注）</button><button class="reanalyze-book" type="button">补齐整本（只处理缺标注的页）</button></div><p class="reanalyze-status tool-status" role="status"></p>
      <details class="supporting-files"><summary>配套素材：PDF、文字稿与提问音频</summary><label class="audio-upload">添加本机素材<input class="supporting-upload" type="file" multiple accept="application/pdf,text/plain,.txt,audio/*"></label><p class="muted">文件只在本机查看和试听，不发送到云端。PDF 可查看原件；自动拆页、内容理解与音频识别等待 API。整本导入书籍的配套文件也保存在本机；示例书的附加文件仅本次会话可用。</p><p class="supporting-status tool-status" role="status"></p><div class="supporting-list"></div></details><div class="material-summary"></div><p class="material-note muted"></p>
      <form class="material-form"><label class="field">手工核对的页面原文<textarea class="material-text" rows="4" maxlength="5000"></textarea></label><label class="field">画面与情节描述<textarea class="material-scene" rows="2" maxlength="1500" placeholder="写下画面里发生了什么，供情节查找试用"></textarea></label><button class="primary save-material" type="submit">保存本次试读文字</button></form><p class="material-status tool-status" role="status"></p>
    </section>`;
  const $ = s => root.querySelector(s);
  const placeholderVideo = $('.tools-video');
  const video = mainVideo || placeholderVideo; if(mainVideo)placeholderVideo.remove();
  const answerAudio = $('.answer-audio');
  const supportingFiles = new Map();
  let videoOriginContext = null;
  let active = 'video', bookId = null, pageNo = null, turns = [], latestTurn = null, audioSource = null;
  let stream = null, recorder = null, timer = null, deadline = null, clockStart = 0, mediaEpoch = 0, audioURL = null;
  let questionRequest = null, asrRequest = null, ttsRequest = null, historyRequest = null, historyEpoch = 0;
  const context = () => { const c = getContext(); return { ...c, page: c.book?.pages[c.pageIndex] }; };
  const storageKey = () => 'pbsc-turns-' + bookId;
  const message = e => e.code === 'NOT_IMPLEMENTED' ? '这个功能的真实 API 尚未接入。录音、文字核对和界面操作仍可先检查。' : e.message;
  function persist() { try { localStorage.setItem(storageKey(), JSON.stringify(turns.slice(-50).map(({ localAudio, ...turn }) => ({ ...turn, question_audio_url: undefined })))); } catch { $('.history-status').textContent = '本机存储不可用，记录只保留在当前页面。'; } }
  function stopTracks() { stream?.getTracks().forEach(t => t.stop()); stream = null; clearInterval(timer); clearTimeout(deadline); }
  function stopCapture() { const wasRecording=recorder?.state==='recording'; const wasRequesting=$('.capture-status').textContent==='正在请求麦克风权限……'; mediaEpoch++; if (recorder?.state === 'recording') recorder.stop(); stopTracks(); $('.record-question').textContent = '开始录音'; if(wasRecording||wasRequesting){$('.record-clock').textContent='';$('.capture-status').textContent='已停止录音；未完成的这段录音没有发送。';} }
  function setSource(blob, filename) {
    asrRequest?.abort();
    if (audioURL) URL.revokeObjectURL(audioURL);
    audioSource = { blob, filename }; audioURL = URL.createObjectURL(blob); $('.question-audio').src = audioURL; $('.question-audio').hidden = false;
    $('.transcribe-question').disabled = false; $('.remove-question-audio').hidden = false;
    $('.capture-status').textContent = `已准备：${filename}。先试听，再转写核对。`;
  }
  function clearSource() { stopCapture(); asrRequest?.abort(); audioSource = null; if (audioURL) URL.revokeObjectURL(audioURL); audioURL = null; $('.question-audio').pause(); $('.question-audio').removeAttribute('src'); $('.question-audio').hidden = true; $('.question-file').value = ''; $('.transcribe-question').disabled = true; $('.remove-question-audio').hidden = true; $('.capture-status').textContent = '尚未录音。'; }
  async function capture() {
    if (recorder?.state === 'recording') { recorder.stop(); stopTracks(); return; }
    if (!navigator.mediaDevices?.getUserMedia || !globalThis.MediaRecorder) { $('.capture-status').textContent = '当前浏览器不能录音，请上传音频；麦克风需要 HTTPS 或 localhost。'; return; }
    pause(); const token = ++mediaEpoch; $('.capture-status').textContent = '正在请求麦克风权限……';
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (token !== mediaEpoch || active !== 'question' || document.querySelector('#reader').hidden) { acquired.getTracks().forEach(t => t.stop()); return; }
      stream = acquired; const type = ['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(t => MediaRecorder.isTypeSupported(t));
      const instance = new MediaRecorder(stream, type ? { mimeType: type } : {}); recorder = instance;
      const chunks = []; instance.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
      instance.onstop = () => { stopTracks(); $('.record-question').textContent = '开始录音'; if (instance !== recorder || !chunks.length || token !== mediaEpoch) return; const mime = instance.mimeType || chunks[0].type; setSource(new Blob(chunks, { type: mime }), mime.includes('mp4') ? '提问录音.m4a' : '提问录音.webm'); };
      instance.onerror = () => { stopTracks(); $('.capture-status').textContent = '录音中断，请重新录制或上传文件。'; $('.record-question').textContent = '开始录音'; };
      instance.start(); clockStart = Date.now(); $('.record-question').textContent = '停止录音'; $('.capture-status').textContent = '正在录音，最长 60 秒；停止后可试听。';
      timer = setInterval(() => { $('.record-clock').textContent = `已录 ${Math.floor((Date.now() - clockStart) / 1000)} 秒`; }, 300);
      deadline = setTimeout(() => { if (instance.state === 'recording') instance.stop(); }, 60000);
    } catch { stopTracks(); $('.capture-status').textContent = '无法使用麦克风。请检查权限，或者上传提问音频。'; }
  }
  function evidenceList(container, evidence) {
    container.replaceChildren();
    for (const ev of evidence || []) {
      const row = node('div', undefined, 'evidence-item');
      if (ev.quote) row.append(node('blockquote', ev.quote));
      if (ev.page_no) row.append(action(`看第 ${ev.page_no} 页`, () => { navigate(ev.page_no); highlight(ev.quote || '', ev.page_no); document.querySelector('#reader').scrollIntoView({ behavior: 'smooth' }); }));
      if (ev.type === 'video' && Number.isFinite(ev.timestamp)) row.append(action(`跳到视频 ${ev.timestamp.toFixed(1)} 秒`, () => { select('video'); $('.video-scope').value = 'book'; refreshVideo(); video.currentTime = ev.timestamp; video.pause(); root.scrollIntoView({ behavior: 'smooth' }); }));
      container.append(row);
    }
  }
  function renderHistory() {
    const list = $('.conversation-list'); list.replaceChildren();
    if (!turns.length) { list.append(node('p', '还没有聊过。可以先去“语音与文字提问”问一个问题。', 'empty-note')); return; }
    for (const turn of [...turns].reverse()) {
      const card = node('article', undefined, 'conversation-card'); card.append(node('small', `第 ${turn.page_no || '?'} 页 · ${turn.preview ? '固定示例预演' : '接口返回'} · ${new Date(turn.created_at).toLocaleString()}`), node('p', '我：' + turn.question_text), node('p', '回答：' + turn.answer_text));
      if (turn.localAudio) { const audio = node('audio'); audio.controls = true; audio.src = turn.localAudio; audio.setAttribute('aria-label', '这次提问的录音'); card.append(audio); }
      if (turn.answer_audio_url) { const audio = node('audio'); audio.controls = true; audio.src = turn.answer_audio_url; audio.setAttribute('aria-label', '这次回答的语音'); card.append(audio); }
      const evidence = node('div'); evidenceList(evidence, turn.evidence); card.append(evidence); list.append(card);
    }
  }
  async function submit(event) {
    event.preventDefault(); const c = context(), text = $('.question-text').value.trim();
    if (!text) { $('.question-error').textContent = audioSource ? '请先把录音转成文字，或者手工填入转写结果。' : '请先写下问题或录一段声音。'; $('.question-text').focus(); return; }
    if (recorder?.state === 'recording') { $('.question-error').textContent = '先停止录音并核对文字，再发送。'; return; }
    pause(); latestTurn=null; $('.speak-answer').disabled=true; $('.answer-evidence').replaceChildren(); $('.answer-label').textContent=''; questionRequest?.abort(); const pending = new AbortController(); questionRequest = pending;
    $('.submit-question').disabled = true; $('.cancel-question').hidden = false; $('.question-error').textContent = ''; $('.answer-text').textContent = '正在等故事的回答……';
    const requestStarted=performance.now();
    $('.answer-status').textContent='已提交，正在检索绘本并生成答案…';
    let firstStatusMs=null;
    requestAnimationFrame(()=>requestAnimationFrame(()=>{firstStatusMs=performance.now()-requestStarted;}));
    try {
      const turn = await conversation.ask({ bookId: c.book.id, pageNo: c.page.no, question: text, sessionId: c.sessionId, playTime: getPlayTime(), selectedObject:c.selectedObject, signal: pending.signal });
      if (bookId !== c.book.id || pending.signal.aborted) return;
      latestTurn = turn; $('.answer-text').textContent = turn.answer_text; $('.answer-label').textContent = turn.preview ? '固定示例回答，用于检查前端流程；没有调用模型。' : turn.grounded ? '回答附有绘本依据。' : '本次回答没有提供可核对的绘本依据。';
      evidenceList($('.answer-evidence'), turn.evidence); $('.speak-answer').disabled = !turn.answer_text; const responseMs=performance.now()-requestStarted;
      if(questionRequest!==pending || pending.signal.aborted)return;
      const audio=turn.answer_audio_url ? new Audio(turn.answer_audio_url) : null;
      $('.answer-status').textContent = `状态约 ${firstStatusMs===null?'未采样':Math.round(firstStatusMs)+' ms'} · 答案约 ${Math.round(responseMs)} ms`;
      if(audio) audio.addEventListener('canplay',()=>{if(latestTurn===turn)$('.answer-status').textContent+=` · 音频可播放约 ${Math.round(performance.now()-requestStarted)} ms`;},{once:true});
      const localAudio = audioSource ? URL.createObjectURL(audioSource.blob) : null;
      turns.push({ ...turn, localAudio }); if (turns.length > 50) { const removed = turns.shift(); if (removed.localAudio) URL.revokeObjectURL(removed.localAudio); } persist(); renderHistory();
    } catch (e) { if (bookId !== c.book.id) return; $('.answer-text').textContent = e.name === 'AbortError' ? '已停止等待。' : '这次没有收到回答。'; $('.question-error').textContent = e.name === 'AbortError' ? '' : message(e); }
    finally { if (questionRequest === pending) { questionRequest = null; $('.submit-question').disabled = false; $('.cancel-question').hidden = true; } }
  }
  function videoUrl() { const c = context(); return $('.video-scope').value === 'book' ? c.book?.video?.url : c.page?.video; }
  function refreshVideo() {
    const c=context();if($('.video-scope').value==='page'&&!c.page?.video&&c.book?.video?.url)$('.video-scope').value='book';const url=videoUrl();video.pause();
    videoOriginContext = { bookId: c.book?.id, pageNo: c.page?.no, scope: $('.video-scope').value, start: c.page?.timeRange?.[0] || 0 };
    if (url && video.getAttribute('src') !== url) { video.src = url; video.load(); }
    if (!url) { video.removeAttribute('src'); video.load(); }
    if(mainVideo){ video.hidden = !url || !isVideoMode(); if(!url&&isVideoMode())showVideo(false); }else video.hidden=!url; $('.video-empty').hidden = !!url;
    for (const s of ['.video-play','.video-pause','.video-resume','.video-stop','.video-rate']) $(s).disabled = !url;
    const currentPage = c.page;
    $('.video-origin').textContent = c.book?.video?.stale ? '页面文字已手工修改，已有视频尚未重新生成，可能仍包含旧内容。'
      : currentPage?.videoFallback ? '这一页的画面是本地“缓慢推近”片段：云端图生视频额度不足时会自动降级，重新生成可再试。'
      : currentPage?.audioPlaceholder ? '这一页的配音是占位提示音；旁白、配音、视频已接本地与 CosyVoice 服务。'
      : config.mode === 'preview' ? '前端试用只播放已有素材，不在浏览器中调用视频生成服务。'
      : '视频来自当前绘本的已生成素材。';
    $('.video-status').textContent = url ? '视频已准备好，点击播放。' : '尚无视频素材。';
  }
  function getPlayTime() {
    const c = context(), origin = videoOriginContext;
    if (origin?.bookId === c.book?.id && !video.hidden && video.currentTime > 0 && (origin.scope === 'book' || origin.pageNo === c.page?.no)) return (origin.scope === 'book' ? 0 : origin.start) + video.currentTime;
    return c.playTime?.() ?? null;
  }
  function renderSupporting() {
    const list = $('.supporting-list'); list.replaceChildren();
    for (const item of supportingFiles.get(bookId) || []) {
      const row = node('article', undefined, 'supporting-item'); row.append(node('p', item.name));
      if (item.type.startsWith('audio/')) { const audio = node('audio'); audio.controls = true; audio.src = item.url; audio.setAttribute('aria-label', item.name); row.append(audio); row.append(action('用作这次提问录音', () => { select('question'); setSource(item.file, item.name); })); }
      else { const link = node('a', item.type === 'application/pdf' ? '查看 PDF 原件' : '查看文字文件'); link.href=item.url; link.target='_blank'; link.rel='noopener'; row.append(link); }
      row.append(action('移除', async () => { try{if(item.id&&config.mode==='preview')await interaction.removeLocalFile(bookId,item.id);URL.revokeObjectURL(item.url);supportingFiles.set(bookId,(supportingFiles.get(bookId)||[]).filter(f=>f!==item));renderSupporting();}catch(e){$('.supporting-status').textContent=e.message;} })); list.append(row);
    }
  }
  function runSearch(event) {
    event?.preventDefault(); const c = context(), kind = $('.search-kind').value;
    const raw = $('.search-query').value.trim(), query = raw.replace(/^(找到|查找|高亮|寻找)\s*/, '').replace(/(的画面|几个字|两个字)$/, '').replace(/[“”"']/g, '').trim();
    const results = $('.search-results'); results.replaceChildren();
    if (!query) { $('.search-status').textContent = '先写一个词或一句原文。'; return; }
    const hits = c.book.pages.filter(p => (kind === 'text' ? (p.original || p.text) : [p.sceneSummary, ...(p.plotEvents || []), ...(p.objects || []).map(o => o.name)].join(' ')).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    $('.search-status').textContent = hits.length ? `找到 ${hits.length} 页。${kind === 'scene' ? '结果来自已有情节标注，不是语义模型检索。' : ''}` : '没有找到。请换一个原文词句；未录入文字或描述的页面无法检索。';
    for (const p of hits) {
      const row = node('article', undefined, 'search-card'); row.append(node('h4', `第 ${p.no} 页`), node('p', kind === 'text' ? p.original || p.text : p.sceneSummary || p.plotEvents?.join('；') || p.objects.map(o => o.name).join('、')));
      const actions = node('div', undefined, 'tool-actions'); actions.append(action(kind === 'text' ? '查看并高亮原文' : '查看这一页', () => { navigate(p.no); highlight(kind === 'text' ? query : '', p.no); document.querySelector('#reader').scrollIntoView({ behavior: 'smooth' }); }));
      const jump = action('跳到对应视频', () => { navigate(p.no); select('video'); $('.video-scope').value = 'book'; refreshVideo(); video.currentTime = p.timeRange[0]; video.pause(); root.scrollIntoView({ behavior: 'smooth' }); }); jump.disabled = !p.timeRange || !c.book.video?.url; actions.append(jump);
      if (!p.timeRange) row.append(node('small', '尚未关联视频时间')); row.append(actions); results.append(row);
    }
  }
  function select(tool) {
    if (!['video','question','search','history','material'].includes(tool)) tool = 'question';
    if (active !== tool) { stopCapture(); pause(); if (tool !== 'video') video.pause(); answerAudio.pause(); }
    active = tool;
    root.querySelectorAll('[data-tool]').forEach(b => { const chosen = b.dataset.tool === tool; b.setAttribute('aria-selected', String(chosen)); b.tabIndex = chosen ? 0 : -1; });
    root.querySelectorAll('[role="tabpanel"]').forEach(p => p.hidden = p.id !== 'panel-' + tool);
    if (tool === 'video') { refreshVideo(); if (!videoUrl()) onRefreshMedia?.(); }
    if (tool === 'history') renderHistory();
  }
  function update({preservePlayback=false}={}) {
    const c = context(); if (!c.book || !c.page) return;
    if (bookId !== c.book.id) {
      stopCapture(); clearSource(); questionRequest?.abort(); asrRequest?.abort(); ttsRequest?.abort(); historyRequest?.abort(); historyEpoch++;
      turns.forEach(t => { if (t.localAudio) URL.revokeObjectURL(t.localAudio); }); bookId = c.book.id;
      try { const stored = JSON.parse(localStorage.getItem(storageKey()) || '[]'); turns = Array.isArray(stored) ? stored.filter(t => t && typeof t.question_text === 'string' && typeof t.answer_text === 'string').slice(-50) : []; } catch { turns = []; }
      latestTurn = null; $('.answer-text').textContent = '提问后，回答和原文依据会出现在这里。'; $('.answer-evidence').replaceChildren(); $('.answer-label').textContent = ''; $('.question-text').value = ''; $('.answer-audio').hidden = true; $('.speak-answer').disabled = true;
    }
    if (pageNo !== c.page.no) stopCapture(); pageNo = c.page.no;
    $('.tools-mode').textContent = config.mode === 'preview' ? '前端试用 · 尚未接 API' : config.mode === 'mock' ? '假接口联调' : '连接本机后端';
    $('.question-context').textContent = `${c.book.title} · 第 ${c.page.no} 页${c.selectedObject?' · 刚点了'+c.selectedObject.name:''}`;
    $('.question-service').textContent = config.mode === 'preview' ? '可真实录音、上传、试听和手工核对文字。语音识别、智能问答和回答朗读等待 API；朵朵示例提供少量固定回答供预演。' : '录音先转写并核对文字，再发送问题。接口尚未接入时会显示原因，不会返回假识别结果。';
    $('.material-summary').textContent = `第 ${c.page.no} 页 · ${c.page.size?.width || '?'} × ${c.page.size?.height || '?'} · ${c.page.objectCount || 0} 个物体标注 · ${c.page.video ? '有单页视频' : '暂无单页视频'} · ${c.page.audio ? '有音轨' : '暂无音轨'}`;
    $('.material-text').value = c.page.original || ''; $('.material-scene').value = c.page.sceneSummary || '';
    $('.reanalyze-page').hidden = config.mode !== 'real'; $('.reanalyze-book').hidden = config.mode !== 'real'; $('.reanalyze-status').hidden = config.mode !== 'real';
    $('.material-form').hidden = config.mode !== 'preview'; $('.material-note').textContent = config.mode === 'preview' ? '可手工补充导入图片的原文和情节，保存后即可测试查找。没有自动 OCR，也不会修改图片上的字。整本导入素材保存在本机书架；刷新后仍可继续阅读。原图文字没有被改动。' : '原文、物体轮廓和视频时间来自后端分页数据。OCR 与画面理解接入后会更新到这里。';
    if(!supportingFiles.has(bookId))supportingFiles.set(bookId,c.book.supportingFiles||[]);
    if(!preservePlayback)refreshVideo(); renderHistory(); renderSupporting();
  }
  $('.supporting-upload').onchange=async event=>{
    const id=bookId,files=[...event.target.files],list=supportingFiles.get(id)||[],input=event.target;input.disabled=true;
    try{
      const valid=files.filter(f=>f.type.startsWith('audio/')||f.type==='application/pdf'||/\.(txt|pdf)$/i.test(f.name));
      for(const file of valid){const type=/\.pdf$/i.test(file.name)?'application/pdf':file.type.startsWith('audio/')?file.type:'text/plain';const safeFile=type.startsWith('audio/')?file:new Blob([file],{type});
        if(config.mode==='preview'){const result=await interaction.attachLocalFile(id,safeFile,file.name,type);list.push(result.item);}else list.push({file:safeFile,name:file.name,type,url:URL.createObjectURL(safeFile)});
      }
      supportingFiles.set(id,list);if(bookId===id){renderSupporting();$('.supporting-status').textContent=`已关联 ${valid.length} 份素材，后续在这本书里直接查看或试听。尚未自动识别音频。`;}
    }catch(e){if(bookId===id)$('.supporting-status').textContent=e.message;}finally{input.disabled=false;input.value='';}
  };
  $('.record-question').onclick = capture;
  $('.question-file').onchange = e => { const f = e.target.files[0]; if (!f) return; if (f.size > 20 * 1024 * 1024) { $('.capture-status').textContent = '提问音频请小于 20 MB。'; return; } stopCapture(); setSource(f, f.name); };
  $('.remove-question-audio').onclick = clearSource;
  $('.transcribe-question').onclick = async () => {
    if (!audioSource) return; const source = audioSource; asrRequest?.abort(); const controller = new AbortController(); asrRequest = controller; $('.transcribe-question').disabled = true; $('.capture-status').textContent = '正在等待转写……';
    try { const r = await interaction.transcribe(source.blob, source.filename, controller.signal); if (audioSource !== source || controller.signal.aborted) return; $('.question-text').value = r.text; $('.capture-status').textContent = config.mode === 'mock' ? '这是固定模拟转写，请核对，不能代表录音内容。' : '转写完成。请核对文字，再发送问题。'; }
    catch (e) { if (audioSource === source && !controller.signal.aborted) $('.capture-status').textContent = message(e); }
    finally { if (audioSource === source) $('.transcribe-question').disabled = false; }
  };
  $('.question-form').onsubmit = submit; $('.cancel-question').onclick = () => questionRequest?.abort();
  $('.speak-answer').onclick = async () => {
    if (!latestTurn) return; const turn = latestTurn; ttsRequest?.abort(); const controller = new AbortController(); ttsRequest = controller; $('.speak-answer').disabled = true; $('.answer-status').textContent = '正在准备回答语音……';
    try { let r = turn.answer_audio_url ? { audio_url: turn.answer_audio_url } : await interaction.speak(turn.answer_text, controller.signal); if (turn !== latestTurn || controller.signal.aborted) return; answerAudio.src = r.audio_url; answerAudio.hidden = false; await answerAudio.play(); $('.stop-answer').disabled = false; $('.answer-status').textContent = r.is_placeholder || turn.preview || config.mode === 'mock' ? '当前音轨是占位 / 示例音频，不是真实回答朗读。' : '正在朗读回答。'; }
    catch (e) { if (!controller.signal.aborted) $('.answer-status').textContent = message(e); }
    finally { if (turn === latestTurn) $('.speak-answer').disabled = false; }
  };
  $('.stop-answer').onclick = () => { ttsRequest?.abort(); answerAudio.pause(); $('.answer-status').textContent = '已停止朗读。'; };
  $('.search-form').onsubmit = runSearch; $('.clear-highlight').onclick = () => { highlight('', context().page?.no); $('.search-status').textContent = '已清除原文高亮。'; };
  $('.reanalyze-page').onclick = async () => {
    const pageNo = context().page?.no; if (!pageNo) return;
    const button = $('.reanalyze-page'); button.disabled = true;
    $('.reanalyze-status').textContent = `正在重新识别第 ${pageNo} 页的画面与物体，通常需要 10–60 秒（遇到限流会自动重试）……`;
    try {
      const ok = await onReanalyze?.(pageNo);
      $('.reanalyze-status').textContent = ok ? '识别完成，点读标注已更新；回到绘本点一点画面试试。' : '识别没有成功：可能仍在限流，请稍后再试。';
    } catch (e) { $('.reanalyze-status').textContent = e.message; }
    finally { button.disabled = false; }
  };
  $('.reanalyze-book').onclick = async () => {
    const button = $('.reanalyze-book'); button.disabled = true;
    $('.reanalyze-status').textContent = '正在补齐整本的点读标注：已有标注的页会跳过，缺标注的页逐页识别，可能需要几分钟……';
    try {
      const ok = await onReanalyzeBook?.();
      $('.reanalyze-status').textContent = ok ? '整本识别完成，缺标注的页已尽量补齐；回到绘本点一点画面试试。' : '整本识别没有全部成功：可能仍在限流，稍后可以再点一次。';
    } catch (e) { $('.reanalyze-status').textContent = e.message; }
    finally { button.disabled = false; }
  };
  $('.video-scope').onchange = () => { const showing=isVideoMode?.();refreshVideo();if(showing&&videoUrl())showVideo(true,videoUrl()); };
  $('.refresh-media').onclick = async () => {
    $('.video-status').textContent = '正在向后端读取最新素材……';
    const fresh = await onRefreshMedia?.();
    refreshVideo();
    $('.video-status').textContent = fresh ? (videoUrl() ? '素材已刷新，可以播放了。' : '已刷新：后端仍未生成这一页的视频。请回兔子入口生成。') : '刷新失败：没有连上后端，或这本书不是从后端打开的。';
  };
  const play = async () => { try { pause(); if(mainVideo)showVideo(true,videoUrl()); await video.play(); } catch { $('.video-status').textContent = '未能播放，请检查视频文件，或点击播放器自带的播放按钮。'; } };
  $('.video-play').onclick = play; $('.video-resume').onclick = play; $('.video-pause').onclick = () => { video.pause(); $('.video-status').textContent = '已暂停，可继续播放。'; };
  $('.video-stop').onclick = () => { video.pause(); video.currentTime = 0; $('.video-status').textContent = '已结束，回到开头。'; };
  $('.video-rate').onchange = () => video.playbackRate = Number($('.video-rate').value);
  video.addEventListener('timeupdate', () => { $('.video-status').textContent = `${video.paused ? '已暂停' : '播放中'} · ${video.currentTime.toFixed(1)} / ${Number.isFinite(video.duration) ? video.duration.toFixed(1) : '—'} 秒`; });
  video.addEventListener('error', () => { if (video.getAttribute('src')) $('.video-status').textContent = '视频加载失败。请检查素材文件；旧项目中的 LFS 指针不能播放。'; });
  $('.refresh-history').onclick = async () => {
    const c = context(), epoch = ++historyEpoch; historyRequest?.abort(); historyRequest = new AbortController(); $('.history-status').textContent = '正在读取……';
    try { const result = await interaction.history(c.sessionId, historyRequest.signal); if (epoch !== historyEpoch || bookId !== c.book.id) return; const merged = new Map(turns.map(t => [t.turn_id,t])); for (const t of result.turns || result.history || []) merged.set(t.turn_id,{...merged.get(t.turn_id),...t}); turns = [...merged.values()].slice(-50); persist(); renderHistory(); $('.history-status').textContent = '已读取本次会话；本机已有文字记录一并保留。'; }
    catch(e) { if (epoch === historyEpoch) $('.history-status').textContent = message(e); }
  };
  $('.clear-history').onclick = () => { if (!confirm('清空这本书在本机保存的对话记录？不会删除服务端记录。')) return; turns.forEach(t => { if (t.localAudio) URL.revokeObjectURL(t.localAudio); }); turns = []; persist(); renderHistory(); $('.history-status').textContent = '本机记录已清空；服务端会话没有删除。'; };
  $('.material-form').onsubmit = async e => { e.preventDefault(); try { await saveText($('.material-text').value, $('.material-scene').value); $('.material-status').textContent = '已保存本次试读文字。现在可以去查找原文和情节。'; } catch(error) { $('.material-status').textContent = message(error); } };
  for (const tab of root.querySelectorAll('[data-tool]')) {
    tab.onclick = () => select(tab.dataset.tool);
    tab.onkeydown = e => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return; e.preventDefault(); e.stopPropagation(); const tabs = [...root.querySelectorAll('[data-tool]')], i = tabs.indexOf(tab); const target = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; select(tabs[target].dataset.tool); tabs[target].focus(); };
  }
  window.addEventListener('pagehide', event => { if(!event.persisted)for(const files of supportingFiles.values()) for(const file of files) URL.revokeObjectURL(file.url); stopCapture(); questionRequest?.abort(); asrRequest?.abort(); ttsRequest?.abort(); });
  if(mainVideo)video.addEventListener('play',()=>pause('page'));
  root.addEventListener('play', event => { if(event.target.matches('audio,video')) { pause('tools'); root.querySelectorAll('audio,video').forEach(media=>{if(media!==event.target)media.pause()}); if(recorder?.state==='recording')stopCapture(); } }, true);
  select('video');
  $('.close-tools').onclick=()=>{stopCapture();answerAudio.pause();root.hidden=true;};
  return { update, select, stopCapture, videoSource:videoUrl, videoScope:()=>$('.video-scope').value, playVideo:play,
    pauseMedia(options={}) { if(video!==options.except)video.pause();root.querySelectorAll('audio,video').forEach(media=>{if(media!==options.except)media.pause()}); }, leave() { stopCapture(); video.pause(); answerAudio.pause(); questionRequest?.abort(); asrRequest?.abort(); ttsRequest?.abort(); historyRequest?.abort(); historyEpoch++; }, focusQuestion() { select('question'); $('.question-text').focus(); } };
}
