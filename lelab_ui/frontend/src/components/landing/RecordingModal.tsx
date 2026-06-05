import React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NumberInput } from "@/components/ui/number-input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertTriangle, CheckCircle, ChevronDown } from "lucide-react";
import CameraConfiguration, {
  CameraConfig,
} from "@/components/recording/CameraConfiguration";
import { useHfAuth } from "@/contexts/HfAuthContext";
import { RobotRecord } from "@/hooks/useRobots";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface RecordingModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  robot: RobotRecord | null;
  datasetName: string;
  setDatasetName: (value: string) => void;
  singleTask: string;
  setSingleTask: (value: string) => void;
  numEpisodes: number;
  setNumEpisodes: (value: number) => void;
  episodeTimeS: number;
  setEpisodeTimeS: (value: number) => void;
  resetTimeS: number;
  setResetTimeS: (value: number) => void;
  streamingEncoding: boolean;
  setStreamingEncoding: (value: boolean) => void;
  datasetVersion: string;
  setDatasetVersion: (value: string) => void;
  cameras: CameraConfig[];
  setCameras: (cameras: CameraConfig[]) => void;
  onStart: () => void;
  releaseStreamsRef?: React.MutableRefObject<(() => void) | null>;
  isResume?: boolean;
  previousCameraNames?: string[];
  taskOptions: string[];
  onAddTaskOption: (task: string) => void;
  onDeleteTaskOption: (task: string) => void;
}

const RecordingModal: React.FC<RecordingModalProps> = ({
  open,
  onOpenChange,
  robot,
  datasetName,
  setDatasetName,
  singleTask,
  setSingleTask,
  numEpisodes,
  setNumEpisodes,
  episodeTimeS,
  setEpisodeTimeS,
  resetTimeS,
  setResetTimeS,
  streamingEncoding,
  setStreamingEncoding,
  datasetVersion,
  setDatasetVersion,
  cameras,
  setCameras,
  onStart,
  releaseStreamsRef,
  isResume = false,
  previousCameraNames = [],
  taskOptions,
  onAddTaskOption,
  onDeleteTaskOption,
}) => {
  const { auth } = useHfAuth();

  const canStart = !!robot && robot.is_clean;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-gray-900 border-gray-800 text-white sm:max-w-[600px] p-8 max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex justify-center items-center mb-4">
            <div className="w-8 h-8 bg-red-500 rounded-full flex items-center justify-center">
              <span className="text-white font-bold text-sm">REC</span>
            </div>
          </div>
          <DialogTitle className="text-white text-center text-2xl font-bold">
            Configure Recording
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-6 py-4">
          <DialogDescription className="text-gray-400 text-base leading-relaxed text-center">
            Pick a configured robot and dataset parameters for recording.
          </DialogDescription>

          <div className="grid grid-cols-1 gap-6">
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-white border-b border-gray-700 pb-2">
                Robot Configuration
              </h3>
              {!robot ? (
                <Alert className="bg-amber-900/40 border-amber-700 text-amber-100">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    Select and configure a robot on the Landing page before
                    recording.
                  </AlertDescription>
                </Alert>
              ) : !robot.is_clean ? (
                <Alert className="bg-amber-900/40 border-amber-700 text-amber-100">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    <strong>{robot.name}</strong> is missing a calibration.
                    Configure it before recording.
                  </AlertDescription>
                </Alert>
              ) : (
                <div className="flex items-center gap-2 text-sm">
                  <CheckCircle className="w-4 h-4 text-green-400" />
                  <span className="text-slate-200">
                    Recording with <strong>{robot.name}</strong>
                  </span>
                </div>
              )}
            </div>

            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-white border-b border-gray-700 pb-2">
                Dataset Configuration
              </h3>
              {isResume && (
                <Alert className="bg-purple-950/40 border-purple-800 text-purple-100">
                  <CheckCircle className="h-4 w-4 text-purple-400" />
                  <AlertDescription>
                    <strong>Resuming Dataset:</strong> You are appending new episodes to the existing local dataset <strong>{datasetName}</strong>.
                  </AlertDescription>
                </Alert>
              )}
              <div className="grid grid-cols-1 gap-4">
                <div className="space-y-2">
                  <Label
                    htmlFor="datasetName"
                    className="text-sm font-medium text-gray-300"
                  >
                    Dataset Name *
                  </Label>
                  <Input
                    id="datasetName"
                    value={datasetName}
                    onChange={(e) =>
                      setDatasetName(
                        e.target.value.replace(/[^A-Za-z0-9._-]/g, "_")
                      )
                    }
                    placeholder="my_dataset"
                    disabled={isResume}
                    className="bg-gray-800 border-gray-700 text-white disabled:opacity-60 disabled:cursor-not-allowed"
                  />
                  <p className="text-xs text-gray-500">
                    Letters, numbers, <code>.</code> <code>_</code>{" "}
                    <code>-</code> only — other characters become{" "}
                    <code>_</code>.
                  </p>
                  {datasetName &&
                    (auth.status === "authenticated" ? (
                      <p className="text-xs text-gray-500">
                        Will be saved as{" "}
                        <span className="text-gray-300 font-mono">
                          {auth.username}/{datasetName}
                        </span>
                      </p>
                    ) : auth.status === "unauthenticated" ? (
                      <p className="text-xs text-amber-400/80">
                        Log in to Hugging Face to set the repository owner.
                      </p>
                    ) : null)}
                </div>
                <div className="space-y-2">
                  <Label
                    htmlFor="singleTask"
                    className="text-sm font-medium text-gray-300"
                  >
                    Task Description *
                  </Label>
                  <Select value={singleTask} onValueChange={setSingleTask}>
                    <SelectTrigger className="bg-gray-800 border-gray-700 text-white">
                      <SelectValue placeholder="Select task" />
                    </SelectTrigger>
                    <SelectContent className="bg-gray-800 border-gray-700">
                      {taskOptions.map((task) => (
                        <SelectItem key={task} value={task} className="text-white hover:bg-gray-700">
                          {task}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <div className="flex gap-2">
                    <Input
                      id="singleTask"
                      value={singleTask}
                      onChange={(e) => setSingleTask(e.target.value)}
                      placeholder="Type task and click Add"
                      className="bg-gray-800 border-gray-700 text-white"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      className="border-gray-600 text-gray-200 hover:bg-gray-800"
                      onClick={() => onAddTaskOption(singleTask)}
                    >
                      Add
                    </Button>
                  </div>
                  {taskOptions.length > 0 && (
                    <div className="space-y-2">
                      {taskOptions.map((task) => (
                        <div
                          key={task}
                          className="flex items-center justify-between rounded border border-gray-700 bg-gray-800/60 px-3 py-2"
                        >
                          <button
                            type="button"
                            onClick={() => setSingleTask(task)}
                            className="text-left text-sm text-gray-200 hover:text-white"
                          >
                            {task}
                          </button>
                          <div className="flex items-center gap-2">
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="border-gray-600 text-gray-200 hover:bg-gray-700"
                              onClick={() => onAddTaskOption(task)}
                            >
                              Add
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="border-red-600 text-red-300 hover:bg-red-900/30"
                              onClick={() => onDeleteTaskOption(task)}
                            >
                              Delete
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="space-y-2">
                  <Label
                    htmlFor="numEpisodes"
                    className="text-sm font-medium text-gray-300"
                  >
                    Number of Episodes
                  </Label>
                  <NumberInput
                    id="numEpisodes"
                    min="1"
                    max="100"
                    value={numEpisodes}
                    onChange={(v) => {
                      if (v !== undefined) setNumEpisodes(v);
                    }}
                    className="bg-gray-800 border-gray-700 text-white"
                  />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label
                      htmlFor="episodeTimeS"
                      className="text-sm font-medium text-gray-300"
                    >
                      Episode duration (seconds)
                    </Label>
                    <NumberInput
                      id="episodeTimeS"
                      min="1"
                      value={episodeTimeS}
                      onChange={(v) => {
                        if (v !== undefined) setEpisodeTimeS(v);
                      }}
                      className="bg-gray-800 border-gray-700 text-white"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label
                      htmlFor="resetTimeS"
                      className="text-sm font-medium text-gray-300"
                    >
                      Reset duration (seconds)
                    </Label>
                    <NumberInput
                      id="resetTimeS"
                      min="1"
                      value={resetTimeS}
                      onChange={(v) => {
                        if (v !== undefined) setResetTimeS(v);
                      }}
                      className="bg-gray-800 border-gray-700 text-white"
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label className="text-sm font-medium text-gray-300">
                    Dataset Format
                  </Label>
                  <Select value={datasetVersion} onValueChange={setDatasetVersion}>
                    <SelectTrigger className="bg-gray-800 border-gray-700 text-white w-full">
                      <SelectValue placeholder="Select format" />
                    </SelectTrigger>
                    <SelectContent className="bg-gray-800 border-gray-700">
                      <SelectItem value="v3.0" className="text-white hover:bg-gray-700">
                        v3.0 (New, chunked files)
                      </SelectItem>
                      <SelectItem value="v2.1" className="text-white hover:bg-gray-700">
                        v2.1 (Legacy, one file per episode)
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <CameraConfiguration
                cameras={cameras}
                onCamerasChange={setCameras}
                releaseStreamsRef={releaseStreamsRef}
                suggestedCameraNames={isResume ? previousCameraNames : []}
              />
            </div>

            <Collapsible className="space-y-4 group">
              <CollapsibleTrigger className="flex items-center justify-between w-full text-lg font-semibold text-white border-b border-gray-700 pb-2">
                <span>Advanced Parameters</span>
                <ChevronDown className="w-4 h-4 transition-transform group-data-[state=open]:rotate-180" />
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-3">
                <div className="flex items-start gap-3">
                  <Checkbox
                    id="streamingEncoding"
                    checked={streamingEncoding}
                    onCheckedChange={(value) =>
                      setStreamingEncoding(value === true)
                    }
                    className="mt-0.5 border-gray-500 data-[state=checked]:bg-red-500 data-[state=checked]:border-red-500"
                  />
                  <div className="space-y-1">
                    <Label
                      htmlFor="streamingEncoding"
                      className="text-sm font-medium text-gray-200 cursor-pointer"
                    >
                      Streaming video encoding
                    </Label>
                    <p className="text-xs text-gray-500">
                      Encodes frames in real time during capture so each
                      episode saves almost instantly. Uncheck to fall back to
                      the slower PNG-then-encode flow.
                    </p>
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>
          </div>

          <div className="flex flex-col sm:flex-row gap-4 justify-center pt-4">
            <Button
              onClick={onStart}
              disabled={!canStart}
              className="w-full sm:w-auto bg-red-500 hover:bg-red-600 text-white px-10 py-6 text-lg transition-all shadow-md shadow-red-500/30 hover:shadow-lg hover:shadow-red-500/40 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Start Recording
            </Button>
            <Button
              onClick={() => onOpenChange(false)}
              variant="outline"
              className="w-full sm:w-auto border-gray-500 hover:border-gray-200 px-10 py-6 text-lg text-zinc-500 bg-zinc-900 hover:bg-zinc-800"
            >
              Cancel
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default RecordingModal;
