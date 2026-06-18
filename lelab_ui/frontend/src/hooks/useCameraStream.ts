import { useEffect, useRef, useState } from "react";

/**
 * Attach a live browser camera stream to a `<video>` element by deviceId.
 * Set `paused=true` to release the stream (e.g. so cv2.VideoCapture can claim
 * the camera exclusively). The stream is auto-stopped on unmount.
 *
 * The active stream is held in a ref so that when `paused` flips to true the
 * tracks are stopped **immediately** (synchronously, before the next React
 * render/effect cycle), which is required to avoid a race condition where the
 * backend tries to open the same device before the browser releases it.
 */
export function useCameraStream(deviceId: string, paused: boolean) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [hasError, setHasError] = useState(false);
  // Hold the live stream in a ref so we can stop it synchronously when needed.
  const streamRef = useRef<MediaStream | null>(null);

  // Immediately stop tracks when paused flips to true — don't wait for the
  // async effect cleanup which only runs after the next render.
  if (paused && streamRef.current) {
    streamRef.current.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }

  useEffect(() => {
    if (paused || !deviceId) {
      if (!deviceId) setHasError(true);
      return;
    }
    let cancelled = false;
    setHasError(false);

    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: deviceId } },
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => {});
        }
      } catch {
        setHasError(true);
      }
    })();

    return () => {
      cancelled = true;
      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
    };
  }, [deviceId, paused]);

  return { videoRef, hasError };
}
