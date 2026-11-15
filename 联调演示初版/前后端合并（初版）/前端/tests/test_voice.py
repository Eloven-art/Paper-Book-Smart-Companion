"""Contract tests only; test provider bytes are NOT human voice or cloning evidence."""
import asyncio
import io
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from backend.server import create_app, ElevenLabsProvider, ProviderError

class TestProvider:
    def __init__(self, status='ready'):
        self.calls=[];self.status=status
    async def enroll(self,name,wav):
        assert wav[:4] == b'RIFF'
        return 'provider-stable-id', self.status
    async def synthesize(self,voice_id,text):
        self.calls.append((voice_id,text))
        return b'ID3-test-fixture-not-real-audio'

def sample_audio():
    output=io.BytesIO()
    with wave.open(output,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*16000*12)
    return output.getvalue()

class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.provider=TestProvider()
        self.client=TestClient(create_app(self.folder.name,self.provider))
    def tearDown(self):self.folder.cleanup()
    def register(self,client=None,**data):
        return (client or self.client).post('/api/voices',data={'name':'测试声音','consent':'yes',**data},
                           files={'audio':('audio.webm',sample_audio(),'audio/webm')})
    def test_reusable_voice_two_texts_and_restart(self):
        # WAV bytes deliberately named webm: real decode, not extension renaming.
        result=self.register();self.assertEqual(result.status_code,200,result.text)
        item=result.json()['voice'];self.assertEqual(item['status'],'ready')
        for text in ('这是第一段全新的文字。','现在换一句完全不同的话。'):
            response=self.client.post(f"/api/voices/{item['id']}/speech",json={'text':text})
            self.assertEqual(response.status_code,200);self.assertEqual(response.headers['content-type'],'audio/mpeg')
        self.assertEqual(len(self.provider.calls),2)
        self.assertEqual(self.provider.calls[0][0],self.provider.calls[1][0])
        self.assertNotEqual(self.provider.calls[0][1],self.provider.calls[1][1])
        self.assertEqual(self.client.post('/api/voices/selection',json={'voice_id':item['id']}).status_code,200)
        reopened=TestClient(create_app(self.folder.name,self.provider))
        self.assertEqual(reopened.get('/api/voices').json()['selected_voice_id'],item['id'])
        self.assertNotIn('provider_id',reopened.get('/api/voices').text)
        self.assertFalse(list(Path(self.folder.name).glob('*.wav')))
    def test_no_provider_explicitly_unavailable(self):
        with patch.dict('os.environ', {'ELEVENLABS_API_KEY':''}):
            with tempfile.TemporaryDirectory() as tmp:
                client=TestClient(create_app(tmp))
                self.assertFalse(client.get('/api/voice/status').json()['configured'])
                self.assertEqual(self.register(client).status_code,503)
                self.assertEqual(client.get('/api/voices').json()['voices'],[])
    def test_consent_empty_name_invalid_audio_and_text(self):
        self.assertEqual(self.register(consent='no').status_code,422)
        self.assertEqual(self.register(name='  ').status_code,422)
        bad=self.client.post('/api/voices',data={'name':'x','consent':'yes'},files={'audio':('x.wav',b'not audio','audio/wav')})
        self.assertEqual(bad.status_code,422)
        item=self.register().json()['voice']
        for text in ('','   ','x'*3001):
            self.assertEqual(self.client.post(f"/api/voices/{item['id']}/speech",json={'text':text}).status_code,422)
        self.assertEqual(self.client.post('/api/voices/missing/speech',json={'text':'你好'}).status_code,404)
    def test_pending_verification_not_usable(self):
        self.provider.status='verification_required';item=self.register().json()['voice']
        self.assertEqual(self.client.post('/api/voices/selection',json={'voice_id':item['id']}).status_code,409)
        self.assertEqual(self.client.post(f"/api/voices/{item['id']}/speech",json={'text':'你好'}).status_code,409)
    def test_auth_origin_private_files(self):
        self.assertEqual(self.client.post('/api/voices',headers={'origin':'https://other.example'},data={}).status_code,403)
        with patch.dict('os.environ',{'VOICE_ACCESS_TOKEN':'test-only'}):
            self.assertEqual(self.client.get('/api/voice/status').status_code,401)
            self.assertEqual(self.client.get('/api/voice.js').status_code,200)
            self.assertEqual(self.client.get('/api/voice/status',headers={'Authorization':'Bearer test-only'}).status_code,200)
        self.assertEqual(self.client.get('/backend/server.py').status_code,404)
        self.assertEqual(self.client.get('/.env').status_code,404)
        self.assertEqual(self.client.get('/api/voice.js').status_code,200)
    def test_provider_failure_never_saved(self):
        async def failed(*args):raise ProviderError('PROVIDER_LIMIT','额度不足',429)
        self.provider.enroll=failed
        self.assertEqual(self.register().status_code,429)
        self.assertEqual(self.client.get('/api/voices').json()['voices'],[])
    def test_official_provider_wire_format(self):
        requests=[]
        def handler(request):
            requests.append(request)
            if request.url.path=='/v1/voices/add':return httpx.Response(200,json={'voice_id':'abc','requires_verification':False})
            return httpx.Response(200,content=b'ID3-fixture',headers={'content-type':'audio/mpeg'})
        original=httpx.AsyncClient
        def client(**kwargs):return original(transport=httpx.MockTransport(handler),**kwargs)
        async def run():
            p=ElevenLabsProvider('test-only')
            with patch('backend.server.httpx.AsyncClient',client):
                self.assertEqual(await p.enroll('测试',b'RIFF-fixture'),('abc','ready'))
                await p.synthesize('abc','新文字')
        asyncio.run(run())
        self.assertIn(b'name="files"',requests[0].content)
        self.assertEqual(requests[1].url.path,'/v1/text-to-speech/abc')
        self.assertIn('新文字',requests[1].content.decode())

if __name__=='__main__':unittest.main()
