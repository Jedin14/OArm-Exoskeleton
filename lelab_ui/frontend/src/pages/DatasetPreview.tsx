import React, { useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Film, ShieldCheck, Loader2, CheckCircle, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";
import { useApi } from "@/contexts/ApiContext";

interface PreviewState {
  datasetRepoId: string;
  previewMode?: "all" | "episode";
  previewEpisodeIndex?: number;
}

interface PreviewInfo {
  dataset_repo_id: string;
  codebase_version?: string;
  camera_names: string[];
  available_episode_indices: number[];
  episodes_per_camera: Record<string, number>;
}

interface ValidationIssue {
  label: string;
  kind: string;
  message: string;
  camera?: string;
}

interface ValidationResult {
  success: boolean;
  message?: string;
  ok?: boolean;
  checked_units?: number;
  camera_names?: string[];
  summary?: Record<string, number>;
  issues?: ValidationIssue[];
  issues_truncated?: boolean;
  units_truncated?: boolean;
}

const ValidationReport = ({ result }: { result: ValidationResult }) => {
  if (result.ok) {
    return (
      <div className="bg-green-900/20 border border-green-800 rounded-xl p-4 flex items-center justify-between text-green-400">
        <div className="flex items-center gap-3">
          <CheckCircle className="w-5 h-5" />
          <p className="font-medium">All data and video files validated successfully.</p>
        </div>
        <span className="text-sm opacity-80">Checked {result.checked_units} units</span>
      </div>
    );
  }

  return (
    <div className="bg-red-900/20 border border-red-800 rounded-xl p-4 space-y-4">
      <div className="flex items-center gap-3 text-red-400">
        <AlertTriangle className="w-5 h-5" />
        <h3 className="font-semibold text-lg">Validation Issues Found</h3>
      </div>
      
      {result.summary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          {Object.entries(result.summary).map(([key, count]) => (
            <div key={key} className="bg-red-950/30 p-2 rounded-lg border border-red-900/50">
              <p className="text-red-400/80 capitalize">{key.replace(/_/g, " ")}</p>
              <p className="text-red-200 font-semibold text-xl">{count}</p>
            </div>
          ))}
        </div>
      )}

      {result.issues && result.issues.length > 0 && (
        <div className="mt-4">
          <h4 className="text-red-300 font-medium mb-2 text-sm">Detailed Report:</h4>
          <div className="max-h-60 overflow-y-auto space-y-2 pr-2 custom-scrollbar">
            {result.issues.map((issue, idx) => (
              <div key={idx} className="bg-red-950/50 p-3 rounded-lg text-sm border border-red-900/30">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-red-300">{issue.label}</span>
                  {issue.camera && (
                    <span className="text-xs bg-red-900/40 px-2 py-0.5 rounded text-red-300">
                      Camera: {issue.camera}
                    </span>
                  )}
                </div>
                <p className="text-red-200/80">{issue.message}</p>
              </div>
            ))}
          </div>
          {result.issues_truncated && (
            <p className="text-xs text-red-400/60 mt-2 italic">
              Showing first 100 issues...
            </p>
          )}
        </div>
      )}
    </div>
  );
};

const DatasetPreview = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { toast } = useToast();
  const { baseUrl, fetchWithHeaders } = useApi();
  const state = location.state as PreviewState | undefined;
  const datasetRepoId =
    searchParams.get("dataset") || state?.datasetRepoId || "";
  const requestedEpisode = Number.parseInt(
    searchParams.get("episode") ?? String(state?.previewEpisodeIndex ?? 0),
    10,
  );

  const [info, setInfo] = useState<PreviewInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedEpisode, setSelectedEpisode] = useState<number>(
    Number.isNaN(requestedEpisode) ? 0 : requestedEpisode,
  );
  const [isValidating, setIsValidating] = useState(false);
  const [validation, setValidation] = useState<ValidationResult | null>(null);

  const handleValidate = async () => {
    if (!datasetRepoId) return;
    setIsValidating(true);
    setValidation(null);
    try {
      const response = await fetchWithHeaders(`${baseUrl}/validate-dataset`, {
        method: "POST",
        body: JSON.stringify({ dataset_repo_id: datasetRepoId }),
      });
      const data: ValidationResult = await response.json();
      if (!response.ok || !data.success) {
        toast({
          title: "Validation failed",
          description: data.message || "Could not validate this dataset.",
          variant: "destructive",
        });
        return;
      }
      setValidation(data);
      toast({
        title: data.ok ? "Dataset is valid" : "Problems found",
        description: data.ok
          ? "Video and data match across all files."
          : "See the validation report for details.",
        variant: data.ok ? undefined : "destructive",
      });
    } catch (error) {
      toast({
        title: "Connection Error",
        description: "Could not connect to the backend server.",
        variant: "destructive",
      });
    } finally {
      setIsValidating(false);
    }
  };

  useEffect(() => {
    const load = async () => {
      if (!datasetRepoId) {
        toast({
          title: "Missing dataset",
          description: "No dataset selected for preview.",
          variant: "destructive",
        });
        navigate("/");
        return;
      }
      try {
        const response = await fetchWithHeaders(`${baseUrl}/dataset-preview-info`, {
          method: "POST",
          body: JSON.stringify({ dataset_repo_id: datasetRepoId }),
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
          throw new Error(data.message || "Could not load local dataset preview.");
        }
        setInfo(data);
        const available = Array.isArray(data.available_episode_indices)
          ? data.available_episode_indices
          : [];
        if (available.length > 0 && !available.includes(Number.isNaN(requestedEpisode) ? 0 : requestedEpisode)) {
          setSelectedEpisode(available[0]);
        }
      } catch (error) {
        toast({
          title: "Preview unavailable",
          description:
            error instanceof Error ? error.message : "Could not load preview videos.",
          variant: "destructive",
        });
        navigate("/");
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [baseUrl, datasetRepoId, fetchWithHeaders, navigate, requestedEpisode, toast]);

  useEffect(() => {
    if (!datasetRepoId) return;
    const next = new URLSearchParams(location.search);
    next.set("dataset", datasetRepoId);
    next.set("episode", String(selectedEpisode));
    if (!next.get("mode")) {
      next.set("mode", "all");
    }
    setSearchParams(next, { replace: true });
  }, [datasetRepoId, location.search, selectedEpisode, setSearchParams]);

  if (isLoading) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4" />
          <p className="text-lg">Loading local preview...</p>
        </div>
      </div>
    );
  }

  if (!info) {
    return null;
  }

  const videoUrl = (cameraName: string) =>
    `${baseUrl}/dataset-video?dataset_repo_id=${encodeURIComponent(
      info.dataset_repo_id,
    )}&camera_name=${encodeURIComponent(cameraName)}&episode_index=${selectedEpisode}`;

  const availableEpisodes = info.available_episode_indices || [];

  return (
    <div className="min-h-screen bg-black text-white p-6 sm:p-8">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <Button
              onClick={() => navigate(-1)}
              variant="outline"
              className="border-gray-600 text-gray-200 hover:bg-gray-800"
            >
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back
            </Button>
            <div>
              <h1 className="text-2xl font-bold">Local Dataset Preview</h1>
              <p className="text-sm text-gray-400 font-mono break-all flex items-center gap-2">
                {info.dataset_repo_id}
                {info.codebase_version && (
                  <span className="bg-gray-800 border border-gray-600 px-2 py-0.5 rounded text-xs font-semibold text-gray-300">
                    {info.codebase_version}
                  </span>
                )}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Button
              onClick={handleValidate}
              disabled={isValidating}
              className="bg-indigo-600 hover:bg-indigo-700 text-white"
            >
              {isValidating ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Validating…
                </>
              ) : (
                <>
                  <ShieldCheck className="w-4 h-4 mr-2" />
                  Validate Dataset
                </>
              )}
            </Button>
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <Film className="w-4 h-4" />
              {info.camera_names.length} camera views
            </div>
          </div>
        </div>

        {isValidating && (
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-4 flex items-center gap-3 text-sm text-gray-300">
            <Loader2 className="w-5 h-5 animate-spin text-indigo-400" />
            Decoding videos and comparing frame counts to logged data. This can
            take a while for large datasets…
          </div>
        )}

        {validation && !isValidating && (
          <ValidationReport result={validation} />
        )}

        <div className="bg-gray-900 border border-gray-700 rounded-xl p-4 space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm text-gray-400">Episode</p>
              <p className="text-lg font-semibold">Episode {selectedEpisode}</p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                className="border-gray-600 text-gray-200 hover:bg-gray-800"
                onClick={() => setSelectedEpisode((prev) => Math.max(0, prev - 1))}
                disabled={selectedEpisode <= 0}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                className="border-gray-600 text-gray-200 hover:bg-gray-800"
                onClick={() =>
                  setSelectedEpisode((prev) =>
                    Math.min(availableEpisodes.length - 1, prev + 1),
                  )
                }
                disabled={selectedEpisode >= availableEpisodes.length - 1}
              >
                Next
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {availableEpisodes.map((episodeIndex) => (
              <Button
                key={episodeIndex}
                variant={episodeIndex === selectedEpisode ? "default" : "outline"}
                className={
                  episodeIndex === selectedEpisode
                    ? "bg-blue-600 hover:bg-blue-700 text-white"
                    : "border-gray-700 text-gray-300 hover:bg-gray-800"
                }
                onClick={() => setSelectedEpisode(episodeIndex)}
              >
                {episodeIndex}
              </Button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {info.camera_names.map((cameraName) => {
            const availableCount = info.episodes_per_camera[cameraName] ?? 0;
            const hasVideo = selectedEpisode < availableCount;
            return (
              <div
                key={cameraName}
                className="bg-gray-900 border border-gray-700 rounded-xl p-4 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-semibold">{cameraName}</h2>
                  <span className="text-xs text-gray-400">
                    {availableCount} episode videos
                  </span>
                </div>
                {hasVideo ? (
                  <video
                    key={`${cameraName}-${selectedEpisode}`}
                    src={videoUrl(cameraName)}
                    controls
                    preload="metadata"
                    className="w-full rounded-lg bg-black"
                  />
                ) : (
                  <div className="h-48 rounded-lg border border-dashed border-gray-700 flex items-center justify-center text-sm text-gray-500">
                    No video for episode {selectedEpisode}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default DatasetPreview;
