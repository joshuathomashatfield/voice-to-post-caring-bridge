// Dependency-free tests for cancellation and queued speech; run: node --test tests/test_speech.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function harness() {
  const spoken = [], timers = new Map(), states = [], errors = [];
  let timerId = 0;
  const window = {speechSynthesis:{cancel(){},speak(u){spoken.push(u)}}};
  const sandbox = {window, SpeechSynthesisUtterance:class {constructor(text){this.text=text}},
    setTimeout:fn=>{timers.set(++timerId,fn);return timerId},clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(fs.readFileSync('frontend/speech.js','utf8'),sandbox);
  return {spoken,states,errors,window,timers,
    speech:new window.ConversationSpeech({onState:x=>states.push(x),onError:()=>errors.push(true)})};
}
test('stop cancels pending speech before it starts',()=>{
 const h=harness(); h.speech.speak('Hello. Next question?'); h.speech.stop();
 assert.equal(h.timers.size,0); assert.equal(h.spoken.length,0); assert.equal(h.states.at(-1),false);
});
test('late callbacks from interrupted speech cannot start another sentence',()=>{
 const h=harness(); h.speech.speak('First sentence. Second sentence.',{delay:0});
 const old=h.spoken[0]; h.speech.stop(); old.onend(); old.onerror({error:'interrupted'});
 assert.equal(h.timers.size,0); assert.equal(h.errors.length,0);
});
test('selected voice and rate are used and long passages are split',()=>{
 const h=harness(), voice={lang:'en-US',name:'Chosen voice'};
 h.speech.speak('Hello. How would you like to begin?',{voice,rate:0.85,delay:0});
 assert.equal(h.spoken[0].voice,voice); assert.equal(h.spoken[0].rate,0.85);
 const chunks=h.window.ConversationSpeech.chunks('word '.repeat(100));
 assert.ok(chunks.length>1); assert.ok(chunks.every(x=>x.length<=220));
});
