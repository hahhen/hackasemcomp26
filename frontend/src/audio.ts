import { useEffect, useRef, useState } from "react";

export const recordingSupported = typeof MediaRecorder !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);

export function useAudioRecorder(onAudio: (blob: Blob) => void, onError: (message: string) => void) {
  const [recording, setRecording] = useState(false);
  const [opening, setOpening] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const alive = useRef(true);
  const timer = useRef<number>(0);
  const handlers = useRef({ onAudio, onError });
  handlers.current = { onAudio, onError };

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      clearTimeout(timer.current);
      if (recorder.current?.state === "recording") recorder.current.stop();
      recorder.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);

  const toggle = async () => {
    if (recorder.current?.state === "recording") { recorder.current.stop(); return; }
    if (opening) return;
    setOpening(true);
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!alive.current) { stream.getTracks().forEach((track) => track.stop()); return; }
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find((type) => MediaRecorder.isTypeSupported(type));
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorder.current = rec;
      const chunks: BlobPart[] = [];
      rec.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      rec.onstop = () => {
        clearTimeout(timer.current);
        rec.stream.getTracks().forEach((track) => track.stop());
        if (!alive.current) return;
        setRecording(false);
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        if (blob.size) handlers.current.onAudio(blob);
        else handlers.current.onError("A gravação ficou vazia. Tente novamente.");
      };
      rec.start();
      setRecording(true);
      timer.current = window.setTimeout(() => { if (rec.state === "recording") rec.stop(); }, 120_000);
    } catch {
      stream?.getTracks().forEach((track) => track.stop());
      handlers.current.onError("Não consegui acessar o microfone. Verifique a permissão ou anexe um áudio.");
    } finally { if (alive.current) setOpening(false); }
  };
  return { recording, opening, toggle };
}

export function audioDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Não consegui ler o áudio."));
    reader.readAsDataURL(blob);
  });
}
