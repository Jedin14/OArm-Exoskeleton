import React, { useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Film } from "lucide-react";
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
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Film className="w-4 h-4" />
            {info.camera_names.length} camera views
          </div>
        </div>

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
