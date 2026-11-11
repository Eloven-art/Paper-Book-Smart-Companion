// Our own voice service contract, separate from competition/reference interfaces.
let accessToken = '', activeAudio = null, activeURL = null, activeRequest = null;
export function setVoiceAccessToken(value) { accessToken = value.trim(); }
export async function voiceRequest(path, {method='GET', body, signal, binary=false}={}) {
  const headers = accessToken ? {Authorization:'Bearer '+accessToken} : {};
  if (body && !(body instanceof FormData)) headers['Content-Type']='application/json';
  const controller = new AbortController(), abort = () => controller.abort();
  signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)controller.abort();
  const timer=setTimeout(abort,150000);
  try {
    const response=await fetch('/api/'+path,{method,headers,body:body instanceof FormData?body:body?JSON.stringify(body):undefined,signal:controller.signal});
    if (!response.ok) { let result;try{result=await response.json();}catch{} const e=new Error(result?.error?.message || `音色后端尚未连接（HTTP ${response.status}）。请使用压缩包内的后端启动方式。`);e.code=result?.error?.code;e.status=response.status;throw e; }
    if(binary){if(!response.headers.get('content-type')?.startsWith('audio/'))throw new Error('后端没有返回音频。');return response.blob();}
    if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('此预览未连接音色后端，请在本机启动完整服务。');
    return response.json();
  } catch(e) { if(e.name==='AbortError')throw new Error('等待已停止或超时；注册可能仍在后端进行，请刷新列表后再决定是否重试。');throw e; }
  finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
export const cloneVoice = {
  status:()=>voiceRequest('voice/status'), list:()=>voiceRequest('voices'),
  enroll:(body)=>voiceRequest('voices',{method:'POST',body}),
  select:(voice_id)=>voiceRequest('voices/selection',{method:'POST',body:{voice_id}}),
  audio:(id,text,signal)=>voiceRequest('voices/'+encodeURIComponent(id)+'/speech',{method:'POST',body:{text},binary:true,signal}),
  stop(){activeRequest?.abort();activeRequest=null;if(activeAudio){activeAudio.pause();activeAudio=null;}if(activeURL){URL.revokeObjectURL(activeURL);activeURL=null;}},
  async speak(id,text,onend=()=>{}) {
    this.stop();const controller=new AbortController();activeRequest=controller;
    const blob=await this.audio(id,text,controller.signal);if(controller.signal.aborted)return;
    activeURL=URL.createObjectURL(blob);activeAudio=new Audio(activeURL);activeAudio.onended=()=>{this.stop();onend();};activeAudio.onerror=()=>{this.stop();onend();};
    try{await activeAudio.play();}catch{this.stop();throw new Error('浏览器未能播放合成音频，请使用小熊面板中的播放控件。');}
  },
  get playing(){return !!activeAudio;}
};
