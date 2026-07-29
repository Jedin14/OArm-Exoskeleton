import React, { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "@/contexts/ApiContext";
import { CameraFeed } from "@/components/recording/RecordingCameraPreview";

interface BackendStatus {
  recording_active: boolean;
  current_phase: string;
  current_episode?: number;
  total_episodes?: number;
  phase_elapsed_seconds?: number;
  phase_time_limit_s?: number;
  session_elapsed_seconds?: number;
  is_paused?: boolean;
}

const RecordingCameras: React.FC = () => {
  const [searchParams] = useSearchParams();
  const { baseUrl } = useApi();
  const [status, setStatus] = useState<BackendStatus | null>(null);

  const cameras = searchParams.get("cameras")?.split(",").filter(Boolean) || [];

  useEffect(() => {
    const pollStatus = async () => {
      try {
        const response = await fetch(`${baseUrl}/recording-status`, {
          headers: { "Cache-Control": "no-cache" },
        });
        if (response.ok) {
          const data = await response.json();
          setStatus(data);
        }
      } catch (error) {
        console.error("Error polling recording status:", error);
      }
    };

    pollStatus();
    const interval = setInterval(pollStatus, 1000);
    return () => clearInterval(interval);
  }, [baseUrl]);

  // Determine progress and format time
  const phaseElapsed = status?.phase_elapsed_seconds || 0;
  const phaseLimit = status?.phase_time_limit_s || 1;
  const progressPercent = Math.min(
    100,
    Math.max(0, (phaseElapsed / phaseLimit) * 100)
  );
  const remainingSeconds = Math.max(0, phaseLimit - phaseElapsed);

  const formatTime = (seconds: number): string => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, "0")}:${secs
      .toString()
      .padStart(2, "0")}`;
  };

  const getPhaseText = () => {
    if (status?.is_paused) return "PAUSED";
    switch (status?.current_phase) {
      case "preparing":
        return "PREPARING";
      case "resetting":
        return "RESETTING";
      case "recording":
        return "RECORDING";
      case "completed":
        return "COMPLETED";
      default:
        return "IDLE";
    }
  };

  const getPhaseColor = () => {
    if (status?.is_paused) return "text-yellow-400 bg-yellow-400/10 border-yellow-400/20";
    switch (status?.current_phase) {
      case "recording":
        return "text-red-400 bg-red-400/10 border-red-400/20";
      case "resetting":
      case "preparing":
        return "text-orange-400 bg-orange-400/10 border-orange-400/20";
      case "completed":
        return "text-green-400 bg-green-400/10 border-green-400/20";
      default:
        return "text-slate-400 bg-slate-800 border-slate-700";
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col p-4 text-white overflow-hidden gap-6">
      {/* Cameras Grid */}
      <div className="flex-1 min-h-0 w-full">
        {cameras.length === 0 ? (
          <div className="text-slate-500 h-full flex items-center justify-center">No cameras available.</div>
        ) : (
          <div 
            className={`w-full h-full grid gap-4 ${
              cameras.length === 1 ? 'grid-cols-1' :
              cameras.length === 2 ? 'grid-cols-2' :
              'grid-cols-2 lg:grid-cols-3'
            }`}
          >
            {cameras.map((cam) => (
              <div key={cam} className="w-full h-full min-h-0 bg-black rounded-lg overflow-hidden border border-slate-800 flex items-center justify-center">
                <div className="w-full h-full flex items-center justify-center [&>div]:!h-full [&>div]:!w-full [&>div]:!aspect-auto [&_img]:!object-contain">
                  <CameraFeed name={cam} expanded />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Centered Music Player Style Control Bar */}
      <div className="flex justify-center shrink-0 mb-2">
        <div className="w-full max-w-3xl bg-slate-900/90 backdrop-blur-md border border-slate-700/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col">
          {/* Progress Bar (top edge of the control bar) */}
          <div className="h-1.5 w-full bg-slate-800/80">
            <div
              className={`h-full transition-all duration-1000 ease-linear ${
                status?.current_phase === 'recording' ? 'bg-red-500 shadow-[0_0_10px_rgba(239,68,68,0.8)]' : 'bg-orange-500'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>

          {/* Controls / Info */}
          <div className="px-6 py-4 flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="flex flex-col">
                <h1 className="text-lg font-bold tracking-tight text-white/90 leading-tight">LeLab</h1>
                <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wider">Recording</span>
              </div>
              <div className="w-px h-8 bg-slate-700/60 mx-2" />
              <div className="flex flex-col">
                <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wider">Episode</span>
                <span className="font-mono text-base font-medium">
                  {status?.current_episode ?? "-"}{" "}
                  <span className="text-slate-500">/ {status?.total_episodes ?? "-"}</span>
                </span>
              </div>
            </div>

            <div className="flex items-center gap-8">
              <div className={`px-4 py-1.5 rounded-full border text-sm font-bold tracking-wider ${getPhaseColor()} flex items-center gap-2 shadow-lg`}>
                {status?.current_phase === 'recording' && !status?.is_paused && (
                  <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                )}
                {getPhaseText()}
              </div>
              
              <div className="font-mono text-4xl font-bold text-slate-100 tabular-nums w-28 text-right tracking-tighter drop-shadow-md">
                {formatTime(remainingSeconds)}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RecordingCameras;
