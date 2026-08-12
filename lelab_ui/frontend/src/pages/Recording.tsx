import React, { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useToast } from "@/hooks/use-toast";
import {
  ArrowLeft,
  MoreHorizontal,
  RotateCcw,
  Square,
  SkipForward,
  Play,
  Pause,
  Volume2,
  VolumeX,
  Trash2,
  Activity,
  AlertTriangle,
} from "lucide-react";
import {
  getMuted,
  setMuted as persistMuted,
  playRecordingStartCue,
  playResetStartCue,
  playAutoAdvanceWarning,
  startFreezeBuzzer,
  stopFreezeBuzzer,
} from "@/lib/recordingAudio";
import { useApi } from "@/contexts/ApiContext";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

import { JointGraph } from "@/components/recording/JointGraph";
import { RecordingCameraPreview } from "@/components/recording/RecordingCameraPreview";

interface RecordingConfig {
  leader_port: string;
  follower_port: string;
  leader_config: string;
  follower_config: string;
  dataset_repo_id: string;
  single_task: string;
  task_options?: string[];
  num_episodes: number;
  episode_time_s: number;
  reset_time_s: number;
  fps: number;
  video: boolean;
  push_to_hub: boolean;
  resume?: boolean;
  streaming_encoding?: boolean;
  vcodec?: string;
  cameras: Record<string, any>;
  test_mode?: boolean;
  dataset_version?: string;
  arm_mode?: string;
  home_position_id?: string;
  include_ee_pose?: boolean;
  robot_name?: string;
}

type Phase = "preparing" | "recording" | "homing" | "saving" | "resetting" | "completed";

interface BackendStatus {
  recording_active: boolean;
  current_phase: string;
  current_episode?: number;
  total_episodes?: number;
  saved_episodes?: number;
  phase_elapsed_seconds?: number;
  phase_time_limit_s?: number;
  session_elapsed_seconds?: number;
  session_ended?: boolean;
  dataset_repo_id?: string;
  is_paused?: boolean;
  available_controls: {
    stop_recording: boolean;
    exit_early: boolean;
    rerecord_episode: boolean;
    toggle_pause?: boolean;
  };
  joint_positions?: Record<string, number>;
  current_task?: string;
  error?: string | null;
  frozen_cameras?: string[];
  events_state?: Record<string, any>;
}

const Recording = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { toast } = useToast();
  const { baseUrl, wsBaseUrl, fetchWithHeaders } = useApi();

  // Get recording config from navigation state
  const recordingConfig = location.state?.recordingConfig as RecordingConfig;

  // Backend status state - this is the single source of truth
  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(
    null
  );
  const [recordingSessionStarted, setRecordingSessionStarted] = useState(false);

  const [optimisticPhase, setOptimisticPhase] = useState<Phase | null>(null);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
  // Camera-freeze alarm. `showFreezeAlert` is the modal; once acknowledged the
  // buzzer stops but `frozenCameras` keeps a persistent banner on screen for as
  // long as the feed is still stalled, so silencing the alarm can't be mistaken
  // for the problem being fixed.
  const [showFreezeAlert, setShowFreezeAlert] = useState(false);
  const [frozenCameras, setFrozenCameras] = useState<string[]>([]);
  const wasFrozenRef = useRef(false);
  const [muted, setMutedState] = useState<boolean>(() => getMuted());
  const [leftArmFixed, setLeftArmFixed] = useState(false);
  const [rightArmFixed, setRightArmFixed] = useState(false);
  const [isTriggeringHome, setIsTriggeringHome] = useState(false);
  // Session ("persistent") locks are a separate concept from the transient
  // lock above: they survive episode boundaries and veto the recorder's
  // auto-unlock for that arm.
  const [leftSessionLock, setLeftSessionLock] = useState(false);
  const [rightSessionLock, setRightSessionLock] = useState(false);
  const [sessionLockInit, setSessionLockInit] = useState(false);
  const prevRealPhaseRef = useRef<Phase | null>(null);
  // Bumps on each re-record so the auto-advance warning re-fires for the same episode number.
  const [rerecordTick, setRerecordTick] = useState(0);
  const warningFiredForPhaseRef = useRef<{ phase: Phase | null; episode: number | null; tick: number }>({ phase: null, episode: null, tick: 0 });
  // Guards against React StrictMode double-invocation of the start effect.
  const startInitiatedRef = useRef(false);

  const [taskHistory, setTaskHistory] = useState<string[]>([]);
  const [customTaskInput, setCustomTaskInput] = useState<string>("");
  const [isUpdatingTask, setIsUpdatingTask] = useState(false);
  const selectedTaskValue =
    taskHistory.includes(customTaskInput) ? customTaskInput : undefined;

  useEffect(() => {
    if (recordingConfig && taskHistory.length === 0) {
      const normalized = (recordingConfig.task_options ?? [])
        .map((t) => (typeof t === "string" ? t.trim() : ""))
        .filter((t) => t.length > 0);
      const merged = normalized.length > 0
        ? normalized
        : [recordingConfig.single_task].filter((t) => !!t?.trim());
      setTaskHistory(Array.from(new Set(merged)));
      setCustomTaskInput(recordingConfig.single_task);
    }
  }, [recordingConfig, taskHistory.length]);

  useEffect(() => {
    if (taskHistory.length === 0) return;
    if (!customTaskInput || !taskHistory.includes(customTaskInput)) {
      setCustomTaskInput(taskHistory[0]);
    }
  }, [taskHistory, customTaskInput]);

  const handleUpdateTask = async () => {
    if (!customTaskInput.trim()) return;
    setIsUpdatingTask(true);
    try {
      const response = await fetchWithHeaders(`${baseUrl}/set-episode-task`, {
        method: "POST",
        body: JSON.stringify({ task: customTaskInput.trim() }),
      });
      if (response.ok) {
        toast({ title: "Task Updated", description: `Task set to: ${customTaskInput.trim()}` });
        if (!taskHistory.includes(customTaskInput.trim())) {
          setTaskHistory(prev => [...prev, customTaskInput.trim()]);
        }
      } else {
        const data = await response.json();
        toast({ title: "Error", description: data.message, variant: "destructive" });
      }
    } catch (err) {
      toast({ title: "Connection Error", description: "Could not connect to backend", variant: "destructive" });
    } finally {
      setIsUpdatingTask(false);
    }
  };

  // Transient lock: park this arm at home right now. Deliberately does NOT
  // touch the persistent (session) lock — those were previously set together,
  // which meant any momentary lock also installed a session-long veto on the
  // recorder's auto-unlock. They are separate controls now.
  const setArmLocked = async (arm: "left" | "right", locked: boolean) => {
    if (arm === "left") setLeftArmFixed(locked);
    else setRightArmFixed(locked);
    try {
      await fetchWithHeaders(`${baseUrl}/toggle-${arm}-arm-home`, {
        method: "POST",
        body: JSON.stringify({ fixed: locked }),
      });
    } catch (e) {
      console.error(e);
    }
  };

  // Session lock: "keep this arm locked even once recording starts". This is
  // the flag the recorder checks before auto-unlocking:
  //   unlock_right = arm_mode in ("both","right") and not persistent_right_lock
  const setArmSessionLock = async (arm: "left" | "right", locked: boolean) => {
    if (arm === "left") setLeftSessionLock(locked);
    else setRightSessionLock(locked);
    try {
      await fetchWithHeaders(`${baseUrl}/set-persistent-lock`, {
        method: "POST",
        body: JSON.stringify({ arm, locked }),
      });
      // A session lock should take effect immediately, not only at the next
      // episode boundary; and releasing it should hand the arm back.
      await setArmLocked(arm, locked);
    } catch (e) {
      console.error(e);
    }
  };

  // Default session locks from arm_mode: lock whichever arm is NOT being
  // recorded, so it stays parked for the whole session instead of drifting
  // with the operator. "both" locks neither. Runs once; the buttons below can
  // still override either arm at any point, including mid-recording.
  useEffect(() => {
    if (sessionLockInit || !recordingConfig) return;
    const mode = recordingConfig.arm_mode ?? "both";
    const wantLeft = mode === "right";   // recording right -> park left
    const wantRight = mode === "left";   // recording left  -> park right
    setSessionLockInit(true);
    if (wantLeft) setArmSessionLock("left", true);
    if (wantRight) setArmSessionLock("right", true);
  }, [recordingConfig, sessionLockInit]);

  const handleToggleLeftArm = () => setArmLocked("left", !leftArmFixed);
  const handleToggleRightArm = () => setArmLocked("right", !rightArmFixed);
  const handleToggleBothArms = () => {
    // "B" reflects the pair: if either is unlocked, lock both; else release both.
    const next = !(leftArmFixed && rightArmFixed);
    setArmLocked("left", next);
    setArmLocked("right", next);
  };

  const handleTriggerHome = async () => {
    setIsTriggeringHome(true);
    setLeftArmFixed(true);
    setRightArmFixed(true);
    try {
      // Deliberately does NOT set a persistent lock. Homing is a transient
      // "park the arms and hold them there" action, whereas the persistent
      // lock means "keep this arm locked even once recording starts" and is
      // what the recorder checks before auto-unlocking the arm being recorded:
      //   unlock_right = arm_mode in ("both","right") and not persistent_right_lock
      // This function also runs automatically at session start, so setting the
      // lock here installed that veto on every session and left the recorded
      // arm stuck locked until the operator unlocked it by hand.
      // Persistent intent belongs to the explicit Lock/Unlock buttons only.
      await fetchWithHeaders(`${baseUrl}/trigger-home-now`, {
        method: "POST"
      });
      toast({ title: "Arms Locked to Home", description: "Both arms are securely locked to the selected home position. Click the unlock buttons to release them." });
    } catch (e) {
      console.error(e);
    } finally {
      setTimeout(() => setIsTriggeringHome(false), 2000);
    }
  };

  const toggleMute = useCallback(() => {
    setMutedState((prev) => {
      const next = !prev;
      persistMuted(next);
      return next;
    });
  }, []);

  // Redirect if no config provided
  useEffect(() => {
    if (!recordingConfig) {
      toast({
        title: "No Configuration",
        description: "Please start recording from the main page.",
        variant: "destructive",
      });
      navigate("/");
    }
  }, [recordingConfig, navigate, toast]);

  // Start recording session when component loads. The ref guard prevents
  // React StrictMode (and any future re-renders) from firing /start-recording
  // twice — the second call returns 409 and bounces the user home.
  useEffect(() => {
    if (recordingConfig && !startInitiatedRef.current) {
      startInitiatedRef.current = true;
      startRecordingSession();
    }
    // startRecordingSession is intentionally omitted: re-running this effect
    // on its identity change would re-fire /start-recording.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingConfig]);

  // Refs so the poll interval below stays stable and reads the latest values
  // without tearing itself down on every state change.
  const optimisticPhaseRef = useRef(optimisticPhase);
  optimisticPhaseRef.current = optimisticPhase;
  const rerecordTickRef = useRef(rerecordTick);
  rerecordTickRef.current = rerecordTick;

  // Poll backend status continuously to stay in sync
  useEffect(() => {
    if (!recordingSessionStarted) return;

    const pollStatus = async () => {
      try {
        const response = await fetchWithHeaders(
          `${baseUrl}/recording-status`
        );
        if (!response.ok) return;
        const status = await response.json();
        setBackendStatus(status);

        const currentOptimistic = optimisticPhaseRef.current;
        if (currentOptimistic && status.current_phase === currentOptimistic) {
          setOptimisticPhase(null);
        }

        const real = status.current_phase as Phase;
        const prev = prevRealPhaseRef.current;
        
        if (prev !== real) {
          if (real === "recording" && prev !== null) {
            playRecordingStartCue();
            // Note: Auto-unlock is now handled synchronously in the backend
            // to completely eliminate polling delay when recording starts.
          } else if (prev === "recording" && real !== "recording") {
            // Reflect the lock the BACKEND performs, without sending our own.
            //
            // This used to POST toggle_*_home:true here. That command is
            // redundant — the recorder already sends set_home_target with
            // lock_all when it enters homing, and again at reset — and it was
            // racy: fired from a ~1s status poll, it could arrive after the next
            // episode's unlock and leave the arm pinned at home for that whole
            // episode. Lock/unlock during a session now has exactly one owner.
            setLeftArmFixed(true);
            setRightArmFixed(true);
            if (real === "resetting") playResetStartCue();
          } else if (real === "resetting") {
            playResetStartCue();
          }
          prevRealPhaseRef.current = real;
          warningFiredForPhaseRef.current = { phase: null, episode: null, tick: 0 };
        }

        // Camera-freeze detection. The backend auto-pauses on a stalled feed
        // (events_state._freeze_paused) and auto-resumes once it recovers, so
        // this is edge-triggered off that flag rather than off the pause state,
        // which the operator can also toggle manually.
        const frozen: string[] = Array.isArray(status.frozen_cameras)
          ? status.frozen_cameras
          : [];
        const isFreezePaused =
          Boolean(status.events_state?._freeze_paused) || frozen.length > 0;
        setFrozenCameras(frozen);
        if (isFreezePaused && !wasFrozenRef.current) {
          setShowFreezeAlert(true);
          startFreezeBuzzer();
        } else if (!isFreezePaused && wasFrozenRef.current) {
          // Recovered on its own -- stand down without needing acknowledgement.
          stopFreezeBuzzer();
          setShowFreezeAlert(false);
        }
        wasFrozenRef.current = isFreezePaused;

        const elapsed = status.phase_elapsed_seconds || 0;
        const limit = status.phase_time_limit_s || 0;
        const inFinalThreeSeconds = limit > 3 && elapsed >= limit - 3;
        const ep = status.current_episode ?? null;
        const tick = rerecordTickRef.current;
        const warned = warningFiredForPhaseRef.current;
        if (
          inFinalThreeSeconds &&
          currentOptimistic === null &&
          (warned.phase !== real ||
            warned.episode !== ep ||
            warned.tick !== tick)
        ) {
          playAutoAdvanceWarning();
          warningFiredForPhaseRef.current = { phase: real, episode: ep, tick };
        }

        if (!status.recording_active && status.session_ended) {
          const datasetInfo = {
            dataset_repo_id:
              status.dataset_repo_id || recordingConfig.dataset_repo_id,
            single_task: recordingConfig.single_task,
            num_episodes: recordingConfig.num_episodes,
            saved_episodes: status.saved_episodes || 0,
            session_elapsed_seconds: status.session_elapsed_seconds || 0,
          };
          if (status.current_phase !== "error") {
            navigate("/upload", { state: { datasetInfo, recordingConfig } });
          } else {
            toast({
              title: "Recording Failed",
              description:
                status.error ||
                "The backend recording session encountered an error. Please check your cameras or logs and try again.",
              variant: "destructive",
            });
            navigate(-1);
          }
        }
      } catch (error) {
        console.error("Error polling recording status:", error);
      }
    };

    pollStatus();
    const statusInterval = setInterval(pollStatus, 1000);
    return () => clearInterval(statusInterval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingSessionStarted, recordingConfig, navigate, baseUrl, fetchWithHeaders]);

  // Never let the buzzer outlive this page (navigation to /upload, unmount, HMR).
  useEffect(() => stopFreezeBuzzer, []);

  const acknowledgeFreezeAlarm = useCallback(() => {
    stopFreezeBuzzer();
    setShowFreezeAlert(false);
  }, []);

  const formatTime = (seconds: number): string => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, "0")}:${secs
      .toString()
      .padStart(2, "0")}`;
  };

  const startRecordingSession = async () => {
    try {
      const response = await fetchWithHeaders(`${baseUrl}/start-recording`, {
        method: "POST",
        body: JSON.stringify(recordingConfig),
      });

      const data = await response.json();

      if (response.ok) {
        setRecordingSessionStarted(true);
        handleTriggerHome();
        toast({
          title: "Recording Started",
          description: `Started recording ${recordingConfig.num_episodes} episodes`,
        });
      } else {
        toast({
          title: "Error Starting Recording",
          description: data.message || "Failed to start recording session.",
          variant: "destructive",
        });
        navigate("/");
      }
    } catch (error) {
      toast({
        title: "Connection Error",
        description: "Could not connect to the backend server.",
        variant: "destructive",
      });
      navigate("/");
    }
  };

  const handleExitEarly = useCallback(async () => {
    if (!backendStatus?.available_controls.exit_early) return;
    if (optimisticPhase !== null) return;

    const realPhase = backendStatus.current_phase as Phase;
    const next: Phase | null =
      realPhase === "recording" ? "resetting" :
      realPhase === "resetting" ? "recording" : null;

    if (!next) return;

    try {
      const response = await fetchWithHeaders(
        `${baseUrl}/recording-exit-early`,
        { method: "POST" }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        setOptimisticPhase(null);
        toast({
          title: "Episode still recording",
          description: data.message || "Record at least 5 seconds before ending the episode.",
        });
        return;
      }

      if (realPhase === "recording") {
        // Only lock arms after the backend accepts the end command.  This
        // prevents an ignored early command from moving the robot home.
        setLeftArmFixed(true);
        setRightArmFixed(true);
        fetchWithHeaders(`${baseUrl}/toggle-left-arm-home`, { method: "POST", body: JSON.stringify({ fixed: true }) }).catch(console.error);
        fetchWithHeaders(`${baseUrl}/toggle-right-arm-home`, { method: "POST", body: JSON.stringify({ fixed: true }) }).catch(console.error);
      }
      // For an episode-ending command, wait for the backend's _is_homing
      // status instead of showing the next reset phase immediately.  This
      // keeps the UI aligned with the physical arm transition.
      if (realPhase !== "recording") {
        setOptimisticPhase(next);
      }
    } catch (error) {
      setOptimisticPhase(null);
      toast({
        title: "Connection Error",
        description: "Could not connect to the backend server.",
        variant: "destructive",
      });
    }
  }, [backendStatus, optimisticPhase, baseUrl, fetchWithHeaders, toast]);

  const handleTogglePause = useCallback(async () => {
    if (!backendStatus?.available_controls.toggle_pause) return;
    try {
      const response = await fetchWithHeaders(`${baseUrl}/recording-toggle-pause`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        toast({ title: "Error", description: data.message, variant: "destructive" });
      }
    } catch (error) {
      toast({ title: "Connection Error", description: "Could not connect to the backend server.", variant: "destructive" });
    }
  }, [backendStatus, baseUrl, fetchWithHeaders, toast]);

  const [showRerecordPrompt, setShowRerecordPrompt] = useState(false);

  const requestRerecordEpisode = useCallback(async () => {
    if (!backendStatus?.available_controls.rerecord_episode) return;
    
    // Temporarily unlock arms so the user can safely guide them to home
    setLeftArmFixed(false);
    setRightArmFixed(false);
    fetchWithHeaders(`${baseUrl}/toggle-left-arm-home`, { method: "POST", body: JSON.stringify({ fixed: false }) }).catch(console.error);
    fetchWithHeaders(`${baseUrl}/toggle-right-arm-home`, { method: "POST", body: JSON.stringify({ fixed: false }) }).catch(console.error);
    
    setShowRerecordPrompt(true);
  }, [backendStatus, baseUrl, fetchWithHeaders]);

  const confirmRerecordEpisode = useCallback(async () => {
    setShowRerecordPrompt(false);

    // Lock arms back to exact zero before triggering backend re-record
    setLeftArmFixed(true);
    setRightArmFixed(true);
    fetchWithHeaders(`${baseUrl}/toggle-left-arm-home`, { method: "POST", body: JSON.stringify({ fixed: true }) }).catch(console.error);
    fetchWithHeaders(`${baseUrl}/toggle-right-arm-home`, { method: "POST", body: JSON.stringify({ fixed: true }) }).catch(console.error);

    try {
      const response = await fetchWithHeaders(
        `${baseUrl}/recording-rerecord-episode`,
        {
          method: "POST",
        }
      );
      const data = await response.json();

      // The endpoint answers 200 even when it refuses the request (e.g. a
      // re-record is already in progress), so response.ok alone would show a
      // success toast for a press that did nothing — which is what made a
      // second press look necessary.
      if (response.ok && data.success !== false) {
        setRerecordTick((t) => t + 1);
        toast({
          title: "Re-recording Episode",
          description: `Episode ${backendStatus?.current_episode} will be re-recorded.`,
        });
      } else {
        toast({
          title: "Error",
          description: data.message,
          variant: "destructive",
        });
      }
    } catch (error) {
      toast({
        title: "Connection Error",
        description: "Could not connect to the backend server.",
        variant: "destructive",
      });
    }
  }, [backendStatus, baseUrl, fetchWithHeaders, toast]);

  const handleStopRecording = useCallback(async () => {
    if (!backendStatus?.available_controls.stop_recording) return;
    try {
      const response = await fetchWithHeaders(`${baseUrl}/stop-recording`, {
        method: "POST",
      });
      const data = await response.json();

      if (!response.ok || !data.success) {
        toast({
          title: "Stop ignored",
          description: data.message || "Record at least 5 seconds before stopping.",
        });
        return;
      }

      // Only home the arms after the backend accepts the stop request.
      fetchWithHeaders(`${baseUrl}/toggle-left-arm-home`, { method: "POST", body: JSON.stringify({ fixed: true }) }).catch(console.error);
      fetchWithHeaders(`${baseUrl}/toggle-right-arm-home`, { method: "POST", body: JSON.stringify({ fixed: true }) }).catch(console.error);

      toast({
        title: "Stopping recording",
        description: "Finalizing dataset…",
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to stop recording.",
        variant: "destructive",
      });
    }
  }, [backendStatus, baseUrl, fetchWithHeaders, toast]);

  const requestStopRecording = useCallback(() => {
    if (!backendStatus?.available_controls.stop_recording) return;
    setShowStopConfirm(true);
  }, [backendStatus]);

  const confirmStopRecording = useCallback(async () => {
    setShowStopConfirm(false);
    await handleStopRecording();
  }, [handleStopRecording]);

  const handleDiscardRecording = useCallback(async () => {
    if (!backendStatus?.available_controls.stop_recording) return;
    try {
      await fetchWithHeaders(`${baseUrl}/discard-recording`, {
        method: "POST",
      });

      toast({
        title: "Discarding recording",
        description: "Deleting dataset files…",
      });
    } catch (error) {
      toast({
        title: "Error",
        description: "Failed to discard recording.",
        variant: "destructive",
      });
    }
  }, [backendStatus, baseUrl, fetchWithHeaders, toast]);

  const requestDiscardRecording = useCallback(() => {
    if (!backendStatus?.available_controls.stop_recording) return;
    setShowDiscardConfirm(true);
  }, [backendStatus]);

  const confirmDiscardRecording = useCallback(async () => {
    setShowDiscardConfirm(false);
    await handleDiscardRecording();
  }, [handleDiscardRecording]);

  const handlersRef = useRef({
    handleExitEarly,
    requestRerecordEpisode,
    handleTogglePause,
    requestStopRecording,
    showStopConfirm,
    requestDiscardRecording,
    showDiscardConfirm,
  });
  useEffect(() => {
    handlersRef.current = {
      handleExitEarly,
      requestRerecordEpisode,
      handleTogglePause,
      requestStopRecording,
      showStopConfirm,
      requestDiscardRecording,
      showDiscardConfirm,
    };
  });

  const sessionReady = recordingSessionStarted && backendStatus !== null;

  useEffect(() => {
    if (!sessionReady) return;

    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      if (e.key === " " || e.code === "Space" || e.key === "ArrowRight") {
        e.preventDefault();
        handlersRef.current.handleExitEarly();
      } else if (e.key === "ArrowLeft" || e.key.toLowerCase() === "r") {
        e.preventDefault();
        handlersRef.current.requestRerecordEpisode();
      } else if (e.key.toLowerCase() === "p") {
        e.preventDefault();
        handlersRef.current.handleTogglePause();
      } else if (e.key === "Escape") {
        if (handlersRef.current.showStopConfirm || handlersRef.current.showDiscardConfirm) return;
        handlersRef.current.requestStopRecording();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sessionReady]);

  if (!recordingConfig) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center">
          <p className="text-lg">No recording configuration found.</p>
          <Button onClick={() => navigate("/")} className="mt-4">
            Return to Home
          </Button>
        </div>
      </div>
    );
  }

  // Show loading state while waiting for backend status
  if (!backendStatus) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-red-500 mx-auto mb-4"></div>
          <p className="text-lg">Connecting to recording session...</p>
        </div>
      </div>
    );
  }

  const realPhase = backendStatus.current_phase as Phase;
  const isHoming = Boolean(backendStatus.events_state?._is_homing);
  const currentPhase: Phase = isHoming ? "homing" : (optimisticPhase ?? realPhase);
  const currentEpisode = backendStatus.current_episode ?? 1;
  const totalEpisodes =
    backendStatus.total_episodes ?? recordingConfig.num_episodes;

  const phaseElapsedTime = optimisticPhase
    ? 0
    : backendStatus.phase_elapsed_seconds || 0;
  const phaseTimeLimit =
    currentPhase === "recording"
      ? Math.max(5, recordingConfig.episode_time_s)
      : currentPhase === "homing" || currentPhase === "saving"
      ? 0
      : currentPhase === "resetting"
      ? recordingConfig.reset_time_s
      : backendStatus.phase_time_limit_s || 0;

  const sessionElapsedTime = backendStatus.session_elapsed_seconds || 0;

  const getStatusText = () => {
    if (currentPhase === "recording") return `RECORDING EPISODE ${currentEpisode}`;
    if (currentPhase === "homing") return "WAITING FOR ARMS TO REACH HOME";
    if (currentPhase === "saving") return "SAVING EPISODE…";
    if (currentPhase === "resetting") return "RESET — GET READY";
    if (currentPhase === "preparing") return "PREPARING SESSION";
    return "SESSION COMPLETE";
  };

  const phaseColor =
    currentPhase === "recording"
      ? { dot: "bg-red-500", pill: "bg-red-500/15 text-red-300", timer: "text-green-400", bar: "bg-green-500", button: "bg-green-500 hover:bg-green-600" }
      : currentPhase === "homing" || currentPhase === "saving"
      ? { dot: "bg-orange-500", pill: "bg-orange-500/15 text-orange-300", timer: "text-orange-400", bar: "bg-orange-500", button: "bg-orange-500 hover:bg-orange-600" }
      : currentPhase === "resetting"
      ? { dot: "bg-orange-500", pill: "bg-orange-500/15 text-orange-300", timer: "text-orange-400", bar: "bg-orange-500", button: "bg-orange-500 hover:bg-orange-600" }
      : { dot: "bg-gray-500", pill: "bg-gray-500/15 text-gray-300", timer: "text-gray-400", bar: "bg-gray-500", button: "bg-gray-500" };

  const primaryLabel =
    currentPhase === "recording"
      ? "End Episode"
      : currentPhase === "homing"
      ? "Waiting for Home"
      : currentPhase === "saving"
      ? "Saving Episode"
      : currentPhase === "resetting"
      ? "Start Next Episode"
      : "Advance";

  const PrimaryIcon = currentPhase === "recording" ? SkipForward : currentPhase === "homing" || currentPhase === "saving" ? Activity : Play;

  return (
    <div className="min-h-screen bg-black text-white p-8">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <Button
            onClick={() => navigate("/")}
            variant="outline"
            className="border-gray-500 hover:border-gray-200 text-gray-300 hover:text-white"
          >
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back to Home
          </Button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          <div className="col-span-1 lg:col-span-5 bg-gray-900 rounded-lg border border-gray-700 p-8 h-fit">
            <div className="flex justify-end items-center gap-4 mb-6 text-sm text-gray-400">
            <span aria-label={`Episode ${currentEpisode} of ${totalEpisodes}`}>
              Episode <span className="text-white font-semibold">{currentEpisode}</span> / {totalEpisodes}
            </span>
            <span className="font-mono" aria-label={`Total session time ${formatTime(sessionElapsedTime)}`}>
              {formatTime(sessionElapsedTime)}
            </span>
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleMute}
              aria-label={muted ? "Unmute" : "Mute"}
              className="h-8 w-8 text-gray-400 hover:text-white hover:bg-gray-800"
            >
              {muted ? <VolumeX className="w-5 h-5" /> : <Volume2 className="w-5 h-5" />}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-gray-400 hover:text-white hover:bg-gray-800"
                  aria-label="More actions"
                >
                  <MoreHorizontal className="w-5 h-5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="end"
                onCloseAutoFocus={(e) => e.preventDefault()}
                className="bg-gray-900 border-gray-700 text-white"
              >
                <DropdownMenuItem
                  onClick={requestRerecordEpisode}
                  disabled={!backendStatus.available_controls.rerecord_episode}
                  className="focus:bg-gray-800 focus:text-white"
                >
                  <RotateCcw className="w-4 h-4 mr-2" />
                  Re-record episode
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {frozenCameras.length > 0 && (
            <div
              role="alert"
              aria-live="assertive"
              className="mb-4 flex items-start gap-3 rounded-lg border border-red-600 bg-red-950/50 px-4 py-3"
            >
              <AlertTriangle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
              <div className="text-sm">
                <div className="font-semibold text-red-300">
                  Camera feed frozen — recording paused
                </div>
                <div className="text-red-200/80">
                  <span className="font-mono">{frozenCameras.join(", ")}</span> —
                  check the camera, then it auto-resumes once frames resume.
                </div>
              </div>
              {!showFreezeAlert && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => startFreezeBuzzer()}
                  className="ml-auto flex-shrink-0 border-red-700 text-red-300 hover:bg-red-900/40"
                >
                  <Volume2 className="w-4 h-4 mr-1" />
                  Buzzer
                </Button>
              )}
            </div>
          )}

          <div className="text-center mb-6 flex justify-center gap-3">
            <div
              role="status"
              aria-live="polite"
              className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-bold tracking-widest ${phaseColor.pill}`}
            >
              <span className={`w-2 h-2 rounded-full ${phaseColor.dot} ${currentPhase !== "completed" ? "animate-pulse" : ""}`} />
              {getStatusText()}
            </div>
            {backendStatus.is_paused && (
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-bold tracking-widest bg-yellow-500/20 text-yellow-500 border border-yellow-500/50 animate-pulse">
                PAUSED
              </div>
            )}
          </div>

            {backendStatus?.events_state?._is_homing && (
              <div className="mt-4 mb-4 bg-orange-950/40 border border-orange-500/30 p-4 rounded-xl shadow-lg w-full max-w-lg text-left">
                <div className="flex items-center gap-2 mb-2 text-orange-400 font-bold">
                  <Activity className="w-5 h-5 animate-pulse" />
                  Waiting for arms to reach home...
                </div>
                <div className="grid grid-cols-2 gap-4 text-xs font-mono mt-3">
                  <div className="bg-black/50 p-3 rounded-lg border border-white/10">
                    <span className="text-gray-400 block mb-1 uppercase tracking-wider text-[10px]">Target Zero</span>
                    <div className="text-green-400 leading-tight">
                      {backendStatus.events_state.target_home_state ? 
                        backendStatus.events_state.target_home_state.map((v: number) => v.toFixed(3)).join(", ") 
                        : "Unknown"}
                    </div>
                  </div>
                  <div className="bg-black/50 p-3 rounded-lg border border-white/10">
                    <span className="text-gray-400 block mb-1 uppercase tracking-wider text-[10px]">Live Position</span>
                    <div className="text-orange-400 leading-tight">
                      {backendStatus.events_state.current_robot_state ? 
                        backendStatus.events_state.current_robot_state.map((v: number) => v.toFixed(3)).join(", ") 
                        : "Waiting..."}
                    </div>
                  </div>
                </div>
                <p className="text-orange-300/60 text-[10px] mt-3 italic text-center">Episode will end automatically when live values match target zero.</p>
              </div>
            )}

          <div className="text-center mb-4">
            <div className={`text-7xl font-mono font-bold leading-none ${phaseColor.timer}`}>
              {formatTime(phaseElapsedTime)}
            </div>
            <div className="text-sm text-gray-500 mt-2">
              / {formatTime(phaseTimeLimit)}
            </div>
          </div>

          <div className="w-full bg-gray-800 rounded-full h-1.5 mb-8">
            <div
              className={`h-1.5 rounded-full transition-all duration-500 ${phaseColor.bar}`}
              style={{
                width: `${Math.min((phaseElapsedTime / phaseTimeLimit) * 100, 100)}%`,
              }}
            />
          </div>

          <div className="mb-8 p-4 bg-gray-800/50 rounded-xl border border-gray-700">
            <div className="text-sm text-gray-400 mb-2 font-semibold">Current Episode Task</div>
            <div className="flex flex-col gap-3">
              {taskHistory.length > 0 && (
                <Select value={selectedTaskValue} onValueChange={setCustomTaskInput}>
                  <SelectTrigger className="bg-gray-900 border-gray-600 text-white">
                    <SelectValue placeholder="Select existing task" />
                  </SelectTrigger>
                  <SelectContent className="bg-gray-900 border-gray-700">
                    {taskHistory.map((t, i) => (
                      <SelectItem key={`${t}-${i}`} value={t} className="text-white hover:bg-gray-800">
                        {t}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <input
                type="text"
                value={customTaskInput}
                onChange={(e) => setCustomTaskInput(e.target.value)}
                placeholder="Enter or edit task for this episode..."
                className="flex h-10 w-full rounded-md border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-white placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <Button 
                onClick={handleUpdateTask} 
                disabled={isUpdatingTask || customTaskInput.trim() === backendStatus?.current_task}
                variant="secondary"
                className="w-full bg-gray-700 hover:bg-gray-600 text-white border border-gray-600"
              >
                {customTaskInput.trim() === backendStatus?.current_task ? "Active Task" : "Assign to Episode"}
              </Button>
            </div>
            {backendStatus?.current_task && customTaskInput.trim() !== backendStatus.current_task && (
               <div className="mt-3 text-xs text-blue-400 bg-blue-900/20 p-2 rounded text-center break-words border border-blue-900/50">
                 Currently recording as: <br/><strong>{backendStatus.current_task}</strong>
               </div>
            )}
          </div>

          <div className="mb-8 p-4 bg-gray-800/50 rounded-xl border border-gray-700">
            <div className="text-sm text-gray-400 mb-2 font-semibold">Arm Homing Controls</div>
            <div className="flex flex-col gap-3">
              {/* Row 1: transient lock. Lit = that arm is currently held at home. */}
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-500 w-14 shrink-0">Lock</span>
                {([
                  { key: "L", lit: leftArmFixed, onClick: handleToggleLeftArm,
                    title: leftArmFixed ? "Left arm locked to home — click to release" : "Lock left arm to home" },
                  { key: "R", lit: rightArmFixed, onClick: handleToggleRightArm,
                    title: rightArmFixed ? "Right arm locked to home — click to release" : "Lock right arm to home" },
                  { key: "B", lit: leftArmFixed && rightArmFixed, onClick: handleToggleBothArms,
                    title: leftArmFixed && rightArmFixed ? "Both arms locked — click to release both" : "Lock both arms to home" },
                ] as const).map((b) => (
                  <Button
                    key={b.key}
                    onClick={b.onClick}
                    title={b.title}
                    aria-pressed={b.lit}
                    variant={b.lit ? "default" : "secondary"}
                    className={`w-12 h-10 p-0 font-bold text-base transition-colors ${
                      b.lit
                        ? "bg-blue-500 hover:bg-blue-400 text-white ring-2 ring-blue-300 shadow-lg shadow-blue-500/40"
                        : "bg-gray-700 hover:bg-gray-600 text-gray-400"
                    }`}
                  >
                    {b.key}
                  </Button>
                ))}
                <span className="text-xs text-gray-500 ml-1">
                  {leftArmFixed || rightArmFixed
                    ? `locked: ${[leftArmFixed && "L", rightArmFixed && "R"].filter(Boolean).join(" ")}`
                    : "both following exoskeleton"}
                </span>
              </div>

              {/* Row 2: session lock. Survives episode boundaries and vetoes the
                  recorder's auto-unlock, so an arm you are not recording stays
                  parked for the whole run. Defaults from arm_mode above. */}
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-500 w-14 shrink-0">Session</span>
                {([
                  { key: "L", lit: leftSessionLock, arm: "left" as const },
                  { key: "R", lit: rightSessionLock, arm: "right" as const },
                ]).map((b) => (
                  <Button
                    key={b.key}
                    onClick={() => setArmSessionLock(b.arm, !b.lit)}
                    aria-pressed={b.lit}
                    title={b.lit
                      ? `${b.arm} arm stays locked for the whole session — click to release`
                      : `Keep ${b.arm} arm locked for the whole session`}
                    variant={b.lit ? "default" : "secondary"}
                    className={`w-12 h-10 p-0 font-bold text-base transition-colors ${
                      b.lit
                        ? "bg-amber-500 hover:bg-amber-400 text-black ring-2 ring-amber-300 shadow-lg shadow-amber-500/40"
                        : "bg-gray-700 hover:bg-gray-600 text-gray-400"
                    }`}
                  >
                    {b.key}
                  </Button>
                ))}
                <span className="text-xs text-gray-500 ml-1">
                  {leftSessionLock || rightSessionLock
                    ? `held all session: ${[leftSessionLock && "L", rightSessionLock && "R"].filter(Boolean).join(" ")}`
                    : "no arm held across episodes"}
                </span>
              </div>
              <Button 
                onClick={handleTriggerHome} 
                disabled={isTriggeringHome}
                variant="outline"
                className="w-full bg-gray-800 border-gray-600 text-gray-300 hover:bg-gray-700 hover:text-white"
              >
                Home Both Arms Now
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-4">
            <Button
              onClick={handleTogglePause}
              disabled={!backendStatus.available_controls.toggle_pause || currentPhase === "completed"}
              className={`flex-1 text-white font-semibold py-6 text-lg disabled:opacity-50 ${backendStatus.is_paused ? 'bg-yellow-500 hover:bg-yellow-600 text-yellow-950' : 'bg-gray-700 hover:bg-gray-600'}`}
            >
              {backendStatus.is_paused ? (
                <><Play className="w-5 h-5 mr-2" /> Resume <span className="ml-2 px-2 py-0.5 rounded text-xs font-mono bg-black/30 text-white/70">P</span></>
              ) : (
                <><Pause className="w-5 h-5 mr-2" /> Pause <span className="ml-2 px-2 py-0.5 rounded text-xs font-mono bg-black/30 text-white/70">P</span></>
              )}
            </Button>

            <Button
              onClick={handleExitEarly}
              disabled={
                !backendStatus.available_controls.exit_early ||
                optimisticPhase !== null ||
                currentPhase === "homing" ||
                currentPhase === "saving" ||
                currentPhase === "completed"
              }
              className={`flex-[2] text-white font-semibold py-6 text-lg disabled:opacity-50 ${phaseColor.button}`}
            >
              <PrimaryIcon className="w-5 h-5 mr-2" />
              {primaryLabel}
              {currentPhase !== "completed" && (
                <span className="ml-3 px-2 py-0.5 rounded text-xs font-mono bg-black/30 text-white/70">SPACE / →</span>
              )}
            </Button>
          </div>

          {currentPhase === "completed" && (
            <p className="text-center text-sm text-gray-400 mt-6">
              Recording complete — redirecting to upload…
            </p>
          )}

          <div className="flex flex-col sm:flex-row justify-center gap-4 mt-8">
            <Button
              variant="destructive"
              onClick={requestDiscardRecording}
              disabled={!backendStatus.available_controls.stop_recording}
              className="w-full bg-red-950/40 text-red-400 hover:bg-red-900/60 hover:text-red-300 border border-red-900/50 font-semibold py-6 text-lg rounded-xl shadow transition-colors"
            >
              <Trash2 className="w-5 h-5 mr-3" />
              Discard Recording
            </Button>
            <Button
              variant="destructive"
              onClick={requestStopRecording}
              disabled={!backendStatus.available_controls.stop_recording}
              className="w-full bg-red-600/20 text-red-400 hover:bg-red-600 hover:text-white border border-red-900 font-semibold py-6 text-lg rounded-xl shadow transition-colors"
            >
              <Square className="w-5 h-5 mr-3" />
              Stop & Save Dataset
            </Button>
          </div>
        </div>

          <div className="lg:col-span-7 flex flex-col gap-8">
            <RecordingCameraPreview cameras={Object.keys(recordingConfig.cameras || {})} />
            <div className="flex-1 min-h-[300px] flex flex-col gap-4">
              <JointGraph jointPositions={backendStatus.joint_positions} armMode={recordingConfig.arm_mode} />
              
              <div className="bg-slate-900/50 border border-slate-800 rounded-xl p-4 flex flex-col min-h-0 relative shrink-0">
                <h3 className="text-white text-sm font-semibold mb-4">Live Joint Values vs Target Home State</h3>
                <div className="grid grid-cols-2 gap-6 text-xs font-mono">
                    <div>
                      <div className="text-slate-400 mb-3 font-semibold">Current State ({backendStatus?.events_state?.current_robot_state?.length || 0} joints)</div>
                      {backendStatus?.events_state?.current_robot_state ? (
                        <div className="grid grid-cols-4 gap-2">
                          {backendStatus.events_state.current_robot_state.map((v: number, i: number) => (
                            <div key={`cur-${i}`} className="bg-slate-800/80 text-blue-400 px-2 py-1 rounded text-center">
                              {v.toFixed(4)}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-slate-500">Waiting for live observation...</div>
                      )}
                    </div>
                    <div>
                      <div className="text-slate-400 mb-3 font-semibold">Target Home State</div>
                      {backendStatus?.events_state?.target_home_state ? (
                        <div className="grid grid-cols-4 gap-2">
                          {backendStatus.events_state.target_home_state.map((v: number, i: number) => (
                            <div key={`tgt-${i}`} className="bg-slate-800/80 text-green-400 px-2 py-1 rounded text-center">
                              {v.toFixed(4)}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-slate-500">Not loaded (No calibration.yaml found)</div>
                      )}
                    </div>
                </div>
              </div>
            </div>

          </div>
        </div>
      </div>

      <AlertDialog open={showRerecordPrompt} onOpenChange={setShowRerecordPrompt}>
        <AlertDialogContent className="bg-gray-900 border-gray-700 text-white">
          <AlertDialogHeader>
            <AlertDialogTitle>Re-record Episode</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-400">
              The arms have been temporarily unlocked. Please manually guide the arms to approximately their home position to avoid sudden jerks.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-gray-800 border-gray-700 text-white hover:bg-gray-700">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmRerecordEpisode}
              className="bg-orange-600 hover:bg-orange-700 text-white"
            >
              Lock Home & Re-record
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={showFreezeAlert}
        onOpenChange={(open) => {
          // Any dismissal (Esc, overlay click, button) also silences the buzzer,
          // so the alarm can never keep sounding with no visible way to stop it.
          if (!open) acknowledgeFreezeAlarm();
        }}
      >
        <AlertDialogContent className="bg-gray-900 border-red-600 text-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2 text-red-400">
              <AlertTriangle className="w-5 h-5" />
              Camera feed frozen — recording paused
            </AlertDialogTitle>
            <AlertDialogDescription className="text-gray-300 space-y-2">
              <span className="block">
                {frozenCameras.length > 0 ? (
                  <>
                    Stalled feed:{" "}
                    <span className="font-mono text-red-300">
                      {frozenCameras.join(", ")}
                    </span>
                  </>
                ) : (
                  "A camera feed stopped delivering new frames."
                )}
              </span>
              <span className="block">
                Recording auto-paused so no stale frames enter the dataset, and
                it will auto-resume by itself if the feed recovers. Silence the
                buzzer below, then check the camera in the Camera Setup area
                (cable, USB port, or the ROS camera bridge).
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction
              onClick={acknowledgeFreezeAlarm}
              className="bg-red-500 hover:bg-red-600 text-white"
            >
              <VolumeX className="w-4 h-4 mr-2" />
              Silence buzzer
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={showStopConfirm} onOpenChange={setShowStopConfirm}>
        <AlertDialogContent className="bg-gray-900 border-gray-700 text-white">
          <AlertDialogHeader>
            <AlertDialogTitle>Stop recording?</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-400">
              Saved episodes are kept. The session will end and you'll be taken to the upload page.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-gray-800 border-gray-700 text-white hover:bg-gray-700">
              Keep recording
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmStopRecording}
              className="bg-red-500 hover:bg-red-600 text-white"
            >
              Stop
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={showDiscardConfirm} onOpenChange={setShowDiscardConfirm}>
        <AlertDialogContent className="bg-gray-900 border-gray-700 text-white">
          <AlertDialogHeader>
            <AlertDialogTitle>Discard recording?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to discard the current ongoing episode and stop the session? 
              (If this is a brand new dataset, the dataset will be deleted. If you are resuming an existing dataset, previously saved episodes are kept safe.)
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-gray-800 border-gray-700 text-white hover:bg-gray-700">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDiscardRecording}
              className="bg-red-500 hover:bg-red-600 text-white"
            >
              Discard Session
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default Recording;
