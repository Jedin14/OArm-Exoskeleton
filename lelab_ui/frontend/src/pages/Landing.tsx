import React, { useState, useRef, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";

import Footer from "@/components/Footer";
import RobotConfigManager from "@/components/landing/RobotConfigManager";
import RecordingModal from "@/components/landing/RecordingModal";
import DatasetPicker from "@/components/landing/DatasetPicker";
import JobsSection from "@/components/jobs/JobsSection";

import UsageInstructionsModal from "@/components/landing/UsageInstructionsModal";
import { useHfAuth } from "@/contexts/HfAuthContext";
import { useRobots } from "@/hooks/useRobots";
import { useDatasets } from "@/hooks/useDatasets";
import { DatasetItem } from "@/lib/replayApi";
import { CameraConfig } from "@/components/recording/CameraConfiguration";
import { isHostedSpace } from "@/lib/isHostedSpace";
import { useApi } from "@/contexts/ApiContext";

const ON_SPACE = isHostedSpace();

const Landing = () => {
  const [showUsageModal, setShowUsageModal] = useState(ON_SPACE);
  const { auth } = useHfAuth();

  const {
    selectedName,
    selectedRecord,
    availableNames,
    isLoading: isLoadingRobots,
    selectRobot,
    createRobot,
    deleteRobot,
  } = useRobots();

  const { datasets, loading: datasetsLoading } = useDatasets();

  // Recording modal state
  const [showRecordingModal, setShowRecordingModal] = useState(false);
  const [datasetName, setDatasetName] = useState("");
  const [singleTask, setSingleTask] = useState("");
  const [taskOptions, setTaskOptions] = useState<string[]>([]);
  const taskOptionsRef = useRef<string[]>([]);
  const [numEpisodes, setNumEpisodes] = useState(5);
  const [episodeTimeS, setEpisodeTimeS] = useState(60);
  const [resetTimeS, setResetTimeS] = useState(15);
  // Capture first and encode after each episode. Real-time encoding can
  // backpressure the 30 FPS camera/action loop and create choppy datasets.
  const [streamingEncoding, setStreamingEncoding] = useState(false);
  const [datasetVersion, setDatasetVersion] = useState("v3.0");
  const [armMode, setArmMode] = useState("both");
  const [includeEePose, setIncludeEePose] = useState(true);
  const [homePositionId, setHomePositionIdState] = useState<string>(
    () => localStorage.getItem("lelab_home_position_id") ?? ""
  );
  const setHomePositionId = (id: string) => {
    setHomePositionIdState(id);
    if (id) {
      localStorage.setItem("lelab_home_position_id", id);
    } else {
      localStorage.removeItem("lelab_home_position_id");
    }
  };
  const [cameras, setCameras] = useState<CameraConfig[]>([]);

  const releaseStreamsRef = useRef<(() => void) | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const { toast } = useToast();
  const { baseUrl, fetchWithHeaders } = useApi();

  const [isResume, setIsResume] = useState(false);
  const [resumeCameraNames, setResumeCameraNames] = useState<string[]>([]);

  useEffect(() => {
    const resumeRepoId = location.state?.resumeRepoId;
    if (resumeRepoId) {
      let name = resumeRepoId;
      if (auth.status === "authenticated" && auth.username && name.startsWith(`${auth.username}/`)) {
        name = name.substring(auth.username.length + 1);
      }
      setDatasetName(name);
      setIsResume(true);
      (async () => {
        try {
          const response = await fetchWithHeaders(`${baseUrl}/dataset-info`, {
            method: "POST",
            body: JSON.stringify({ dataset_repo_id: resumeRepoId }),
          });
          const data = await response.json();
          if (response.ok && data.success && Array.isArray(data.camera_names)) {
            setResumeCameraNames(data.camera_names);
            // These were fixed when the dataset was first created and must match
            // exactly when appending episodes — seed them so the modal can lock them.
            if (typeof data.arm_mode === "string" && data.arm_mode) {
              setArmMode(data.arm_mode);
            }
            if (typeof data.codebase_version === "string" && data.codebase_version) {
              setDatasetVersion(data.codebase_version);
            }
            if (typeof data.include_ee_pose === "boolean") {
              setIncludeEePose(data.include_ee_pose);
            }
            const existingTasks = Array.isArray(data.tasks)
              ? data.tasks.filter((t: unknown): t is string => typeof t === "string" && t.trim().length > 0)
              : [];
            setTaskOptions(existingTasks);
            if (existingTasks.length > 0) {
              setSingleTask(existingTasks[0]);
            } else if (typeof data.single_task === "string" && data.single_task.trim().length > 0) {
              setTaskOptions([data.single_task.trim()]);
              setSingleTask(data.single_task.trim());
            }
          } else {
            setResumeCameraNames([]);
          }
        } catch {
          setResumeCameraNames([]);
        } finally {
          // Let the modal open in the next tick after state updates
          setTimeout(() => openRecordingModal(), 100);
          navigate(location.pathname, { replace: true });
        }
      })();
    }
  }, [location.state, auth, navigate, baseUrl, fetchWithHeaders]);

  // Clear camera state and release streams when returning to landing page
  useEffect(() => {
    if (cameras.length > 0) {
      console.log(
        "🧹 Landing page: Cleaning up camera state from previous session",
      );
      if (releaseStreamsRef.current) {
        releaseStreamsRef.current();
      }
      setCameras([]);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (releaseStreamsRef.current) {
        console.log("🧹 Landing page: Cleaning up camera streams on unmount");
        releaseStreamsRef.current();
      }
    };
  }, []);

  const openRecordingModal = () => {
    setCameras(selectedRecord ? [...(selectedRecord.cameras ?? [])] : []);
    setShowRecordingModal(true);
  };

  const handleRecordingModalClose = (open: boolean) => {
    setShowRecordingModal(open);
    if (!open) {
      setIsResume(false);
      setResumeCameraNames([]);
      if (releaseStreamsRef.current) {
        console.log("🧹 Modal closed: Releasing camera streams");
        releaseStreamsRef.current();
      }
    }
  };

  const handleTrainingClick = () => navigate("/camera-setup");

  const openHubViewer = (repoId: string, isPrivate: boolean) => {
    const spacePath = `/spaces/lerobot/visualize_dataset?path=${encodeURIComponent(`/${repoId}`)}`;
    const target = isPrivate
      ? `https://huggingface.co/login?next=${encodeURIComponent(spacePath)}`
      : `https://huggingface.co${spacePath}`;
    window.open(target, "_blank", "noopener,noreferrer");
  };

  const handlePickExisting = (item: DatasetItem) => {
    if (item.source === "local" || item.source === "both") {
      navigate("/upload", {
        state: {
          datasetInfo: {
            dataset_repo_id: item.repo_id,
            source: item.source,
          },
        },
      });
      return;
    }
    openHubViewer(item.repo_id, item.private);
  };

  const handleOpenCustom = (repoId: string) => {
    // Custom-typed repo IDs are always treated as Hub paths. We don't know
    // privacy, so route through the login redirect to be safe.
    openHubViewer(repoId, true);
  };

  const handleCreateDataset = (name: string) => {
    setDatasetName(name);
    const initialTask = singleTask.trim();
    if (initialTask) {
      setTaskOptions((prev) =>
        prev.includes(initialTask) ? prev : [initialTask, ...prev]
      );
    }
    openRecordingModal();
  };

  const handleAddTaskOption = (task: string) => {
    const normalized = task.trim();
    if (!normalized) return;
    setTaskOptions((prev) =>
      prev.includes(normalized) ? prev : [...prev, normalized]
    );
    setSingleTask(normalized);
  };

  const handleDeleteTaskOption = (task: string) => {
    setTaskOptions((prev) => {
      const remaining = prev.filter((t) => t !== task);
      if (singleTask === task) {
        setSingleTask(remaining[0] ?? "");
      }
      return remaining;
    });
  };

  const handleStartRecording = async () => {
    if (!selectedRecord) {
      toast({
        title: "No robot selected",
        description: "Select or create a robot on the Landing page first.",
        variant: "destructive",
      });
      return;
    }
    const robot = selectedRecord;
    if (!robot.is_clean) {
      toast({
        title: "Robot not ready",
        description: `${robot.name} is missing a calibration. Configure it before recording.`,
        variant: "destructive",
      });
      return;
    }
    if (!datasetName || !singleTask) {
      toast({
        title: "Missing dataset details",
        description: "Please enter a dataset name and task description.",
        variant: "destructive",
      });
      return;
    }

    const datasetRepoId =
      auth.status === "authenticated"
        ? `${auth.username}/${datasetName}`
        : datasetName;

    if (cameras.length > 0 && releaseStreamsRef.current) {
      console.log("🔓 Releasing camera streams before starting recording...");
      toast({
        title: "Preparing Camera Resources",
        description: `Releasing ${cameras.length} camera stream(s) for recording...`,
      });
      releaseStreamsRef.current();
      await new Promise((resolve) => setTimeout(resolve, 1000));
      console.log("✅ Camera streams released, proceeding with recording...");
      toast({
        title: "Camera Resources Ready",
        description:
          "Camera streams released successfully. Starting recording...",
      });
    }

    const cameraDict = cameras.reduce(
      (acc, cam) => {
        acc[cam.name] = {
          type: cam.type,
          camera_index: cam.camera_index,
          width: cam.width,
          height: cam.height,
          fps: cam.fps,
        };
        return acc;
      },
      {} as Record<
        string,
        {
          type: string;
          camera_index?: number;
          width: number;
          height: number;
          fps?: number;
        }
      >,
    );

    const recordingConfig = {
      leader_port: robot.leader_port,
      follower_port: robot.follower_port,
      leader_config: robot.leader_config,
      follower_config: robot.follower_config,
      dataset_repo_id: datasetRepoId,
      single_task: singleTask,
      task_options: Array.from(
        new Set(
          [...taskOptionsRef.current, singleTask.trim()].filter(
            (t) => t.length > 0
          )
        )
      ),
      num_episodes: numEpisodes,
      episode_time_s: episodeTimeS,
      reset_time_s: resetTimeS,
      fps: 30,
      video: true,
      push_to_hub: false,
      resume: isResume,
      streaming_encoding: streamingEncoding,
      dataset_version: datasetVersion,
      arm_mode: armMode,
      home_position_id: homePositionId,
      include_ee_pose: includeEePose,
      robot_name: robot.name,
      cameras: cameraDict,
    };

    if (releaseStreamsRef.current) {
      console.log("🧹 Start recording: Releasing camera streams");
      releaseStreamsRef.current();
    }
    setShowRecordingModal(false);
    navigate("/recording", { state: { recordingConfig } });
  };

  useEffect(() => {
    taskOptionsRef.current = taskOptions;
  }, [taskOptions]);

  return (
    <div
      className="min-h-screen bg-black text-white pb-16"
      style={{ ["--lelab-topbar-h" as string]: "136px" }}
    >


      <div
        className="sticky z-20 bg-black/95 backdrop-blur supports-[backdrop-filter]:bg-black/70 border-b border-gray-800"
        style={{ top: "var(--lelab-topbar-h)" }}
      >
        <div className="mx-auto max-w-7xl px-4 py-4 grid gap-4 grid-cols-1 lg:grid-cols-[1.2fr_2fr]">
          <RobotConfigManager
            selectedName={selectedName}
            selectedRecord={selectedRecord}
            availableNames={availableNames}
            isLoading={isLoadingRobots}
            selectRobot={selectRobot}
            createRobot={createRobot}
            deleteRobot={deleteRobot}
          />
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-3 flex flex-col gap-2">
              <h3 className="font-semibold text-2xl text-left h-12 flex items-center">
                Dataset
              </h3>
              <DatasetPicker
                datasets={datasets}
                loading={datasetsLoading}
                onPickExisting={handlePickExisting}
                onOpenCustom={handleOpenCustom}
                onCreateNew={handleCreateDataset}
              >
                <Button
                  variant="outline"
                  role="combobox"
                  className="w-full justify-between bg-gray-800 border-gray-600 text-white hover:bg-gray-700 text-lg py-6"
                >
                  <span className="truncate text-gray-300">
                    {datasetsLoading
                      ? "Loading datasets…"
                      : "Select or create a dataset…"}
                  </span>
                  <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                </Button>
              </DatasetPicker>
            </div>
            
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-3 flex flex-col gap-2">
              <h3 className="font-semibold text-2xl text-left h-12 flex items-center">
                Home Positions
              </h3>
              <Button
                onClick={() => navigate("/arm-positions")}
                className="w-full bg-blue-600 hover:bg-blue-500 text-white text-lg py-6 shadow-md shadow-blue-500/20"
              >
                Manage Arm Poses
              </Button>
            </div>

            <div className="bg-gray-800 rounded-lg border border-gray-700 p-3 flex flex-col gap-2">
              <h3 className="font-semibold text-2xl text-left h-12 flex items-center">
                Camera Setup
              </h3>
              <Button
                onClick={handleTrainingClick}
                className="w-full bg-cyan-600 hover:bg-cyan-500 text-white text-lg py-6 shadow-md shadow-cyan-500/20"
              >
                Configure ROS Cameras
              </Button>
            </div>
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-7xl px-4 py-6">
        <JobsSection />
      </main>

      <Footer />

      <UsageInstructionsModal
        open={showUsageModal}
        onOpenChange={setShowUsageModal}
        dismissible={!ON_SPACE}
      />

      <RecordingModal
        open={showRecordingModal}
        onOpenChange={handleRecordingModalClose}
        robot={selectedRecord}
        datasetName={datasetName}
        setDatasetName={setDatasetName}
        singleTask={singleTask}
        setSingleTask={setSingleTask}
        numEpisodes={numEpisodes}
        setNumEpisodes={setNumEpisodes}
        episodeTimeS={episodeTimeS}
        setEpisodeTimeS={setEpisodeTimeS}
        resetTimeS={resetTimeS}
        setResetTimeS={setResetTimeS}
        streamingEncoding={streamingEncoding}
        setStreamingEncoding={setStreamingEncoding}
        datasetVersion={datasetVersion}
        setDatasetVersion={setDatasetVersion}
        cameras={cameras}
                setCameras={setCameras}
        taskOptions={taskOptions}
        onAddTaskOption={handleAddTaskOption}
        onDeleteTaskOption={handleDeleteTaskOption}
        onStart={handleStartRecording}
        releaseStreamsRef={releaseStreamsRef}
        isResume={isResume}
        previousCameraNames={resumeCameraNames}
        armMode={armMode}
        setArmMode={setArmMode}
        homePositionId={homePositionId}
        setHomePositionId={setHomePositionId}
        includeEePose={includeEePose}
        setIncludeEePose={setIncludeEePose}
      />
    </div>
  );
};

export default Landing;
