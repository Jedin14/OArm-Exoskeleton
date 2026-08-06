const MUTE_KEY = "lelab.recording.muted";

let ctx: AudioContext | null = null;

const getCtx = (): AudioContext => {
  if (!ctx) ctx = new AudioContext();
  return ctx;
};

export const getMuted = (): boolean => {
  return localStorage.getItem(MUTE_KEY) === "1";
};

export const setMuted = (value: boolean): void => {
  localStorage.setItem(MUTE_KEY, value ? "1" : "0");
};

const playTone = (frequency: number, durationMs: number, startOffsetMs = 0) => {
  if (getMuted()) return;
  const c = getCtx();
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.frequency.value = frequency;
  osc.type = "sine";
  gain.gain.value = 0;
  osc.connect(gain);
  gain.connect(c.destination);
  const start = c.currentTime + startOffsetMs / 1000;
  const stop = start + durationMs / 1000;
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(0.2, start + 0.01);
  gain.gain.setValueAtTime(0.2, stop - 0.02);
  gain.gain.linearRampToValueAtTime(0, stop);
  osc.start(start);
  osc.stop(stop);
};

export const playRecordingStartCue = (): void => {
  playTone(660, 80, 0);
  playTone(880, 80, 90);
};

export const playResetStartCue = (): void => {
  playTone(660, 80, 0);
  playTone(440, 80, 90);
};

export const playAutoAdvanceWarning = (): void => {
  playTone(880, 70, 0);
  playTone(880, 70, 1000);
  playTone(880, 70, 2000);
};

// --- Camera-freeze alarm -----------------------------------------------------
// A repeating buzzer for the one fault that silently ruins a recording: a
// frozen camera feed. Unlike the cues above this deliberately ignores the mute
// setting -- muting the phase chirps shouldn't also disable the fault alarm --
// and it uses a square wave so it carries across a room. It repeats until the
// operator acknowledges it or the feed recovers.

let buzzerTimer: number | null = null;

const playBuzzTone = (frequency: number, durationMs: number, startOffsetMs = 0) => {
  const c = getCtx();
  // Browsers suspend the AudioContext until a user gesture; without this the
  // alarm can be silently dropped on the first freeze of a session.
  if (c.state === "suspended") void c.resume();
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.frequency.value = frequency;
  osc.type = "square";
  gain.gain.value = 0;
  osc.connect(gain);
  gain.connect(c.destination);
  const start = c.currentTime + startOffsetMs / 1000;
  const stop = start + durationMs / 1000;
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(0.32, start + 0.005);
  gain.gain.setValueAtTime(0.32, stop - 0.01);
  gain.gain.linearRampToValueAtTime(0, stop);
  osc.start(start);
  osc.stop(stop);
};

const buzzOnce = () => {
  playBuzzTone(1180, 150, 0);
  playBuzzTone(880, 150, 200);
};

export const isFreezeBuzzerActive = (): boolean => buzzerTimer !== null;

export const startFreezeBuzzer = (): void => {
  if (buzzerTimer !== null) return; // already sounding; don't stack timers
  buzzOnce();
  buzzerTimer = window.setInterval(buzzOnce, 900);
};

export const stopFreezeBuzzer = (): void => {
  if (buzzerTimer !== null) {
    window.clearInterval(buzzerTimer);
    buzzerTimer = null;
  }
};
