import { cloneVoice } from './api/voice.js';
import { openVoicePanel } from './ui/voice-panel.js';
import { story, bookshelf, voice, conversation, challenge, demoBook } from './api/service.js';
const $ = s => document.querySelector(s), dialog = $('#dialog'), content = $('#content');
const state = { book: null, page: 0, voice: localStorage.getItem('storybook-voice') || '', task: null, status: 'idle', history: [], lastAnimal: null, cleanup: null, cloned: null, timer: null, quiz: 0, score: 0, answers: [] };
function el(tag, text, cls) { const e = document.createElement(tag); if (text) e.textContent = text; if (cls) e.className = cls; return e; }
function button(text, action, cls) { const b = el('button', text, cls); b.type = 'button'; b.onclick = action; return b; }
function toast(text) { clearTimeout(state.timer); $('#toast').textContent = text; $('#toast').classList.add('on'); state.timer = setTimeout(() => $('#toast').classList.remove('on'), 3500); }
function close() { dialog.close(); }
function show(kind, title, intro) { state.cleanup?.();state.cleanup=null;content.dataset.view=''; content.replaceChildren(); $('#paper').className = 'paper ' + ({ fox:'scroll',bear:'voice',owl:'letter' }[kind] || ''); content.append(el('small','纸书智伴 · 演示模式'), el('h2',title)); if (intro) content.append(el('p',intro)); if (!dialog.open) dialog.showModal(); }
function actions(...items) { const d = el('div', '', 'actions'); d.append(...items); content.append(d); }
function feedback(id, text) { close(); const a = document.querySelector(`[data-open="${id}"]`); a?.classList.add('selected'); setTimeout(() => a?.classList.remove('selected'), 900); toast(text); }
function rabbit() {
  if (['waiting','running','failed','success','cancelled'].includes(state.status)) return taskView();
  show('rabbit','今天读哪一本？','先选一本绘本，再让故事慢慢展开。');
  const b = button('', prepare, 'choice'); b.append(el('span','书','book-icon'), el('strong', demoBook.title), el('small','原创演示 · 3 页')); content.append(b);
  content.append(el('p','当前只有演示绘本。比赛授权样本尚未导入，不会上传到任何服务。','muted'));
  const label = el('label','导入绘本图片（本地检查）','field'), input = document.createElement('input'); input.type='file'; input.accept='image/png,image/jpeg,image/webp'; input.multiple=true;
  const info = el('p','','error'); input.onchange = () => { const fs=[...input.files]; info.textContent=fs.some(f=>!['image/png','image/jpeg','image/webp'].includes(f.type))?'请选择 PNG、JPG 或 WebP 图片。':fs.some(f=>f.size>10*1024*1024)?'单张图片请不超过 10 MB。':`已选择 ${fs.length} 张图片；上传与 OCR 接口待确认，文件未发送。`; }; label.append(input); content.append(label,info);
}
function prepare() {
  show('rabbit','故事就要开始了','这次先用演示绘本走一遍流程。');
  content.append(el('p',`讲述声音：${state.cloned ? state.cloned.name+'（克隆音色）' : '尚未选定克隆音色'}`));
  actions(button('试听', () => speak('雪落在窗外，故事留在灯下。')),button('换个声音', () => bear(prepare)));
  const details=document.createElement('details'); details.innerHTML='<summary>故事设置</summary><label class="field">故事主题<input name="theme" value="雪夜里的陪伴" maxlength="100"></label><label class="field">主角<input name="character" value="兔子与朋友们" maxlength="60"></label><label class="field">场景<input name="scene" value="冬日小屋" maxlength="60"></label><p class="muted">本地演示不按输入生成内容；真实字段以后端协议为准。</p>';
  content.append(details); const error=el('p','','error'); error.setAttribute('role','alert'); content.append(error);
  const failure=el('label','','muted'), check=document.createElement('input'); check.type='checkbox'; failure.append(check,document.createTextNode(' 开发演示：模拟一次失败')); content.append(failure);
  actions(button('开始生成',()=>{ const fields=[...details.querySelectorAll('input')]; if(fields.some(i=>!i.value.trim())) { error.textContent='请填写主题、主角和场景。'; details.open=true; fields.find(i=>!i.value.trim()).focus(); return; } startTask(check.checked); },'primary'),button('返回选书',rabbit));
}
async function startTask(fail=false) {
  state.task=new AbortController(); state.status='waiting'; state.stage=0; state.stageText='等待开始'; taskView();
  try { state.book=await story.generate({signal:state.task.signal,fail,onStage:(n,text)=>{state.status=n?'running':'waiting';state.stage=n;state.stageText=text;if(dialog.open&&content.dataset.view==='task')taskView();}}); state.status='success'; if(dialog.open&&content.dataset.view==='task') taskView(); else toast('演示流程已完成，点兔子翻开绘本。'); }
  catch(e) { state.status=e.name==='AbortError'?'cancelled':'failed';state.error=e.message;if(dialog.open&&content.dataset.view==='task')taskView(); }
}
function taskView() {
  show('rabbit',state.status==='success'?'故事准备好了':state.status==='failed'?'这次没有完成':state.status==='cancelled'?'演示已停止':'让故事慢慢展开'); content.dataset.view='task';
  if(['waiting','running'].includes(state.status)){ const steps=el('div','','steps');for(let i=0;i<4;i++)steps.append(el('span','',i<=state.stage?'done':''));content.append(steps,el('p',state.stageText,'status-line'));actions(button('停止演示',()=>state.task.abort()),button('回小屋等一会儿',close)); }
  else if(state.status==='success')actions(button('翻开绘本',()=>{feedback('rabbit','故事准备好了。');openBook(state.book);},'primary'),button('重新选书',()=>{state.status='idle';rabbit();}));
  else { content.append(el('p',state.status==='failed'?state.error:'仅停止本地模拟，没有向后端发送取消请求。','status-line'));actions(button('重试',()=>startTask(false),'primary'),button('返回选书',()=>{state.status='idle';rabbit();})); }
  content.append(el('p','演示状态不代表真实 OCR、模型或视频任务进度。','muted'));
}
function fox() { show('fox','我的书架','喜欢的故事，留着下次再读。'); const list=bookshelf.list(); if(!list.length)content.append(el('p','书架还空着。和兔子一起读一本，再把它放在这里。')); for(const book of list){ const row=el('div','','choice');row.append(el('strong',book.title),button('继续阅读',()=>{close();openBook(book);}),button('移除',()=>{if(confirm('从本机书架移除这本绘本？')){try{bookshelf.remove(book.id);fox();}catch{toast('保存失败，请检查浏览器存储权限。');}}}));content.append(row); } content.append(el('p','当前书架仅保存在本浏览器，清理网站数据会移除记录；不是云端保存。','muted')); }
function bear(returnTo=null) {
  show('bear','留住一个熟悉的声音');content.querySelector('small').textContent='纸书智伴 · 音色服务';
  openVoicePanel({content,toast,close,onSelected:chosen=>{state.cloned=chosen;state.voice=chosen.name;if(returnTo)setTimeout(returnTo,0);},onClosed:cleanup=>{state.cleanup=cleanup;}});
}
function owl() { show('owl','故事小挑战',state.book?'还记得故事里的事吗？来试试这几道小题吧。':'先读一个故事，再来找我吧。');content.append(el('p','固定的原创演示题，仅用于验证答题流程，不是模型出题或比赛评测。','muted'));if(state.book)actions(button('试一试',()=>{state.quiz=0;state.score=0;state.answers=[];quiz();},'primary'),button('下次再来',close));else actions(button('去找兔子',rabbit)); }
function quiz() { const q=challenge.questions[state.quiz];show('owl',`第 ${state.quiz+1} 题 / ${challenge.questions.length}`,q.question);const opts=el('div','','question-options');q.options.forEach((option,i)=>opts.append(button(option,()=>{const correct=i===q.answer;state.score+=Number(correct);state.answers.push({q:q.question,explanation:q.explanation,correct});opts.querySelectorAll('button').forEach(b=>b.disabled=true);content.append(el('p',(correct?'答对了。':'再读一遍就能发现。')+q.explanation));actions(button(state.quiz===2?'看看结果':'下一题',()=>{state.quiz++;if(state.quiz<challenge.questions.length)quiz();else{show('owl','又发现了一些故事里的事',`这次答对了 ${state.score} 道，共 ${challenge.questions.length} 道。`);state.answers.forEach(a=>content.append(el('p',`${a.correct?'✓':'○'} ${a.q} ${a.explanation}`)));actions(button('回到故事',()=>{feedback('owl','带着新发现，再翻一页吧。');openBook(state.book);},'primary'));}},'primary'));})));content.append(opts); }
function ask() { show('bear','故事里有什么想问的？','可以写下来；真实语音问答将在后端联调后启用。');const history=el('div','','history');state.history.forEach(item=>history.append(el('p',item)));content.append(history);const label=el('label','我的问题','field'),input=document.createElement('textarea');input.rows=3;input.maxLength=500;label.append(input);const error=el('p','','error');error.setAttribute('role','alert');content.append(label,error);const send=button('交给熊熊',async()=>{if(!input.value.trim()){error.textContent='先写下你想问的问题吧。';input.focus();return;}send.disabled=true;try{const text=input.value.trim();const answer=await conversation.ask(text);state.history.push('我：'+text,'熊熊：'+answer);ask();}catch(e){error.textContent=e.message;send.disabled=false;}},'primary');actions(send,button('语音说明',()=>toast('录音与 ASR 待接入；当前不申请麦克风，也不上传声音。'))); }
async function speak(text){
  if(!state.cloned){toast('请先在小熊那里连接服务、保存并选择一个声音。');return;}
  $('#audio-status').textContent='正在用所选音色合成新文字……';
  try{await cloneVoice.speak(state.cloned.id,text,()=>{$('#audio-status').textContent='朗读已结束';$('#narrate').textContent='朗读';});$('#audio-status').textContent='克隆音色：'+state.cloned.name;$('#narrate').textContent='停止朗读';}
  catch(e){$('#audio-status').textContent=e.message;toast(e.message);}
}
function openBook(book){voice.stop();cloneVoice.stop();state.book=book;state.page=Math.min(book.progress||0,book.pages.length-1);$('#home').hidden=true;$('#reader').hidden=false;renderPage();window.scrollTo(0,0);}
function renderPage(){voice.stop();cloneVoice.stop();$('#narrate').textContent='朗读';$('#audio-status').textContent='';const p=state.book.pages[state.page];$('#reading-title').textContent=state.book.title;$('#page-count').textContent=`第 ${state.page+1} 页 / ${state.book.pages.length} 页`;$('#page-title').textContent=p.title;$('#page-text').textContent=p.text;$('#prev').disabled=state.page===0;$('#next').disabled=state.page===state.book.pages.length-1;$('#object-result').hidden=true;}
$('#close').onclick=close;dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close();}});dialog.addEventListener('close',()=>{state.cleanup?.();state.cleanup=null;content.dataset.view='';voice.stop();cloneVoice.stop();state.lastAnimal?.focus();});
for(const b of document.querySelectorAll('[data-open]')) b.onclick=()=>{state.lastAnimal=b;({rabbit,fox,bear,owl}[b.dataset.open])();};
$('#motion').onclick=()=>{const reduced=document.body.classList.toggle('reduce');$('#motion').setAttribute('aria-pressed',String(reduced));$('#motion').textContent=reduced?'恢复动态':'减少动态';};
$('#back-home').onclick=()=>{voice.stop();cloneVoice.stop();$('#home').hidden=false;$('#reader').hidden=true;};$('#prev').onclick=()=>{state.page--;renderPage();};$('#next').onclick=()=>{state.page++;renderPage();};$('#narrate').onclick=()=>{if(cloneVoice.playing){voice.stop();cloneVoice.stop();$('#narrate').textContent='朗读';$('#audio-status').textContent='已停止朗读';}else{speak(state.book.pages[state.page].text);if(cloneVoice.playing)$('#narrate').textContent='停止朗读';}};
$('#reader-ask').onclick=ask;$('#identify').onclick=()=>{$('#object-result').hidden=false;toast('演示识物：固定标注，未调用视觉识别模型。');};$('#save-book').onclick=()=>{try{bookshelf.save({...state.book,progress:state.page});toast('已放入本机书架。');}catch{toast('保存失败，浏览器存储空间不足或权限受限。');}};
window.addEventListener('keydown',e=>{if(dialog.open||$('#reader').hidden||/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;if(e.key==='ArrowRight'&&!$('#next').disabled)$('#next').click();if(e.key==='ArrowLeft'&&!$('#prev').disabled)$('#prev').click();});

cloneVoice.list().then(data=>{const selected=data.voices.find(v=>v.id===data.selected_voice_id&&v.status==='ready');if(selected)state.cloned={id:selected.id,name:selected.name};}).catch(()=>{});
