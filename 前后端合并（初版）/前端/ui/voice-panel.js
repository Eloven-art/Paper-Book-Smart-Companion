import {cloneVoice,setVoiceAccessToken} from '../api/voice.js';
export function openVoicePanel({content,onSelected,toast,close,onClosed}) {
  let active=true,stream=null,recorder=null,recordTimer=null,clock=null,source=null,sourceURL=null,resultURL=null,requesting=false;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
  const action=(text,fn,cls)=>{const b=node('button',text,cls);b.type='button';b.onclick=fn;return b;};
  const status=node('p','正在检查音色服务……','status-line');status.setAttribute('role','status');
  const error=node('p','','error');error.setAttribute('role','alert');
  content.append(status,node('p','录一段声音，留下它的音色。以后换一段文字，也能用同一个声音讲述。'));
  const credentials=node('details');credentials.append(node('summary','后端访问设置'));
  const token=node('input');token.type='password';token.autocomplete='off';token.placeholder='仅在后端启用 VOICE_ACCESS_TOKEN 时填写';const tokenLabel=node('label','后端访问凭证（不是服务商 API 密钥）','field');tokenLabel.append(token);credentials.append(tokenLabel,action('连接',()=>{setVoiceAccessToken(token.value);token.value='';check();}));content.append(credentials);
  const form=node('form'), nameLabel=node('label','给声音起个名字','field'),name=node('input');name.maxLength=40;name.placeholder='例如：我的故事声音';name.required=true;nameLabel.append(name);
  const uploadLabel=node('label','参考音频','field'),file=node('input');file.type='file';file.accept='audio/*';uploadLabel.append(file);
  const sourceInfo=node('p','10—120 秒，建议 30—60 秒；清晰单人说话，尽量没有音乐和回声。','muted');
  const recorderRow=node('div','','actions'),timer=node('span','','muted'),record=action('开始录音',async()=>{
    error.textContent='';
    if(recorder?.state==='recording'){recorder.stop();return;}
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){error.textContent='此浏览器不能录音，请上传已有音频；录音需要 HTTPS 或 localhost。';return;}
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:true});if(!active){stream.getTracks().forEach(t=>t.stop());return;}
      const format=['audio/webm;codecs=opus','audio/mp4','audio/webm','audio/ogg;codecs=opus'].find(x=>MediaRecorder.isTypeSupported(x));
      recorder=new MediaRecorder(stream,format?{mimeType:format}:undefined);const chunks=[];recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      recorder.onstop=()=>{clearTimeout(recordTimer);clearInterval(clock);stream?.getTracks().forEach(t=>t.stop());record.textContent='重新录音';file.disabled=false;if(!active)return;const type=recorder.mimeType||chunks[0]?.type||'audio/webm';setSource(new Blob(chunks,{type}),type.includes('mp4')?'recording.m4a':type.includes('ogg')?'recording.ogg':'recording.webm');};
      recorder.onerror=()=>{clearTimeout(recordTimer);clearInterval(clock);stream?.getTracks().forEach(t=>t.stop());file.disabled=false;record.textContent='重新录音';error.textContent='录音中断，请重新录制或上传文件。';};
      recorder.start();file.disabled=true;record.textContent='停止录音';const started=Date.now();clock=setInterval(()=>timer.textContent=`已录 ${Math.floor((Date.now()-started)/1000)} 秒`,300);recordTimer=setTimeout(()=>{if(recorder.state==='recording')recorder.stop();},120000);
    }catch{stream?.getTracks().forEach(t=>t.stop());error.textContent='没有获得麦克风权限，请检查浏览器设置，或上传音频。';}
  });recorderRow.append(record,timer);
  const reference=node('audio');reference.controls=true;reference.hidden=true;reference.setAttribute('aria-label','参考声音录音');
  function setSource(blob,filename){if(sourceURL)URL.revokeObjectURL(sourceURL);source={blob,filename};sourceURL=URL.createObjectURL(blob);reference.src=sourceURL;reference.hidden=false;sourceInfo.textContent=`已准备参考音频：${filename}。这只是原录音试听，不是克隆结果。`;}
  file.onchange=()=>{const f=file.files[0];if(!f)return;if(f.size>20*1024*1024){error.textContent='参考音频不能超过 20 MB。';file.value='';return;}error.textContent='';setSource(f,f.name);};
  const consent=node('label','','consent'),checkbox=node('input');checkbox.type='checkbox';consent.append(checkbox,document.createTextNode(' 我确认这是本人或已获授权的成人声音，并同意将样本发送给配置的音色服务进行克隆。'));
  const register=node('button','保存这个声音','primary');register.type='submit';register.disabled=true;
  form.append(nameLabel,uploadLabel,sourceInfo,recorderRow,reference,consent,register);
  form.onsubmit=async e=>{e.preventDefault();error.textContent='';if(!name.value.trim()){error.textContent='先给声音起个名字吧。';name.focus();return;}if(recorder?.state==='recording'){error.textContent='请先停止录音。';return;}if(!source){error.textContent='请先录音或上传音频。';return;}if(!checkbox.checked){error.textContent='请确认声音授权与样本发送。';return;}if(requesting)return;requesting=true;register.disabled=true;status.textContent='正在处理并注册声音；此步骤会调用外部服务。';
    const data=new FormData();data.append('name',name.value.trim());data.append('consent','yes');data.append('audio',source.blob,source.filename);
    try{const result=await cloneVoice.enroll(data);if(!active)return;status.textContent=result.voice.status==='ready'?'声音已保存。换一句话试试吧。':'声音已创建，但服务商要求进一步验证，暂时不能合成。';await loadList(result.voice.id);}catch(e){if(active){error.textContent=e.message;status.textContent='声音尚未确认就绪。';}}finally{requesting=false;if(active)register.disabled=false;}
  };
  content.append(form,error,node('h3','让这个声音说点什么'));
  const selectionLabel=node('label','选择已保存的声音','field'),selection=node('select');selectionLabel.append(selection);
  const textLabel=node('label','想说的话','field'),text=node('textarea');text.rows=3;text.maxLength=3000;text.value='今天我们换一个新故事。窗外下着雪，屋里亮着一盏温暖的灯。';textLabel.append(text);
  const output=node('audio');output.controls=true;output.hidden=true;output.setAttribute('aria-label','克隆音色生成的音频');
  const resultNote=node('p','','muted');const synth=action('用这个声音生成',async()=>{
    error.textContent='';if(!selection.value){error.textContent='请先保存或选择一个已就绪的声音。';return;}if(!text.value.trim()){error.textContent='写一句想让它说的话吧。';text.focus();return;}
    synth.disabled=true;status.textContent='正在用选定的音色合成新文字……';output.pause();output.hidden=true;resultNote.textContent='';
    try{const blob=await cloneVoice.audio(selection.value,text.value.trim());if(!active)return;if(resultURL)URL.revokeObjectURL(resultURL);resultURL=URL.createObjectURL(blob);output.src=resultURL;output.hidden=false;status.textContent='新的语音已生成，请点击播放。';resultNote.textContent='这是通过所选音色 ID 合成的新音频；可改写上面的文字，再次生成验证。';}catch(e){if(active){error.textContent=e.message;status.textContent='这次没有生成音频。';}}finally{if(active)synth.disabled=false;}
  },'primary');
  const choose=action('选作故事讲述声音',async()=>{error.textContent='';if(!selection.value){error.textContent='请选择一个已就绪的声音。';return;}choose.disabled=true;try{const selected=await cloneVoice.select(selection.value);onSelected({id:selected.selected_voice_id,name:selected.name});close();toast('选好了。阅读时会用这个音色合成新文字。');}catch(e){error.textContent=e.message;}finally{choose.disabled=false;}});
  const row=node('div','','actions');row.append(synth,choose);content.append(selectionLabel,textLabel,row,output,resultNote,node('p','单次最多 3000 字，这是本版本的保护上限；可多次更换文字。长篇故事按段落使用同一音色 ID 合成，不需要反复注册。','muted'));
  const refresh=action('刷新音色列表',()=>loadList());content.append(refresh);
  async function loadList(preferred){try{const list=await cloneVoice.list();if(!active)return;selection.replaceChildren();const empty=node('option','请选择已就绪的声音');empty.value='';selection.append(empty);list.voices.forEach(v=>{const op=node('option',v.name+(v.status==='ready'?'':'（需服务商验证）'));op.value=v.id;op.disabled=v.status!=='ready';selection.append(op);});const id=preferred||list.selected_voice_id;if(list.voices.some(v=>v.id===id&&v.status==='ready'))selection.value=id;}catch(e){if(active)error.textContent=e.message;}}
  async function check(){try{const result=await cloneVoice.status();if(!active)return;register.disabled=!result.configured||!result.audio_tools;synth.disabled=!result.configured;status.textContent=!result.configured?'尚未连接克隆服务：后端未配置密钥。':!result.audio_tools?'克隆服务已配置，但服务器缺少 FFmpeg，暂不能注册。':'音色服务已配置。首次请求时检查账户权限；注册与生成可能消耗服务额度。';await loadList();}catch(e){if(active){register.disabled=true;synth.disabled=true;status.textContent='当前在线预览尚未连接音色后端。';error.textContent=e.message;content.append(node('p','下载源码，在本机启动 Python 后端后，从 http://localhost:8000 打开同一个小屋，即可使用此入口。','muted'));}}}
  check();
  onClosed(()=>{active=false;clearTimeout(recordTimer);clearInterval(clock);if(recorder?.state==='recording')recorder.stop();stream?.getTracks().forEach(t=>t.stop());reference.pause();output.pause();if(sourceURL)URL.revokeObjectURL(sourceURL);if(resultURL)URL.revokeObjectURL(resultURL);});
}
