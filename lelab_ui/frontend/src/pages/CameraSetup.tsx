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
} from "lucide-react";

const CAMERA_SLOT_NAMES = ["main_camera", "right_camera", "left_camera"] as const;
type CameraSlotName = typeof CAMERA_SLOT_NAMES[number];

interface RosCameraMapping {
  name: CameraSlotName;
  device_index: number | string;
  width: number;
  height: number;
  fps: number;
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
  const [bridgeLoading, setBridgeLoading] = useState(false);

  // Attach form state
  const [selectedDeviceIndex, setSelectedDeviceIndex] = useState<string>("");
  const [selectedSlot, setSelectedSlot] = useState<string>("");
  const [attaching, setAttaching] = useState(false);

  // Browser preview stream
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [previewDeviceId, setPreviewDeviceId] = useState<string | null>(null);

  // Load mappings + bridge status on mount
  const refresh = useCallback(async () => {
    try {
      const [mRes, bRes] = await Promise.all([
        fetchWithHeaders(`${baseUrl}/ros-camera-mappings`),
        fetchWithHeaders(`${baseUrl}/ros-camera-bridge/status`),
      ]);
      const mData = await mRes.json();
      const bData = await bRes.json();
      setMappings(mData.mappings || []);
      setBridgeRunning(bData.running || false);
      setBridgePid(bData.pid || null);
    } catch {
      // ignore transient errors
    }
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
        setSelectedDeviceIndex("");
        setSelectedSlot("");
        setPreviewDeviceId(null);
        toast({ title: "Camera attached", description: `${SLOT_LABELS[selectedSlot as CameraSlotName]} mapped to device ${selectedDeviceIndex}` });
      } else {
        toast({ title: "Failed to attach", variant: "destructive" });
      }
    } catch {
      toast({ title: "Error attaching camera", variant: "destructive" });
    } finally {
      setAttaching(false);
    }
  };

  const handleDetach = async (name: string) => {
    try {
      const res = await fetchWithHeaders(`${baseUrl}/ros-camera-mappings/${name}`, { method: "DELETE" });
      const data = await res.json();
      setMappings(data.mappings || []);
      toast({ title: "Camera detached", description: `${SLOT_LABELS[name as CameraSlotName]} removed` });
    } catch {
      toast({ title: "Error detaching camera", variant: "destructive" });
    }
  };

  const handleBridgeToggle = async () => {
    setBridgeLoading(true);
    try {
      const endpoint = bridgeRunning ? "/ros-camera-bridge/stop" : "/ros-camera-bridge/start";
      const res = await fetchWithHeaders(`${baseUrl}${endpoint}`, { method: "POST" });
      const data = await res.json();
      await refresh();
      toast({
        title: bridgeRunning ? "Bridge stopped" : "Bridge started",
        description: data.message || (bridgeRunning ? "Camera bridge has been stopped" : `Started (PID ${data.pid})`),
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
                <p className="text-xs text-gray-400">Attach cameras to ROS topics for perfectly-synced recording</p>
              </div>
            </div>
          </div>

          {/* Bridge control */}
          <div className="flex items-center gap-3">
            {bridgeRunning && bridgePid && (
              <span className="text-xs text-gray-500 font-mono">PID {bridgePid}</span>
            )}
            <Button
              onClick={handleBridgeToggle}
              disabled={bridgeLoading || mappings.length === 0}
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
          </div>
        </div>
      </div>

      {/* Status banner */}
      {bridgeRunning && (
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
              Select a USB camera and assign it to a ROS topic slot.
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
              <Label className="text-sm font-medium text-gray-300">Assign as ROS Topic</Label>
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
                            /camera/{slot}/image_raw/compressed
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
              <h2 className="text-lg font-semibold text-white mb-1">ROS Cameras</h2>
              <p className="text-sm text-gray-400">
                {mappings.length === 0
                  ? "No cameras attached yet."
                  : `${mappings.length} camera${mappings.length > 1 ? "s" : ""} attached`}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={refresh}
              className="border-gray-700 text-gray-400 hover:text-white hover:border-gray-500 hover:bg-gray-800"
            >
              <RefreshCw className="w-4 h-4" />
            </Button>
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
                const isGreen = bridgeRunning && status?.ok;
                const isAmber = bridgeRunning && status && !status.ok;
                const isGray = !bridgeRunning;

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
                          /camera/{mapping.name}/image_raw/compressed
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
                        <span className="text-emerald-400">Publishing to ROS — synced with joint states</span></>
                      ) : isAmber ? (
                        <><WifiOff className="w-3.5 h-3.5 text-amber-400" />
                        <span className="text-amber-400">Low FPS — check USB connection</span></>
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
            <ul className="text-xs text-gray-400 space-y-1">
              <li>• Camera frames are published to ROS 2 with hardware timestamps</li>
              <li>• The UDP bridge pairs frames with joint states using the same clock</li>
              <li>• Result: video and action timestamps within &lt;1ms — zero dropped frames</li>
              <li>• Start the bridge before starting a recording session</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CameraSetup;
