import { useCallback, useEffect, useState } from "react";
import { useApi } from "@/contexts/ApiContext";

export interface AvailableCamera {
  index: number | string;
  name: string;
  deviceId: string;
  available: boolean;
  /** Named /dev symlinks pointing at this camera, e.g. ["left_camera"] */
  symlinkNames: string[];
}

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();

interface UseAvailableCamerasOptions {
  /** When false, do nothing. Use to gate on modal open. */
  enabled?: boolean;
}

/**
 * Enumerates cv2 camera indices from `/available-cameras` and merges each
 * with the matching browser deviceId (by AVFoundation localizedName) so
 * callers can render a preview alongside the bound dropdowns. Refreshes on
 * USB hotplug.
 */
export function useAvailableCameras({
  enabled = true,
}: UseAvailableCamerasOptions = {}) {
  const { baseUrl, fetchWithHeaders } = useApi();
  const [cameras, setCameras] = useState<AvailableCamera[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const browserOnlyFallback = useCallback(async (): Promise<AvailableCamera[]> => {
    const browserDevices = (await navigator.mediaDevices.enumerateDevices())
      .filter((d) => d.kind === "videoinput")
      .map((d) => ({ deviceId: d.deviceId, label: d.label }));
    const fallback = browserDevices.map((d, i) => ({
      index: i,
      name: d.label?.trim() || `Camera ${i}`,
      deviceId: d.deviceId,
      available: true,
      symlinkNames: [] as string[],
    }));
    setCameras(fallback);
    return fallback;
  }, []);

  const refresh = useCallback(async (): Promise<AvailableCamera[]> => {
    setIsLoading(true);
    try {
      try {
        const streamPromise = navigator.mediaDevices.getUserMedia({ video: true });
        try {
          const probe = await Promise.race([
            streamPromise,
            new Promise<MediaStream>((_, reject) =>
              setTimeout(() => reject(new Error("Timeout")), 2000)
            ),
          ]);
          probe.getTracks().forEach((t) => t.stop());
        } catch {
          // Even if we timeout, ensure we stop the stream if it eventually resolves
          streamPromise.then((stream) => {
            stream.getTracks().forEach((t) => t.stop());
          }).catch(() => {});
        }
      } catch {
        // ignore — we'll still try to enumerate, just without labels
      }

      const browserDevices = (await navigator.mediaDevices.enumerateDevices())
        .filter((d) => d.kind === "videoinput")
        .map((d) => ({ deviceId: d.deviceId, label: d.label }));

      const r = await Promise.race([
        fetchWithHeaders(`${baseUrl}/available-cameras`),
        new Promise<Response>((_, reject) =>
          setTimeout(() => reject(new Error("Timed out loading cameras")), 5000)
        ),
      ]);
      if (!r.ok) {
        return await browserOnlyFallback();
      }
      const data = await r.json();
      const backendCams: {
        index: number | string;
        name?: string;
        device_path?: string;
        available: boolean;
        symlink_names?: string[];
      }[] = data.cameras ?? [];

      // Browser's MediaDeviceInfo.label starts with AVFoundation's localizedName
      // but Chrome often appends "(vendorId:productId)". Match by exact, then
      // prefix, then either-contains, then by position (Linux fallback when
      // card names don't align with browser labels).
      const used = new Set<string>();
      const merged: AvailableCamera[] = backendCams.map((cam, posIdx) => {
        let label = cam.name || `Camera ${cam.index}`;
        if (cam.device_path) {
          label = `${label} (${cam.device_path})`;
        }
        const target = norm(label);
        const candidates = browserDevices.filter(
          (d) => !used.has(d.deviceId) && d.label
        );
        const match =
          candidates.find((d) => norm(d.label) === target) ||
          candidates.find((d) => norm(d.label).startsWith(target)) ||
          candidates.find(
            (d) => norm(d.label).includes(target) || target.includes(norm(d.label))
          ) ||
          // Position-based fallback: assign the Nth unused browser device to
          // the Nth backend camera. Works on Linux where label matching fails.
          browserDevices.filter((d) => !used.has(d.deviceId))[0];
        if (match) used.add(match.deviceId);
        return {
          index: cam.index,
          name: label,
          deviceId: match?.deviceId ?? "",
          available: cam.available,
          symlinkNames: cam.symlink_names ?? [],
        };
      });
      setCameras(merged);
      return merged;
    } catch {
      return await browserOnlyFallback();
    } finally {
      setIsLoading(false);
    }
  }, [baseUrl, fetchWithHeaders, browserOnlyFallback]);

  useEffect(() => {
    if (!enabled) return;
    refresh();
    const handler = () => refresh();
    navigator.mediaDevices.addEventListener("devicechange", handler);
    return () =>
      navigator.mediaDevices.removeEventListener("devicechange", handler);
  }, [enabled, refresh]);

  return { cameras, isLoading, refresh };
}
