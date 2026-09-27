/* Browser TTS only: no new service, model download, or package. */
(() => {
  class ConversationSpeech {
    constructor({onState = () => {}, onError = () => {}} = {}) {
      this.onState = onState;
      this.onError = onError;
      this.generation = 0;
      this.timer = null;
      this.current = null;
    }
    static chunks(text) {
      // Keep normal sentence prosody; cap long passages so read-back is interruptible.
      const clean = text.replace(/\*\*|__|`/g, '').replace(/\s+/g, ' ').trim();
      const sentences = typeof Intl.Segmenter === 'function'
        ? Array.from(new Intl.Segmenter('en', {granularity:'sentence'}).segment(clean), item => item.segment)
        : (clean.match(/[^.!?]+[.!?]*|[.!?]+/g) || [clean]);
      return sentences.flatMap(sentence => {
        const words = sentence.trim().split(/\s+/);
        const chunks = [];
        let chunk = '';
        for (const word of words) {
          if (chunk && (chunk + word).length > 220) { chunks.push(chunk); chunk = ''; }
          chunk += (chunk ? ' ' : '') + word;
        }
        if (chunk) chunks.push(chunk);
        return chunks;
      }).filter(Boolean);
    }
    stop() {
      this.generation++;
      clearTimeout(this.timer);
      this.current = null;
      try { window.speechSynthesis?.cancel(); } catch { /* Speech must never block input. */ }
      this.onState(false);
    }
    speak(text, {voice = null, rate = 1, delay = 240} = {}) {
      this.stop();
      if (!text) return;
      if (!window.speechSynthesis) { this.onError(); return; }
      const generation = this.generation;
      const chunks = ConversationSpeech.chunks(text);
      this.onState(true);
      const next = () => {
        if (generation !== this.generation) return;
        const chunk = chunks.shift();
        if (!chunk) { this.current = null; this.onState(false); return; }
        const utterance = new SpeechSynthesisUtterance(chunk);
        this.current = utterance; // Retain until completion (some engines need this).
        if (voice) { utterance.voice = voice; utterance.lang = voice.lang; }
        else utterance.lang = 'en-US';
        utterance.rate = Math.max(0.7, Math.min(1.3, Number(rate) || 1));
        utterance.onend = () => {
          if (generation === this.generation) this.timer = setTimeout(next, 140);
        };
        utterance.onerror = (event) => {
          if (generation !== this.generation) return;
          this.stop();
          if (!['canceled', 'interrupted'].includes(event.error)) this.onError();
        };
        try { window.speechSynthesis.speak(utterance); }
        catch { this.stop(); this.onError(); }
      };
      if (delay) this.timer = setTimeout(next, delay);
      else next(); // Direct gesture path for browsers that gate playback.
    }
  }
  window.ConversationSpeech = ConversationSpeech;
})();
