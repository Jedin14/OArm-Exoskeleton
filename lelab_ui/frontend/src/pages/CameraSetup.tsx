import React, { useState, useEffect, useRef, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { useApi } from "@/contexts/ApiContext";
import { useAvailableCameras } from "@/hooks/useAvailableCameras";
import {
  Camera,
  Wifi,
  WifiOff,
  Play,
  Square,
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  VideoOff,
  Plug,
} from "lucide-react";

const CAMERA_SLOT_NAMES = ["main_camera", "right_camera", "left_camera"] as const;
type CameraSlotName = typeof CAMERA_SLOT_NAMES[number];

interface RosCameraMapping {
  name: CameraSlotName;
  device_index: number | string;
  width: number;
  height: number;
  fps: number;
  /** Live device info from the backend (absent on older responses). */
  resolved_path?: string;
  device_present?: boolean;
}

interface CameraStatus {
  fps: number;
  ok: boolean;
}

const SLOT_LABELS: Record<CameraSlotName, string> = {
  main_camera: "Main Camera",
  right_camera: "Right Camera",
  left_camera: "Left Camera",
};

const CameraSetup: React.FC<{ isModal?: boolean; onClose?: () => void }> = ({ isModal = false, onClose }) => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();
  const { cameras: availableCameras, isLoading: loadingCameras } = useAvailableCameras();

  const [mappings, setMappings] = useState<RosCameraMapping[]>([]);
  const [cameraStatus, setCameraStatus] = useState<Record<string, CameraStatus>>({});
  const [bridgeRunning, setBridgeRunning] = useState(false);
  const [bridgePid, setBridgePid] = useState<number | null>(null);
  const [bridgeCrash, setBridgeCrash] = useState<string[] | null>(null);
  // Which refresh() sources failed on the last poll, so the page can say it is
  // showing stale data instead of quietly presenting it as current.
  const [staleSources, setStaleSources] = useState<string[]>([]);
  const [bridgeLoading, setBridgeLoading] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  // The persisted I/O Configuration preference. This ALONE decides how the page
  // presents itself, so the wording and controls stay put regardless of whether
  // a bridge process happens to be alive at this instant.
  const [ioRosCameraEnabled, setIoRosCameraEnabled] = useState(false);

  // Which capture path this page is describing.
  const rosCameraMode = ioRosCameraEnabled;
  // A bridge is alive even though ROS camera mode is off. It holds the V4L2
  // devices exclusively, so direct capture cannot open them — worth flagging
  // loudly and offering a stop, but NOT worth reskinning the page over.
  const strayBridge = bridgeRunning && !ioRosCameraEnabled;

  // Attach form state
  const [selectedDeviceIndex, setSelectedDeviceIndex] = useState<string>("");
  const [selectedSlot, setSelectedSlot] = useState<string>("");
  const [attaching, setAttaching] = useState(false);

  // Browser preview stream
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [previewDeviceId, setPreviewDeviceId] = useState<string | null>(null);

  // Load mappings + bridge status on mount.
  //
  // Each source is applied INDEPENDENTLY (allSettled, not all). With Promise.all
  // a single blip on any one endpoint threw before any setState ran, so all six
  // values silently kept their previous poll's contents — including right after
  // a start/stop toggle, which left the header contradicting the toast it had
  // just shown. A failure now only leaves that one source stale, and surfaces
  // instead of vanishing.
  const refresh = useCallback(async () => {
    const readJson = async (path: string) => {
      const res = await fetchWithHeaders(`${baseUrl}${path}`);
      if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
      return res.json();
    };
    const [mapRes, bridgeRes, ioRes] = await Promise.allSettled([
      readJson("/ros-camera-mappings"),
      readJson("/ros-camera-bridge/status"),
      readJson("/io-config"),
    ]);

    if (mapRes.status === "fulfilled" && Array.isArray(mapRes.value.mappings)) {
      setMappings(mapRes.value.mappings);
    }

    const bData = bridgeRes.status === "fulfilled" ? bridgeRes.value : null;
    if (bData) {
      setBridgeRunning(!!bData.running);
      setBridgePid(bData.pid ?? null);
      // The bridge can die on its own between polls -- a stale PID badge
      // is exactly what looked like a running bridge in a blank-preview
      // recording. died_unexpectedly + log_tail come straight from the
      // backend's log file, which survives the crash even though pgrep no
      // longer finds the process.
      setBridgeCrash(bData.died_unexpectedly ? (bData.log_tail || []) : null);
    }

    if (ioRes.status === "fulfilled") {
      // Presentation follows the PERSISTED preference only — never live process
      // detection. Deriving it from "is a bridge running" made the whole page
      // reskin itself between ROS and direct wording every time the bridge died
      // or a teleop restart relaunched it, which read as the page flickering
      // between two different designs. A stray bridge is now reported as an
      // anomaly banner (see strayBridge) instead of silently redefining the mode.
      setIoRosCameraEnabled(!!ioRes.value.ros_camera);
    }

    const failed = [
      mapRes.status === "rejected" && "camera mappings",
      bridgeRes.status === "rejected" && "bridge status",
      ioRes.status === "rejected" && "I/O config",
    ].filter(Boolean) as string[];
    if (failed.length > 0) {
      setStaleSources(failed);
    } else {
      setStaleSources([]);
    }
    // Deliberately NOT depending on bridgeRunning: it is read through a ref
    // above. As a dependency it would give refresh a new identity on every
    // bridge state change, re-firing the mount effect that calls refresh().
  }, [baseUrl, fetchWithHeaders]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll camera status every 1.5s when bridge is running
  useEffect(() => {
    if (!bridgeRunning) {
      setCameraStatus({});
      return;
    }
    const poll = async () => {
      try {
        const res = await fetchWithHeaders(`${baseUrl}/ros-camera-status`);
        const data = await res.json();
        setCameraStatus(data.status || {});
      } catch {
        // ignore
      }
    };
    poll();
    const id = setInterval(poll, 1500);
    return () => clearInterval(id);
  }, [bridgeRunning, baseUrl, fetchWithHeaders]);

  // Camera browser preview
  useEffect(() => {
    const startPreview = async () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      if (!previewDeviceId) return;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: previewDeviceId }, width: 640, height: 480 },
        });
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }
      } catch {
        // ignore preview failure
      }
    };
    startPreview();
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
    };
  }, [previewDeviceId]);

  // When device selection changes, start preview
  const handleDeviceSelect = (indexStr: string) => {
    setSelectedDeviceIndex(indexStr);
    const cam = availableCameras.find((c) => c.index.toString() === indexStr);
    setPreviewDeviceId(cam?.deviceId || null);
  };

  const handleAttach = async () => {
    if (!selectedDeviceIndex || !selectedSlot) return;
    setAttaching(true);
    try {
      const res = await fetchWithHeaders(`${baseUrl}/ros-camera-mappings`, {
        method: "POST",
        body: JSON.stringify({
          name: selectedSlot,
          device_index: selectedDeviceIndex.includes("/") ? selectedDeviceIndex : parseInt(selectedDeviceIndex),
          width: 640,
          height: 480,
          fps: 30,
        }),
      });
      const data = await res.json();
      if (data.success) {
        setMappings(data.mappings || []);
        const attachedSlot = selectedSlot as CameraSlotName;
        const attachedDevice = selectedDeviceIndex;
        setSelectedDeviceIndex("");
        setSelectedSlot("");
        setPreviewDeviceId(null);

        // Push the new mapping into a session that is recording RIGHT NOW.
        // Writing the file alone only affects the NEXT session, which is why
        // re-attaching a camera that dropped mid-dataset appeared to do nothing.
        // reconnect_cameras() re-reads this file, so this is what makes an
        // in-flight recording pick the device up — and it also clears the frozen
        // flag that keeps the recorder paused. Reports the real per-camera
        // outcome instead of assuming it worked.
        let liveNote = "";
        try {
          const rc = await fetchWithHeaders(`${baseUrl}/reconnect-cameras`, { method: "POST" });
          const rcData = await rc.json();
          // "Recording not active" is the normal idle case: nothing to apply.
          if (rcData.cameras && Object.keys(rcData.cameras).length > 0) {
            liveNote = rcData.success
              ? " — applied to the running recording"
              : ` — but the live session still reports: ${rcData.message}`;
          }
        } catch {
          // Non-fatal: the mapping is saved regardless.
        }

        toast({
          title: "Camera attached",
          description: `${SLOT_LABELS[attachedSlot]} mapped to device ${attachedDevice}${liveNote}`,
          variant: liveNote.includes("still reports") ? "destructive" : undefined,
        });
      } else {
        toast({ title: "Failed to attach", variant: "destructive" });
      }
    } catch {
      toast({ title: "Error attaching camera", variant: "destructive" });
    } finally {
      setAttaching(false);
    }
  };

  // Re-open the already-attached devices, without touching the mappings.
  //
  // This is the "the plug went loose mid-episode and the buzzer is going" path:
  // the camera is still in the same physical port, so nothing needs re-attaching
  // — the reader just has to open it again. Doing that previously required going
  // through Attach Camera (re-selecting device + slot) purely as a side effect of
  // the attach handler calling this same endpoint, which is a lot of clicking
  // while a recording is paused and waiting.
  const handleReconnect = async () => {
    setReconnecting(true);
    try {
      const res = await fetchWithHeaders(`${baseUrl}/reconnect-cameras`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      await refresh();

      if (data.success) {
        toast({
          title: "Cameras reconnected",
          description: data.message || "Frames are flowing again.",
        });
        return;
      }
      // "Recording not active" is not a failure worth alarming about: outside a
      // session there is no live robot holding the devices, so there is nothing
      // to reconnect and the next recording will open them fresh anyway.
      const idle = typeof data.message === "string" && data.message.includes("not active");
      toast({
        title: idle ? "No active recording" : "Reconnect did not recover every camera",
        description: idle
          ? "Nothing to reconnect — the next recording will open the cameras fresh."
          : data.message || data.detail || "See the lelab log for details.",
        variant: idle ? undefined : "destructive",
      });
    } catch {
      toast({ title: "Could not reach the backend", variant: "destructive" });
    } finally {
      setReconnecting(false);
    }
  };

  const handleDetach = async (name: string) => {
    try {
      const res = await fetchWithHeaders(`${baseUrl}/ros-camera-mappings/${name}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      // Check the response before trusting its body. `data.mappings || []` on an
      // error payload (which has `detail`, no `mappings`) wiped the whole camera
      // list in the UI while the backend still had every mapping — and reported
      // success for a delete that never happened.
      if (!res.ok || !Array.isArray(data.mappings)) {
        toast({
          title: "Could not detach camera",
          description: data.detail || data.message || "The camera list was left unchanged.",
          variant: "destructive",
        });
        await refresh();  // resync from the backend rather than guessing
        return;
      }
      setMappings(data.mappings);
      toast({ title: "Camera detached", description: `${SLOT_LABELS[name as CameraSlotName]} removed` });
    } catch {
      toast({ title: "Error detaching camera", variant: "destructive" });
    }
  };

  const handleBridgeToggle = async () => {
    const wasRunning = bridgeRunning;
    setBridgeLoading(true);
    try {
      const endpoint = wasRunning ? "/ros-camera-bridge/stop" : "/ros-camera-bridge/start";
      const res = await fetchWithHeaders(`${baseUrl}${endpoint}`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      await refresh();
      // The backend now verifies the process actually started/stopped and
      // returns 4xx/5xx with a reason when it did not. Reporting success
      // regardless — which is what this did before — is why "Start Bridge"
      // looked like it worked while the bridge had already exited.
      if (!res.ok) {
        toast({
          title: wasRunning ? "Could not stop the bridge" : "Bridge failed to start",
          description: data.detail || data.message || "See the lelab log for details.",
          variant: "destructive",
        });
        return;
      }
      toast({
        title: wasRunning ? "Bridge stopped" : "Bridge started",
        description: data.message || (wasRunning ? "Cameras released" : `Started (PID ${data.pid})`),
      });
    } catch {
      toast({ title: "Bridge control error", variant: "destructive" });
    } finally {
      setBridgeLoading(false);
    }
  };

  const usedSlots = mappings.map((m) => m.name);
  const allGreen = bridgeRunning && mappings.length > 0 && mappings.every((m) => cameraStatus[m.name]?.ok);
  const anyIssue = bridgeRunning && mappings.some((m) => !cameraStatus[m.name]?.ok);
  // Direct mode: nothing streams until a recording starts, so readiness is
  // "every attached device still exists".
  const missingDevices = mappings.filter((m) => m.device_present === false);
  const directReady = mappings.length > 0 && missingDevices.length === 0;

  return (
    <div className={isModal ? "bg-black text-white" : "min-h-screen bg-black text-white"}>
      {/* Page header */}
      <div className={isModal ? "bg-black/95 border-b border-gray-800 px-6 py-4" : "sticky z-10 top-0 bg-black/95 backdrop-blur border-b border-gray-800 px-6 py-4"}>
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            {!isModal && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => window.location.href = "/"}
                className="text-gray-400 hover:text-white"
              >
                ← Back
              </Button>
            )}
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
                <Camera className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-white">Camera Setup</h1>
                <p className="text-xs text-gray-400">
                  {rosCameraMode
                    ? "Attach cameras to ROS topics for perfectly-synced recording"
                    : "Attach cameras — recording reads them directly, exactly as deployment does"}
                </p>
              </div>
            </div>
          </div>

          {/* Bridge control (ROS mode only — direct capture has no bridge) */}
          <div className="flex items-center gap-3">
            {!rosCameraMode && (
              <span className="text-xs px-2.5 py-1 rounded-full bg-cyan-950/60 text-cyan-300 border border-cyan-900/60 font-medium">
                Direct capture
              </span>
            )}
            {rosCameraMode && bridgeRunning && bridgePid && (
              <span className="text-xs text-gray-500 font-mono">PID {bridgePid}</span>
            )}
            {/* Only present when ROS camera mode is actually the chosen path.
                With it off there is nothing to start — recording opens the V4L2
                devices itself — so a bridge button here would be dead UI that
                only invites a wrong click. A bridge that IS somehow running
                while the mode is off gets its own stop button in the stray-bridge
                banner below, so it stays stoppable without this control having to
                appear and disappear as processes come and go. */}
            {rosCameraMode && (
              <Button
                onClick={handleBridgeToggle}
                disabled={bridgeLoading || (!bridgeRunning && mappings.length === 0)}
                className={`flex items-center gap-2 px-5 py-2 text-sm font-semibold transition-all ${
                  bridgeRunning
                    ? "bg-red-600/80 hover:bg-red-600 text-white border border-red-500/50"
                    : "bg-cyan-600 hover:bg-cyan-500 text-white border border-cyan-400/30 shadow-md shadow-cyan-500/20"
                }`}
              >
                {bridgeLoading ? (
                  <RefreshCw className="w-4 h-4 animate-spin" />
                ) : bridgeRunning ? (
                  <Square className="w-4 h-4" />
                ) : (
                  <Play className="w-4 h-4" />
                )}
                {bridgeRunning ? "Stop Bridge" : "Start Bridge"}
              </Button>
            )}
          </div>
        </div>
        {!ioRosCameraEnabled && (
          <div className="max-w-7xl mx-auto px-0 pt-2 text-xs text-gray-500">
            ROS camera mode is off — recording will read cameras directly. To use
            the bridge instead, enable it on the{" "}
            <a href="/io-config" className="text-cyan-400 hover:underline">
              I/O Configuration
            </a>{" "}
            page.
          </div>
        )}
      </div>

      {/* The bridge died on its own since the last time someone explicitly
          started or stopped it. Surfaced until acknowledged (Stop, or a fresh
          Start) so a stale "running" impression can't persist across a poll
          gap the way it did when this was only visible via a log file nobody
          checked mid-recording. */}
      {/* A bridge is running while ROS camera mode is off. Reported as an
          anomaly to resolve — not by silently switching the page into ROS mode,
          which is what made this page appear to flip between two designs. */}
      {strayBridge && (
        <div className="px-6 py-3 border-b bg-amber-950/40 border-amber-800/60">
          <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
            <div className="flex items-center gap-2 text-sm text-amber-300">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>
                A ROS camera bridge is running{bridgePid ? ` (PID ${bridgePid})` : ""} even
                though ROS camera mode is off. It holds the cameras exclusively, so
                direct capture cannot open them — stop it before recording.
              </span>
            </div>
            <Button
              onClick={handleBridgeToggle}
              disabled={bridgeLoading}
              size="sm"
              className="shrink-0 bg-red-600/80 hover:bg-red-600 text-white border border-red-500/50"
            >
              {bridgeLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4 mr-1" />}
              Stop Bridge
            </Button>
          </div>
        </div>
      )}

      {staleSources.length > 0 && (
        <div className="px-6 py-2 border-b bg-amber-950/30 border-amber-900/50">
          <div className="max-w-7xl mx-auto flex items-center gap-2 text-xs text-amber-300">
            <AlertTriangle className="w-3.5 h-3.5" />
            Could not refresh {staleSources.join(", ")} — showing the last known
            value. Check that the backend is reachable.
          </div>
        </div>
      )}

      {bridgeCrash && (
        <div className="px-6 py-3 border-b bg-red-950/40 border-red-800/60">
          <div className="max-w-7xl mx-auto">
            <div className="flex items-center gap-2 text-sm text-red-300 font-medium">
              <AlertTriangle className="w-4 h-4" />
              Camera bridge exited unexpectedly — cameras stopped publishing mid-session.
            </div>
            {bridgeCrash.length > 0 && (
              <pre className="mt-2 text-xs text-red-200/70 font-mono whitespace-pre-wrap overflow-x-auto">
                {bridgeCrash.join("\n")}
              </pre>
            )}
          </div>
        </div>
      )}

      {/* Direct-mode status banner */}
      {!rosCameraMode && (
        <div className={`px-6 py-3 border-b ${
          directReady
            ? "bg-emerald-950/40 border-emerald-800/50"
            : missingDevices.length > 0
            ? "bg-amber-950/40 border-amber-800/50"
            : "bg-gray-900/60 border-gray-800"
        }`}>
          <div className="max-w-7xl mx-auto flex items-center gap-2">
            {directReady ? (
              <><CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span className="text-sm text-emerald-300 font-medium">
                {mappings.length} camera{mappings.length > 1 ? "s" : ""} attached and present — ready to record
              </span></>
            ) : missingDevices.length > 0 ? (
              <><AlertTriangle className="w-4 h-4 text-amber-400" />
              <span className="text-sm text-amber-300 font-medium">
                Device missing for {missingDevices.map((m) => SLOT_LABELS[m.name]).join(", ")} — re-attach it below
              </span></>
            ) : (
              <><Camera className="w-4 h-4 text-gray-400" />
              <span className="text-sm text-gray-400">No cameras attached — recording will capture state and action only</span></>
            )}
          </div>
        </div>
      )}

      {/* Status banner */}
      {rosCameraMode && bridgeRunning && (
        <div className={`px-6 py-3 border-b ${
          allGreen
            ? "bg-emerald-950/40 border-emerald-800/50"
            : anyIssue
            ? "bg-amber-950/40 border-amber-800/50"
            : "bg-gray-900/60 border-gray-800"
        }`}>
          <div className="max-w-7xl mx-auto flex items-center gap-2">
            {allGreen ? (
              <><CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span className="text-sm text-emerald-300 font-medium">
                All cameras running at 30fps — Ready to record
              </span></>
            ) : anyIssue ? (
              <><AlertTriangle className="w-4 h-4 text-amber-400" />
              <span className="text-sm text-amber-300 font-medium">
                Camera issue detected — check connections
              </span></>
            ) : (
              <><RefreshCw className="w-4 h-4 text-gray-400 animate-spin" />
              <span className="text-sm text-gray-400">Waiting for camera frames...</span></>
            )}
          </div>
        </div>
      )}

      <div className="max-w-7xl mx-auto px-6 py-8 grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Left: Attach camera form */}
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-semibold text-white mb-1">Attach Camera</h2>
            <p className="text-sm text-gray-400">
              {rosCameraMode
                ? "Select a USB camera and assign it to a ROS topic slot."
                : "Select a USB camera and assign it to a recording slot. The device is pinned by USB port, so it survives a replug."}
            </p>
          </div>

          <div className="bg-gray-900/60 border border-gray-800 rounded-2xl p-6 space-y-5">
            <div className="space-y-2">
              <Label className="text-sm font-medium text-gray-300">USB Camera Device</Label>
              <Select
                value={selectedDeviceIndex}
                onValueChange={handleDeviceSelect}
                disabled={loadingCameras}
              >
                <SelectTrigger className="bg-gray-800 border-gray-700 text-white h-10">
                  <SelectValue placeholder={loadingCameras ? "Detecting cameras..." : "Select camera"} />
                </SelectTrigger>
                <SelectContent className="bg-gray-800 border-gray-700">
                  {availableCameras.map((cam) => (
                    <SelectItem key={cam.index} value={cam.index.toString()} className="text-white hover:bg-gray-700">
                      <div className="flex flex-col">
                        <span className="font-medium">{cam.name}</span>
                        <span className="text-xs text-gray-400">
                          {cam.index.toString().startsWith('/') ? cam.index.toString() : `/dev/video${cam.index}`}
                        </span>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label className="text-sm font-medium text-gray-300">
                {rosCameraMode ? "Assign as ROS Topic" : "Assign to Recording Slot"}
              </Label>
              <Select value={selectedSlot} onValueChange={setSelectedSlot}>
                <SelectTrigger className="bg-gray-800 border-gray-700 text-white h-10">
                  <SelectValue placeholder="Select slot" />
                </SelectTrigger>
                <SelectContent className="bg-gray-800 border-gray-700">
                  {CAMERA_SLOT_NAMES.map((slot) => {
                    const taken = usedSlots.includes(slot);
                    return (
                      <SelectItem
                        key={slot}
                        value={slot}
                        disabled={taken && slot !== selectedSlot}
                        className="text-white hover:bg-gray-700"
                      >
                        <div className="flex flex-col">
                          <span className="font-medium">{SLOT_LABELS[slot]}</span>
                          <span className="text-xs text-gray-400 font-mono">
                            {rosCameraMode
                              ? `/camera/${slot}/image_raw/compressed`
                              : `observation.images.${slot}`}
                          </span>
                          {taken && <span className="text-xs text-amber-400">Already attached (will replace)</span>}
                        </div>
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
            </div>

            <Button
              onClick={handleAttach}
              disabled={!selectedDeviceIndex || !selectedSlot || attaching}
              className="w-full bg-cyan-600 hover:bg-cyan-500 text-white font-semibold py-2.5 shadow-md shadow-cyan-500/20 transition-all"
            >
              {attaching ? <RefreshCw className="w-4 h-4 animate-spin mr-2" /> : <Camera className="w-4 h-4 mr-2" />}
              Attach Camera
            </Button>
          </div>

          {/* Browser preview */}
          <div className="bg-gray-900/60 border border-gray-800 rounded-2xl overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-800">
              <h3 className="text-sm font-semibold text-gray-300">Live Preview</h3>
            </div>
            <div className="aspect-video bg-gray-950 relative">
              {previewDeviceId ? (
                <video
                  ref={videoRef}
                  autoPlay
                  muted
                  playsInline
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="w-full h-full flex flex-col items-center justify-center gap-2">
                  <VideoOff className="w-10 h-10 text-gray-600" />
                  <span className="text-sm text-gray-500">Select a camera to preview</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right: Active ROS cameras */}
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white mb-1">
                {rosCameraMode ? "ROS Cameras" : "Attached Cameras"}
              </h2>
              <p className="text-sm text-gray-400">
                {mappings.length === 0
                  ? "No cameras attached yet."
                  : `${mappings.length} camera${mappings.length > 1 ? "s" : ""} attached`}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {/* Recovery for a camera that dropped while still plugged into the
                  same port: re-opens the devices from the existing mappings, so
                  there is nothing to re-select. Deliberately prominent — this is
                  reached mid-episode with the freeze buzzer going. */}
              {mappings.length > 0 && (
                <Button
                  onClick={handleReconnect}
                  disabled={reconnecting}
                  className="bg-amber-600 hover:bg-amber-500 text-white font-semibold"
                >
                  {reconnecting ? (
                    <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <Plug className="w-4 h-4 mr-2" />
                  )}
                  Reconnect Cameras
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={refresh}
                title="Refresh this page's data"
                className="border-gray-700 text-gray-400 hover:text-white hover:border-gray-500 hover:bg-gray-800"
              >
                <RefreshCw className="w-4 h-4" />
              </Button>
            </div>
          </div>

          {mappings.length === 0 ? (
            <div className="bg-gray-900/40 border border-gray-800 border-dashed rounded-2xl p-12 text-center">
              <Camera className="w-12 h-12 text-gray-700 mx-auto mb-3" />
              <p className="text-gray-500 text-sm">No cameras attached.</p>
              <p className="text-gray-600 text-xs mt-1">Use the form on the left to attach a camera.</p>
            </div>
          ) : (
            <div className="space-y-4">
              {mappings.map((mapping) => {
                const status = cameraStatus[mapping.name];
                // ROS mode grades on live topic FPS; direct mode has no stream
                // outside a recording, so it grades on device presence.
                const isGreen = rosCameraMode
                  ? bridgeRunning && !!status?.ok
                  : mapping.device_present !== false;
                const isAmber = rosCameraMode
                  ? bridgeRunning && !!status && !status.ok
                  : mapping.device_present === false;

                return (
                  <div
                    key={mapping.name}
                    className={`bg-gray-900/60 border rounded-2xl p-5 space-y-4 transition-all ${
                      isGreen
                        ? "border-emerald-800/60 shadow-sm shadow-emerald-500/10"
                        : isAmber
                        ? "border-amber-800/60"
                        : "border-gray-800"
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          {/* Status dot */}
                          <div
                            className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                              isGreen
                                ? "bg-emerald-400 shadow-sm shadow-emerald-400/50 animate-pulse"
                                : isAmber
                                ? "bg-amber-400"
                                : "bg-gray-600"
                            }`}
                          />
                          <span className="font-semibold text-white text-base">
                            {SLOT_LABELS[mapping.name]}
                          </span>
                          {bridgeRunning && status && (
                            <span
                              className={`text-xs px-2 py-0.5 rounded-full font-mono font-bold ${
                                status.ok
                                  ? "bg-emerald-900/60 text-emerald-300"
                                  : "bg-amber-900/60 text-amber-300"
                              }`}
                            >
                              {status.fps.toFixed(1)} fps
                            </span>
                          )}
                        </div>
                        <p className="text-xs font-mono text-gray-500">
                          {rosCameraMode
                            ? `/camera/${mapping.name}/image_raw/compressed`
                            : `observation.images.${mapping.name}`}
                        </p>
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleDetach(mapping.name)}
                        className="text-gray-500 hover:text-red-400 hover:bg-red-900/20"
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>

                    <div className="grid grid-cols-3 gap-3 text-xs">
                      <div className="bg-gray-800/60 rounded-lg px-3 py-2 overflow-hidden">
                        <div className="text-gray-500 mb-0.5">Device</div>
                        <div className="text-gray-200 font-mono text-xs truncate" title={mapping.device_index.toString().startsWith('/') ? mapping.device_index.toString() : `/dev/video${mapping.device_index}`}>
                          {mapping.device_index.toString().startsWith('/') ? mapping.device_index.toString() : `/dev/video${mapping.device_index}`}
                        </div>
                      </div>
                      <div className="bg-gray-800/60 rounded-lg px-3 py-2">
                        <div className="text-gray-500 mb-0.5">Resolution</div>
                        <div className="text-gray-200">{mapping.width}×{mapping.height}</div>
                      </div>
                      <div className="bg-gray-800/60 rounded-lg px-3 py-2">
                        <div className="text-gray-500 mb-0.5">Target FPS</div>
                        <div className="text-gray-200">{mapping.fps}</div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 text-xs">
                      {isGreen ? (
                        <><Wifi className="w-3.5 h-3.5 text-emerald-400" />
                        <span className="text-emerald-400">
                          {rosCameraMode
                            ? "Publishing to ROS — synced with joint states"
                            : "Device present — opened directly when recording starts"}
                        </span></>
                      ) : isAmber ? (
                        <><WifiOff className="w-3.5 h-3.5 text-amber-400" />
                        <span className="text-amber-400">
                          {rosCameraMode ? "Low FPS — check USB connection" : "Device not found — check USB connection"}
                        </span></>
                      ) : (
                        <><WifiOff className="w-3.5 h-3.5 text-gray-600" />
                        <span className="text-gray-600">Bridge not running</span></>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Info box */}
          <div className="bg-blue-950/30 border border-blue-900/40 rounded-xl p-4 space-y-1.5">
            <h4 className="text-xs font-semibold text-blue-300 uppercase tracking-wide">How it works</h4>
            {rosCameraMode ? (
              <ul className="text-xs text-gray-400 space-y-1">
                <li>• Camera frames are published to ROS 2 with hardware timestamps</li>
                <li>• The UDP bridge pairs frames with joint states using the same clock</li>
                <li>• Result: video and action timestamps within &lt;1ms — zero dropped frames</li>
                <li>• Start the bridge before starting a recording session</li>
              </ul>
            ) : (
              <ul className="text-xs text-gray-400 space-y-1">
                <li>• Recording opens these devices itself (MJPG 640×480@30, buffer 1)</li>
                <li>• No JPEG round trip — identical to how the deployed policy reads cameras</li>
                <li>• Each frame is paired with the nearest CAN joint sample, not the newest</li>
                <li>• Nothing to start here; just start a recording session</li>
                <li>• Need ROS topics (e.g. for RViz)? Enable them on the I/O Configuration page</li>
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default CameraSetup;
