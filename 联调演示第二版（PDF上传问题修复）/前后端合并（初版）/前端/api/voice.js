import { config, request } from './service.js';
let activeAudio = null, activeURL = null, activeRequest = null, activeTextKey = null;

async function cosy(path, options) {
  if(config.mode!=='real')throw new Error('在真实后端模式下可以使用 CosyVoice。');
  return request('/' + path,{timeout:150000,...options});
}
function voiceView(item) {
  let name = item.prefix || item.voice_id;
  try { name = localStorage.getItem('pbsc-cosyvoice-name:' + item.voice_id) || name; } catch { /* optional storage */ }
  const status = String(item.status || '').toUpperCase();
  return {id:item.voice_id, name, status:['OK','READY','SUCCEEDED','AVAILABLE'].includes(status)?'ready':status};
}
export const cloneVoice = {
  async status() {
    if(config.mode!=='real')return {configured:false,preview:true};
    const health=await cosy('health');
    return {configured:health.voices.configured && health.tts_provider==='cosyvoice', audio_tools:true};
  },
  async list() {
    if(config.mode!=='real')return {voices:[]};
    const [list, selection]=await Promise.all([cosy('voices/cosyvoice?page_size=100'),cosy('voices/cosyvoice/selection')]);
    return {voices:list.voices.map(voiceView),selected_voice_id:selection.voice_id};
  },
  async enroll(body) {
    body.set('prefix', String(body.get('name') || '').replace(/[^a-z0-9]/gi,'').slice(0,10) || 'pbsc');
    const result=await cosy('voices/cosyvoice',{method:'POST',body});
    try { localStorage.setItem('pbsc-cosyvoice-name:' + result.voice_id, body.get('name')); } catch { /* optional storage */ }
    return {voice:voiceView({...result.voice,voice_id:result.voice_id,status:result.status})};
  },
  async select(id) {
    const voice=voiceView(await cosy('voices/cosyvoice/'+encodeURIComponent(id)));
    if(voice.status!=='ready')throw new Error('此音色尚未通过服务商审核。');
    const result=await cosy('voices/cosyvoice/selection',{method:'POST',body:{voice_id:id,name:voice.name}});
    return {selected_voice_id:result.voice_id,name:voice.name};
  },
  async audio(id,text,signal) {
    const result=await cosy('tts',{method:'POST',body:{text,voice:id},signal});
    if(result.is_placeholder)throw new Error('真实配音服务未就绪。');
    const response=await fetch(result.audio_url,{signal});
    if(!response.ok)throw new Error('合成音频下载失败。');
    return response.blob();
  },
  pause(){if(activeAudio)activeAudio.pause();else activeRequest?.abort();},
  stop(){activeTextKey=null;activeRequest?.abort();activeRequest=null;if(activeAudio){activeAudio.pause();activeAudio=null;}if(activeURL){URL.revokeObjectURL(activeURL);activeURL=null;}},
  async speak(id,text,onend=()=>{}) {
    if(activeAudio&&activeTextKey===id+'|'+text){await activeAudio.play();return;}
    this.stop();activeTextKey=id+'|'+text;const controller=new AbortController();activeRequest=controller;
    const blob=await this.audio(id,text,controller.signal);if(controller.signal.aborted)return;
    activeURL=URL.createObjectURL(blob);activeAudio=new Audio(activeURL);activeAudio.onended=()=>{this.stop();onend();};activeAudio.onerror=()=>{this.stop();onend();};
    try{await activeAudio.play();}catch{this.stop();throw new Error('浏览器未能播放合成音频，请使用小熊面板中的播放控件。');}
  },
  get playing(){return !!activeAudio&&!activeAudio.paused;}
};
