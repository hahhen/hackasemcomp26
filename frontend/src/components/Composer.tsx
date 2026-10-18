import { useRef, useState } from "react";
import { api } from "../api";
import { audioDataUrl, recordingSupported, useAudioRecorder } from "../audio";

interface Props {
  placeholder: string;
  disabled?: boolean;
  compact?: boolean;
  onSend: (text: string, images: string[]) => Promise<unknown> | void;
}

/** Text or microphone recording; both feed the same chat flow. */
export function Composer({ placeholder, disabled, compact, onSend }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [audioError, setAudioError] = useState<string | null>(null);
  const lastAudio = useRef<Blob | null>(null);
  const sendAudio = async (blob: Blob) => {
    lastAudio.current = blob;
    setTranscribing(true); setAudioError(null);
    try {
      const { text: transcript } = await api.transcribe(await audioDataUrl(blob));
      const message = [text.trim(), transcript].filter(Boolean).join(" ");
      if (message.length > 2000) throw new Error("O texto ficou longo. Grave um áudio mais curto.");
      setText(message);
      const success = await onSend(message, []);
      lastAudio.current = null;
      if (success !== false) setText("");
    } catch (error) { setAudioError(error instanceof Error ? error.message : "Falha ao transcrever o áudio."); }
    finally { setTranscribing(false); }
  };
  const recording = useAudioRecorder((blob) => void sendAudio(blob), setAudioError);
  const blocked = disabled || transcribing || recording.opening;
  const submit = async () => {
    const clean = text.trim();
    if (!clean || sending || blocked || recording.recording) return;
    setSending(true);
    try { if (await onSend(clean, []) !== false) setText(""); }
    finally { setSending(false); }
  };
  return <>
    {audioError && <div className="audio-status warn" role="alert">{audioError} {lastAudio.current && <button type="button" onClick={() => void sendAudio(lastAudio.current!)} disabled={transcribing}>Tentar novamente</button>}</div>}
    {(transcribing || recording.recording || recording.opening) && <div className="audio-status" role="status">{transcribing ? "Transcrevendo no Gemini e enviando…" : recording.opening ? "Abrindo microfone…" : "Gravando · toque em parar para enviar."}</div>}
    <form className={`composer ${compact ? "compact" : ""}`} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <input value={text} onChange={(event) => setText(event.target.value)} placeholder={recording.recording ? "Gravando…" : placeholder} aria-label="Mensagem" maxLength={2000} disabled={blocked || recording.recording} />
      {recordingSupported && <button type="button" className={`icon ${recording.recording ? "rec" : ""}`} title={recording.recording ? "Parar e enviar áudio" : "Gravar áudio"} aria-label={recording.recording ? "Parar e enviar áudio" : "Gravar áudio"} disabled={blocked || sending} onClick={() => void recording.toggle()}>{recording.recording ? "⏹" : "🎤"}</button>}
      <button className="send" type="submit" aria-label="Enviar mensagem" disabled={blocked || recording.recording || sending || !text.trim()}>{compact ? "➤" : "Enviar"}</button>
    </form>
  </>;
}
