import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";
import { ArrowLeft, AlertTriangle, Camera, Check, Move3d } from "lucide-react";
import { useApi } from "@/contexts/ApiContext";

interface IOConfig {
  ros_camera: boolean;
  include_ee_pose: boolean;
  ros_camera_running?: boolean;
  requires_restart?: boolean;
}

/**
 * I/O settings that oarm7dof_teleop.sh reads at launch.
 *
 * These are persisted to ~/.config/lelab/io_config.json rather than applied
 * live, because the launcher decides which bridges to start. So the page has to
 * be explicit that a change does nothing until teleop restarts — otherwise you
 * flip a switch, see no effect, and reasonably conclude it is broken.
 */
const IOConfigPage = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const { baseUrl, fetchWithHeaders } = useApi();

  const [cfg, setCfg] = useState<IOConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const load = async () => {
    try {
      const r = await fetchWithHeaders(`${baseUrl}/io-config`);
      setCfg(await r.json());
    } catch (e) {
      toast({ title: "Could not load I/O config", variant: "destructive" });
    }
  };

  useEffect(() => {
    load();
    // Poll so the live/persisted mismatch clears on its own once teleop is
    // restarted, instead of showing a stale "restart required" banner.
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  const save = async (changes: Partial<IOConfig>, toastArgs: { title: string; description: string }) => {
    setSaving(true);
    try {
      const r = await fetchWithHeaders(`${baseUrl}/io-config`, {
        method: "POST",
        body: JSON.stringify(changes),
      });
      const data = await r.json();
      setCfg(data);
      toast(toastArgs);
    } catch (e) {
      toast({ title: "Save failed", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  // Only the bridge toggle needs a restart, so only it arms the restart/OK
  // banner below.
  const saveRosCamera = (next: boolean) => {
    setDirty(true);
    return save(
      { ros_camera: next },
      {
        title: next ? "ROS camera bridge enabled" : "ROS camera bridge disabled",
        description: "Saved. Restart teleop for this to take effect.",
      },
    );
  };

  const saveIncludeEePose = (next: boolean) =>
    save(
      { include_ee_pose: next },
      {
        title: next
          ? "End-effector pose will be recorded"
          : "End-effector pose will not be recorded",
        description: "Applies to new datasets. Resumed datasets keep their own setting.",
      },
    );

  const rosCamera = cfg?.ros_camera ?? false;
  const includeEePose = cfg?.include_ee_pose ?? false;
  const running = cfg?.ros_camera_running ?? false;
  const needsRestart = cfg?.requires_restart ?? false;

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-3xl mx-auto">
        <Button
          onClick={() => navigate("/")}
          variant="ghost"
          className="mb-6 text-gray-300 hover:text-white"
        >
          <ArrowLeft className="w-4 h-4 mr-2" /> Back to Home
        </Button>

        <h1 className="text-3xl font-bold mb-2">I/O Configuration</h1>
        <p className="text-gray-400 mb-8">
          How observations are captured. Read by <code>oarm7dof_teleop.sh</code> at launch.
        </p>

        {needsRestart && (
          <div className="mb-6 p-4 rounded-xl border border-amber-500/60 bg-amber-500/10 flex gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
            <div className="text-sm">
              <div className="font-semibold text-amber-300">Restart required</div>
              <div className="text-amber-200/80 mt-1">
                Saved setting is <b>{rosCamera ? "enabled" : "disabled"}</b> but the
                bridge is currently <b>{running ? "running" : "not running"}</b>.
                Restart teleop to apply:
              </div>
              <pre className="mt-2 p-2 bg-black/40 rounded text-xs overflow-x-auto">
{`# Ctrl+C the running teleop, then:
./oarm7dof_teleop.sh --real --ws-port 19191 --lelab`}
              </pre>
            </div>
          </div>
        )}

        <div className="p-5 rounded-xl bg-gray-800/60 border border-gray-700">
          <div className="flex items-start justify-between gap-6">
            <div className="flex gap-3">
              <Camera className="w-5 h-5 text-gray-400 shrink-0 mt-1" />
              <div>
                <div className="font-semibold">ROS camera bridge</div>
                <div className="text-sm text-gray-400 mt-1 max-w-xl">
                  Publishes cameras as ROS <code>CompressedImage</code> topics. Off by
                  default: it JPEG-encodes every frame and leLab decodes it again,
                  while deployment reads the cameras directly with OpenCV and never
                  does that round trip — so recording through the bridge trains the
                  policy on compression artifacts and extra latency it will not see
                  at run time.
                </div>
                <div className="text-xs text-gray-500 mt-2">
                  V4L2 devices are exclusive: while this is on, direct capture
                  cannot open the same camera. Turn it on only if you need the ROS
                  topics (e.g. RViz).
                </div>
                <div className="text-xs mt-2">
                  <span className="text-gray-500">live: </span>
                  <span className={running ? "text-blue-400" : "text-gray-400"}>
                    {running ? "bridge running" : "direct capture"}
                  </span>
                </div>
              </div>
            </div>

            <Button
              onClick={() => saveRosCamera(!rosCamera)}
              disabled={saving || cfg === null}
              variant={rosCamera ? "default" : "secondary"}
              className={`w-28 shrink-0 ${
                rosCamera
                  ? "bg-blue-600 hover:bg-blue-500 text-white"
                  : "bg-gray-700 hover:bg-gray-600 text-gray-300"
              }`}
            >
              {rosCamera ? "Enabled" : "Disabled"}
            </Button>
          </div>
        </div>

        <div className="mt-4 p-5 rounded-xl bg-gray-800/60 border border-gray-700">
          <div className="flex items-start justify-between gap-6">
            <div className="flex gap-3">
              <Move3d className="w-5 h-5 text-gray-400 shrink-0 mt-1" />
              <div>
                <div className="font-semibold">End-effector pose in observations</div>
                <div className="text-sm text-gray-400 mt-1 max-w-xl">
                  Adds derived end-effector pose and gripper width as extra
                  observation dims (7 + 1 per arm). Off by default so a dataset
                  records only the raw joint positions — 8 observations matching
                  the 8 actions per arm, nothing derived.
                </div>
                <div className="text-xs text-gray-500 mt-2">
                  Applies to new datasets. Resuming an existing dataset keeps
                  whatever it was created with, regardless of this setting.
                </div>
              </div>
            </div>

            <Button
              onClick={() => saveIncludeEePose(!includeEePose)}
              disabled={saving || cfg === null}
              variant={includeEePose ? "default" : "secondary"}
              className={`w-28 shrink-0 ${
                includeEePose
                  ? "bg-blue-600 hover:bg-blue-500 text-white"
                  : "bg-gray-700 hover:bg-gray-600 text-gray-300"
              }`}
            >
              {includeEePose ? "Enabled" : "Disabled"}
            </Button>
          </div>
        </div>

        {dirty && !needsRestart && (
          <div className="mt-4 flex items-center gap-2 text-sm text-green-400">
            <Check className="w-4 h-4" /> Setting matches the running processes.
          </div>
        )}

        <div className="mt-8 text-xs text-gray-500">
          Persisted to <code>~/.config/lelab/io_config.json</code>. Override per-run
          with <code>--ros-camera</code> / <code>--no-ros-camera</code>, which take
          precedence over this page.
        </div>
      </div>
    </div>
  );
};

export default IOConfigPage;
