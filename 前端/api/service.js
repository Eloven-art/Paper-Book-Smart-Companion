// 默认仅本地模拟；禁止把此实现作为真实模型结果或比赛评测结果。
export const config = { mode: 'mock', baseURL: '/tts-story-book/api', timeout: 30000 };
export async function request(path, { body, method = 'POST', signal } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, config.timeout);
  try {
    const response = await fetch(config.baseURL + path, { method, signal: controller.signal,
      headers: body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
      body: body ? (body instanceof FormData ? body : JSON.stringify(body)) : undefined });
    const data = await response.json();
    if (!response.ok || data.success === false) throw new Error(data.error || data.message || `请求失败 (${response.status})`);
    return data;
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}
const delay = (ms, signal) => new Promise((resolve, reject) => {
  const cancel = () => { clearTimeout(timer); reject(new DOMException('已停止演示', 'AbortError')); };
  const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve(); }, ms);
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
});
export const demoBook = { id: 'winter-demo', title: '雪夜里的一盏灯', pages: [
  { title: '窗外的雪', text: '雪花轻轻落在窗台上。兔子翻开一本书，屋里的伙伴们慢慢围了过来。' },
  { title: '留一个位置', text: '狐狸搬来一张小凳子。熊熊把热茶放在桌上，给晚到的朋友留了一个暖和的位置。' },
  { title: '灯还亮着', text: '故事读完了，雪还没有停。大家约好，明天再来这里，一起翻开下一页。' }
] };
function mockOnly() { if (config.mode !== 'mock') throw new Error('真实服务尚未完成协议确认，请在适配层实现后启用。'); }
export const story = { async generate({ signal, onStage, fail = false }) {
  mockOnly(); onStage(0, '等待开始'); await delay(650, signal);
  onStage(1, '演示：正在读懂绘本'); await delay(1000, signal);
  onStage(2, '演示：正在准备画面和讲述'); await delay(1000, signal);
  if (fail) throw new Error('这是预设的演示失败，可以重试。');
  onStage(3, '演示：正在生成视频'); await delay(1000, signal);
  return structuredClone(demoBook);
} };
export const bookshelf = {
  list() { try { return JSON.parse(localStorage.getItem('storybook-shelf') || '[]'); } catch { return []; } },
  save(book) { const list = this.list().filter(x => x.id !== book.id); localStorage.setItem('storybook-shelf', JSON.stringify([...list, book])); },
  remove(id) { localStorage.setItem('storybook-shelf', JSON.stringify(this.list().filter(x => x.id !== id))); }
};
export const voice = {
  list() { return 'speechSynthesis' in window ? speechSynthesis.getVoices().filter(x => /^zh/i.test(x.lang)) : []; },
  stop() { window.speechSynthesis?.cancel(); },
  speak(text, name, onend = () => {}) {
    if (!('speechSynthesis' in window)) throw new Error('此浏览器不支持系统朗读。真实 TTS 尚未接入。');
    const voices = this.list(); if (!voices.length) throw new Error('此设备没有可用中文声音，真实 TTS 尚未接入。');
    this.stop(); const utterance = new SpeechSynthesisUtterance(text); utterance.lang = 'zh-CN'; utterance.voice = voices.find(v => v.name === name) || voices[0]; utterance.rate = .85;
    utterance.onend = onend; utterance.onerror = onend; speechSynthesis.speak(utterance);
  }
};
export const conversation = { async ask(question) { mockOnly(); await delay(600); return `【演示回复】已收到“${question}”。真实问答需要后端结合当前绘本与视频位置回答，此处不模拟模型答案。`; } };
export const challenge = { questions: [
  { question: '故事开始时，窗外是什么天气？', options: ['下雪', '下雨', '晴天'], answer: 0, explanation: '第一篇写着：雪花轻轻落在窗台上。' },
  { question: '谁搬来了一张小凳子？', options: ['熊熊', '狐狸', '猫头鹰'], answer: 1, explanation: '第二篇里，狐狸搬来了一张小凳子。' },
  { question: '伙伴们约好明天做什么？', options: ['去远方旅行', '收起所有书', '一起继续读书'], answer: 2, explanation: '最后一篇里，他们约好一起翻开下一页。' }
] };
