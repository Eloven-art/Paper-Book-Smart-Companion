// 前端与后端的唯一接口层。字段以 docs/接口约定_v0.1.md 为准。
// 默认连接真实接口 /api/v1；地址后加 ?mock=1 切到假接口 /mock/api/v1（固定示例数据，只用于联调界面）。
// 后端连不上时，兔子入口仍可用本地演示书走一遍流程（明确标注为演示）。
const params = new URLSearchParams(location.search);
const mockMode = params.get('mock') === '1';
export const config = { mode: mockMode ? 'mock' : 'real', baseURL: mockMode ? '/mock/api/v1' : '/api/v1', timeout: 30000, pollInterval: 1000 };

export class ApiError extends Error {
  constructor(message, { code = 'UNKNOWN', status = 0, details = null, runId = null } = {}) {
    super(message); this.name = 'ApiError'; this.code = code; this.status = status; this.details = details; this.runId = runId;
  }
}

// 统一请求：解析响应外壳 {status, data, error, run_id}，成功返回 data，失败抛 ApiError。
export async function request(path, { body, method = body ? 'POST' : 'GET', signal, timeout = config.timeout } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, timeout);
  try {
    let response;
    try {
      response = await fetch(config.baseURL + path, { method, signal: controller.signal,
        headers: !body || body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
        body: body ? (body instanceof FormData ? body : JSON.stringify(body)) : undefined });
    } catch (e) {
      if (e.name === 'AbortError') throw signal?.aborted ? new DOMException('已停止', 'AbortError') : new ApiError('请求超时，请稍后重试。', { code: 'TIMEOUT' });
      throw new ApiError('连不上后端服务。请在项目根目录运行 python -m server，再从 http://127.0.0.1:8000 打开。', { code: 'BACKEND_UNREACHABLE' });
    }
    let envelope = null;
    try { envelope = await response.json(); } catch { /* 非 JSON */ }
    if (!envelope || !('status' in envelope)) throw new ApiError(`后端返回格式不对（HTTP ${response.status}）。`, { code: 'BAD_RESPONSE', status: response.status });
    if (!response.ok || envelope.status !== 'ok') {
      const err = envelope.error || {};
      throw new ApiError(err.message || `请求失败（HTTP ${response.status}）`, { code: err.code, status: response.status, details: err.details, runId: envelope.run_id });
    }
    return envelope.data;
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const cancel = () => { clearTimeout(timer); reject(new DOMException('已停止', 'AbortError')); };
  const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve(); }, ms);
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
});

// ---------------------------------------------------------------- 数据映射：后端分页数据 → 阅读页使用的结构
function mapBook(meta, pages) {
  return {
    id: meta.book_id, title: meta.title, source: 'backend', mode: config.mode,
    ageRange: meta.age_range, characters: meta.characters || [],
    video: meta.video?.url ? { url: meta.video.url, duration: meta.video.duration, stale: !!meta.video.stale } : null,
    pages: pages.map(p => ({
      no: p.page_no,
      title: `第 ${p.page_no} 页`,
      text: p.narration || p.ocr_text?.full_text || '',
      original: p.ocr_text?.full_text || '',
      narrationMethod: p.narration_meta?.method || (p.narration ? 'provided' : null),
      needsReview: !!p.narration_meta?.needs_review,
      image: p.page_image_url, size: p.image_size,
      video: p.video_clip_url, videoFallback: !!p.video_meta?.is_fallback,
      audio: p.narration_audio_url, audioPlaceholder: !!p.narration_audio?.is_placeholder,
      timeRange: p.time_range, objectCount: (p.objects || []).length,
      safety: p.safety_check?.status || null,
    })),
  };
}

export const books = {
  list: () => request('/books').then(d => d.books),
  async load(bookId) {
    const [meta, pages] = await Promise.all([request(`/books/${encodeURIComponent(bookId)}`), request(`/books/${encodeURIComponent(bookId)}/pages`)]);
    return mapBook(meta, pages.pages);
  },
  upload(files, title) {
    const form = new FormData();
    if (title) form.append('title', title);
    for (const f of files) form.append('pages', f, f.name);
    return request('/books', { body: form, timeout: 120000 });
  },
};

// ---------------------------------------------------------------- 任务
export const tasks = {
  get: (id, signal) => request(`/tasks/${encodeURIComponent(id)}`, { signal }),
  cancel: id => config.mode === 'mock' ? Promise.resolve() : request(`/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  async wait(id, { signal, onUpdate } = {}) {
    for (;;) {
      signal?.throwIfAborted();
      const task = await this.get(id, signal);
      onUpdate?.(task);
      if (['succeeded', 'failed', 'cancelled'].includes(task.status)) return task;
      await sleep(config.pollInterval, signal);
    }
  },
};

// 生成：旁白任务 → 视频任务 → 读回整本数据。onStage(第几步 0~3, 说明文字)。
export const story = {
  async generate({ bookId, force = false, signal, onStage, fail = false }) {
    const extra = fail && config.mode === 'mock' ? '?mock_fail=1' : '';
    let current = null;
    const stop = () => { if (current) tasks.cancel(current).catch(() => {}); };
    signal?.addEventListener('abort', stop, { once: true });
    try {
      const steps = [
        ['narration', 1, '正在把原文改写成朗读旁白'],
        ['video', 2, '正在配音、生成视频片段'],
      ];
      onStage(0, '已提交，等待开始');
      for (const [kind, stage, label] of steps) {
        const submitted = await request(`/generate/${kind}${extra}`, { body: { book_id: bookId, force }, signal });
        current = submitted.task_id;
        const done = await tasks.wait(current, { signal, onUpdate: t => {
          if (t.status === 'running') onStage(kind === 'video' && t.progress >= 0.88 ? 3 : stage, t.message || label);
        } });
        current = null;
        if (done.status === 'cancelled') throw new DOMException('已停止', 'AbortError');
        if (done.status !== 'succeeded') throw new ApiError(done.error?.message || '生成失败', { code: done.error?.code });
      }
      onStage(3, '正在读取结果');
      return await books.load(bookId);
    } finally { signal?.removeEventListener('abort', stop); }
  },
};

// ---------------------------------------------------------------- 问答与识物
export const conversation = {
  ask({ bookId, pageNo, question, playTime = null, sessionId }) {
    return request('/qa/ask', { body: { book_id: bookId, page_no: pageNo, question, play_time: playTime, session_id: sessionId } });
  },
};
export const touch = {
  identify({ bookId, pageNo, x, y, playTime = null }) {
    return request('/touch/identify', { body: { book_id: bookId, page_no: pageNo, x, y, play_time: playTime } });
  },
};
export const health = () => request('/health', { timeout: 5000 });

// ---------------------------------------------------------------- 本机书架（只存书的 ID 和读到第几页，内容每次从后端读）
export const bookshelf = {
  list() { try { return JSON.parse(localStorage.getItem('storybook-shelf') || '[]'); } catch { return []; } },
  save(book, progress) {
    const entry = book.source === 'backend' ? { id: book.id, title: book.title, source: 'backend', mode: book.mode, progress } : { ...book, progress };
    const list = this.list().filter(x => x.id !== book.id); localStorage.setItem('storybook-shelf', JSON.stringify([...list, entry]));
  },
  remove(id) { localStorage.setItem('storybook-shelf', JSON.stringify(this.list().filter(x => x.id !== id))); }
};

// ---------------------------------------------------------------- 播放后端生成的旁白音频
let pageAudio = null;
export const narration = {
  play(url, onend = () => {}) {
    this.stop(); pageAudio = new Audio(url); pageAudio.onended = () => { pageAudio = null; onend(); }; pageAudio.onerror = () => { pageAudio = null; onend(); };
    return pageAudio.play();
  },
  stop() { if (pageAudio) { pageAudio.pause(); pageAudio = null; } },
  get playing() { return !!pageAudio; }
};

// 兼容原有代码：系统朗读已不再使用，保留空实现。
export const voice = { list: () => [], stop() {}, speak() { throw new Error('请使用后端生成的旁白配音或小熊的克隆音色。'); } };

// ---------------------------------------------------------------- 后端连不上时的本地演示（不调用任何服务）
export const demoBook = { id: 'winter-demo', title: '雪夜里的一盏灯', source: 'local', pages: [
  { title: '窗外的雪', text: '雪花轻轻落在窗台上。兔子翻开一本书，屋里的伙伴们慢慢围了过来。' },
  { title: '留一个位置', text: '狐狸搬来一张小凳子。熊熊把热茶放在桌上，给晚到的朋友留了一个暖和的位置。' },
  { title: '灯还亮着', text: '故事读完了，雪还没有停。大家约好，明天再来这里，一起翻开下一页。' }
] };
export const localDemo = { async generate({ signal, onStage }) {
  onStage(0, '本地演示：等待开始'); await sleep(500, signal);
  onStage(1, '本地演示：没有连接后端，不做真实处理'); await sleep(700, signal);
  onStage(3, '本地演示：准备翻开'); await sleep(500, signal);
  return structuredClone(demoBook);
} };
export const challenge = { bookId: 'winter-demo', questions: [
  { question: '故事开始时，窗外是什么天气？', options: ['下雪', '下雨', '晴天'], answer: 0, explanation: '第一篇写着：雪花轻轻落在窗台上。' },
  { question: '谁搬来了一张小凳子？', options: ['熊熊', '狐狸', '猫头鹰'], answer: 1, explanation: '第二篇里，狐狸搬来了一张小凳子。' },
  { question: '伙伴们约好明天做什么？', options: ['去远方旅行', '收起所有书', '一起继续读书'], answer: 2, explanation: '最后一篇里，他们约好一起翻开下一页。' }
] };
