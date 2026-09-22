// No npm packages needed: node --test tests/test_audio_capture.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function harness({denied = false, meterFails = false, pending = false} = {}) {
  const timers = new Map(), frames = new Map(), errors = [], audio = [], states = [], notices = [];
  let id = 0, resolvePermission, constraints, resumed = 0;
  const track = {readyState:'live', label:'USB microphone', stopped:false, stop(){this.stopped=true;}};
  const stream = {getTracks:()=>[track],getAudioTracks:()=>[track]};
  class AudioContext {
    constructor(){this.state='suspended';}
    resume(){resumed++;this.state='running';return Promise.resolve();}
    close(){this.state='closed';return Promise.resolve();}
    createMediaStreamSource(){if(meterFails)throw Error('Meter unavailable');return {connect(){}};}
    createAnalyser(){return {fftSize:0,getByteTimeDomainData(buffer){buffer.fill(131)}};}
  }
  class MediaRecorder {
    static isTypeSupported(type){return type==='audio/webm';}
    constructor(stream,options){this.state='inactive';this.mimeType=options.mimeType;}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['recorded bytes'])});this.onstop?.();}
  }
  const window={AudioContext};
  const sandbox={window,MediaRecorder,Blob,Uint8Array,performance:{now:()=>100},console,
    navigator:{mediaDevices:{getUserMedia(c){constraints=c;
      if(denied)return Promise.reject({name:'NotAllowedError'});
      if(pending)return new Promise(resolve=>{resolvePermission=resolve});
      return Promise.resolve(stream);
    }}},
    setTimeout(fn,ms){timers.set(++id,{fn,ms});return id},clearTimeout(key){timers.delete(key)},
    requestAnimationFrame(fn){frames.set(++id,fn);return id},cancelAnimationFrame(key){frames.delete(key)},
  };
  vm.runInNewContext(fs.readFileSync('frontend/audio.js','utf8'),sandbox);
  const recorder=new window.VoiceRecorder({onStateChange:s=>states.push(s),onError:e=>errors.push(e),
    onAudioReady:b=>audio.push(b),onStatus:n=>notices.push(n)});
  return {recorder,audio,errors,states,notices,timers,track,get constraints(){return constraints},
    get resumed(){return resumed},allow(){resolvePermission(stream)}};
}
test('manual recording resumes audio context, captures chosen mic, and sends once', async()=>{
 const h=harness();await h.recorder.start({deviceId:'usb-mic'});
 assert.equal(h.constraints.audio.deviceId.exact,'usb-mic');assert.ok(h.resumed>0);
 assert.equal(h.recorder.state,'LISTENING');h.recorder.stop();
 assert.equal(h.audio.length,1);assert.equal(h.audio[0].type,'audio/webm');assert.ok(h.audio[0].size>0);
 assert.ok(h.track.stopped);assert.equal(h.timers.size,0);
});
test('recording can finish and send even if level meter fails',async()=>{
 const h=harness({meterFails:true});await h.recorder.start();h.recorder.stop();
 assert.equal(h.audio.length,1);assert.ok(h.notices.some(n=>n.includes('meter is unavailable')));
});
test('denied permission gives actionable error and releases context',async()=>{
 const h=harness({denied:true});await h.recorder.start();
 assert.equal(h.recorder.state,'IDLE');assert.match(h.errors[0],/permission is blocked/);assert.equal(h.recorder.audioContext,null);
});
test('canceling a pending permission request discards the late stream',async()=>{
 const h=harness({pending:true});const start=h.recorder.start();h.recorder.cancel();h.allow();await start;
 assert.ok(h.track.stopped);assert.equal(h.audio.length,0);assert.equal(h.recorder.state,'IDLE');
});
test('canceling recording never uploads its buffered audio',async()=>{
 const h=harness();await h.recorder.start();h.recorder.cancel();
 assert.equal(h.audio.length,0);assert.ok(h.track.stopped);assert.equal(h.timers.size,0);
});
