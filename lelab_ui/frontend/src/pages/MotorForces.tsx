import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";
import { useApi } from "@/contexts/ApiContext";
import {
  ArrowLeft,
  Activity,
  AlertTriangle,
  Lock,
  Unlock,
  RefreshCw,
} from "lucide-react";

interface Motor {
  joint: string;
  can_id: number;
  torque_nm: number | null;
  t_max_nm: number | null;
  position_rad?: number;
  velocity_rad_s?: number;
  t_rotor_c?: number;
  stale: boolean;
  age_ms: number | null;
}

interface ArmData {
  channel: string;
  motors: Motor[];
  frames_decoded?: number;
  error?: string;
}

interface CapReport {
  cap_nm: number;
  peak_nm: number | null;
  enforced: boolean | null;
}

interface BridgeCapState {
  seen: boolean;
  stale?: boolean;
  age_s?: number;
  cap_nm?: Record<string, number | null>;
  hold_m?: Record<string, number | null>;
  torque_nm?: Record<string, number | null>;
}

interface TorqueData {
  arms: Record<string, ArmData>;
  units: string;
  gripper_limits_m: Record<string, number>;
  gripper_torque_limits_nm: Record<string, number>;
  cap_enforcement?: Record<string, CapReport>;
  bridge_cap_state?: BridgeCapState;
  default_gripper_torque_cap_nm?: number;
}

const JOINT_LABEL: Record<string, string> = {
  joint1: "J1",
  joint2: "J2",
  joint3: "J3",
  joint4: "J4",
  joint5: "J5",
  joint6: "J6",
  joint7: "J7",
  finger: "Gripper",
};

const ARM_LABEL: Record<string, string> = { left: "Left Arm", right: "Right Arm" };

/**
 * Live motor torque, in Nm straight from each motor's own feedback frame.
 *
 * Deliberately NOT labelled "force in Newtons": converting joint torque to a
 * fingertip force needs a lever-arm/transmission constant that does not exist in
 * this repo, so the honest reading is the one the hardware actually reports.
 * `t_max` comes from the motor type (DM8009 54Nm / DM4340 28Nm / DM4310 10Nm),
 * so "% of rated" is derived rather than hard-coded.
 */
const MotorForces: React.FC<{ isModal?: boolean; onClose?: () => void }> = ({
  isModal = false,
}) => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();

  const [data, setData] = useState<TorqueData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busySide, setBusySide] = useState<string | null>(null);
  // Per-side draft value for the Nm input, so typing does not fight the 5Hz poll.
  const [draftCaps, setDraftCaps] = useState<Record<string, string>>({});
  // Seconds left before the arms start following the exoskeleton, or null once
  // that has happened (or been cancelled by leaving).
  const [countdown, setCountdown] = useState<number | null>(null);
  const [armsLive, setArmsLive] = useState(false);
  // Peak |torque| seen per motor this session, so a brief spike stays visible
  // after the live value drops back — a 30ms squeeze is otherwise unreadable.
  const peaks = useRef<Record<string, number>>({});

  const load = useCallback(async () => {
    try {
      const res = await fetchWithHeaders(`${baseUrl}/motor-torques`);
      const body = (await res.json()) as TorqueData;
      if (!res.ok) {
        setError((body as unknown as { detail?: string }).detail || "Could not read motor torques");
        return;
      }
      setError(null);
      setData(body);
      Object.entries(body.arms || {}).forEach(([side, arm]) =>
        (arm.motors || []).forEach((m) => {
          if (m.torque_nm == null || m.stale) return;
          const key = `${side}.${m.joint}`;
          peaks.current[key] = Math.max(peaks.current[key] ?? 0, Math.abs(m.torque_nm));
        })
      );
    } catch {
      setError("Could not reach the backend");
    }
  }, [baseUrl, fetchWithHeaders]);

  useEffect(() => {
    load();
    // 5 Hz: fast enough to see a squeeze build, slow enough to stay cheap. The
    // motors themselves report at ~203 Hz; this is a display, not a control loop.
    const id = setInterval(load, 200);
    return () => clearInterval(id);
  }, [load]);

  // The exoskeleton drives the arms only while this page is open, and they return
  // to the pose they were in when it opened as soon as it closes. Squeezing to
  // read a force means the arms have to be live, and leaving them live after
  // navigating away is exactly the surprise worth avoiding.
  //
  // Both calls are no-ops during a recording: the recorder owns lock state then,
  // and a second owner fighting it pins an arm mid-episode.
  useEffect(() => {
    let entered = false;
    let cancelled = false;
    const enter = async () => {
      try {
        const res = await fetchWithHeaders(`${baseUrl}/force-page/enter`, { method: "POST" });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          toast({
            title: "Arms not released",
            description: body.detail || "Squeezing will not move the arms.",
            variant: "destructive",
          });
          return;
        }
        entered = true;
        setArmsLive(body.applied !== false);
        if (body.applied === false && body.message) {
          toast({ title: "Arms already under recorder control", description: body.message });
        }
      } catch {
        toast({ title: "Could not reach the backend", variant: "destructive" });
      }
    };

    // Three-second countdown BEFORE the arms start following the exoskeleton.
    // The arms move the instant this page takes control, so opening it must never
    // be the same moment they go live -- the warning is the point, not decoration.
    setCountdown(3);
    const tick = setInterval(() => {
      setCountdown((c) => {
        if (c === null) return null;
        if (c <= 1) {
          clearInterval(tick);
          if (!cancelled) enter();
          return null;
        }
        return c - 1;
      });
    }, 1000);

    const leave = () => {
      if (!entered) return;
      // keepalive so the request still lands if this fires during a page unload.
      fetchWithHeaders(`${baseUrl}/force-page/exit`, { method: "POST", keepalive: true }).catch(
        () => undefined
      );
    };
    // Covers closing the tab / hard refresh, which unmount alone does not.
    window.addEventListener("beforeunload", leave);
    return () => {
      cancelled = true;          // leaving during the countdown: never go live
      clearInterval(tick);
      window.removeEventListener("beforeunload", leave);
      setArmsLive(false);
      leave();
    };
  }, [baseUrl, fetchWithHeaders, toast]);

  const setLimit = async (side: string, torqueNm: number) => {
    setBusySide(side);
    try {
      const res = await fetchWithHeaders(`${baseUrl}/gripper-limit`, {
        method: "POST",
        body: JSON.stringify({ side, torque_nm: torqueNm }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast({
          title: "Could not set the limit",
          description: body.detail || "See the lelab log for details.",
          variant: "destructive",
        });
        return;
      }
      toast({
        title: `${ARM_LABEL[side]} gripper capped at ${torqueNm.toFixed(2)} Nm`,
        description: "It will stop closing once it reaches that torque.",
      });
      await load();
    } finally {
      setBusySide(null);
    }
  };

  const clearLimit = async (side: string) => {
    setBusySide(side);
    try {
      const res = await fetchWithHeaders(`${baseUrl}/gripper-limit/${side}`, { method: "DELETE" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast({
          title: "Could not release the limit",
          description: body.detail || "See the lelab log for details.",
          variant: "destructive",
        });
        return;
      }
      toast({ title: `${ARM_LABEL[side]} gripper limit released` });
      await load();
    } finally {
      setBusySide(null);
    }
  };

  const limits = data?.gripper_limits_m || {};
  const torqueLimits = data?.gripper_torque_limits_nm || {};
  const capReport = data?.cap_enforcement || {};
  const bridge = data?.bridge_cap_state;
  const sides = Object.keys(data?.arms || {}).sort();

  return (
    <div className={isModal ? "bg-black text-white" : "min-h-screen bg-black text-white"}>
      <div
        className={
          isModal
            ? "bg-black/95 border-b border-gray-800 px-6 py-4"
            : "sticky z-10 top-0 bg-black/95 backdrop-blur border-b border-gray-800 px-6 py-4"
        }
      >
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            {!isModal && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => (window.location.href = "/")}
                className="text-gray-400 hover:text-white"
              >
                <ArrowLeft className="w-4 h-4 mr-1" /> Back
              </Button>
            )}
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-600 flex items-center justify-center shadow-lg shadow-violet-500/20">
                <Activity className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-white">Motor Forces</h1>
                <p className="text-xs text-gray-400">
                  Measured torque per motor, in Nm, read straight off the CAN bus
                </p>
              </div>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              peaks.current = {};
              load();
            }}
            className="border-gray-700 text-gray-400 hover:text-white hover:border-gray-500 hover:bg-gray-800"
          >
            <RefreshCw className="w-4 h-4 mr-2" /> Reset peaks
          </Button>
        </div>
      </div>

      {countdown !== null && (
        <div className="px-6 py-4 border-b bg-amber-950/60 border-amber-700">
          <div className="max-w-7xl mx-auto flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-300 shrink-0" />
            <div className="text-sm">
              <div className="font-semibold text-amber-200">
                Arms go live in {countdown}…
              </div>
              <div className="text-amber-200/80">
                The exoskeleton will start driving the arms. Stand clear and hold the
                suit steady. Leave this page to cancel.
              </div>
            </div>
          </div>
        </div>
      )}

      {armsLive && countdown === null && (
        <div className="px-6 py-2 border-b bg-emerald-950/40 border-emerald-800/50">
          <div className="max-w-7xl mx-auto flex items-center gap-2 text-xs text-emerald-300">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            Arms are following the exoskeleton. They return to their previous pose and
            stop listening when you leave this page.
          </div>
        </div>
      )}

      {/* The bridge is not reporting at all: either it predates the force-cap
          code or it is not running. Either way nothing is enforcing the cap, and
          this is a FACT from the bridge's silence rather than an inference. */}
      {bridge && (!bridge.seen || bridge.stale) && (
        <div className="px-6 py-3 border-b bg-red-950/50 border-red-800/60">
          <div className="max-w-7xl mx-auto flex items-start gap-2 text-sm text-red-300">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>
              The exoskeleton bridge is not reporting any force-cap state
              {bridge.stale ? ` (last heard ${bridge.age_s}s ago)` : ""} — so the cap
              is <b>not being enforced</b>. It is running code from before the cap
              existed, or it is not running. Restart it, then look for{" "}
              <span className="font-mono text-xs">gripper torque caps loaded from …</span>{" "}
              in its log.
            </span>
          </div>
        </div>
      )}

      {/* The bridge IS reporting. Now the two failure modes are distinguishable. */}
      {bridge?.seen && !bridge.stale &&
        Object.entries(capReport).map(([side, r]) => {
          const armed = bridge.cap_nm?.[side];
          const torqueSeen = bridge.torque_nm?.[side];
          if (armed == null) {
            return (
              <div key={side} className="px-6 py-3 border-b bg-red-950/50 border-red-800/60">
                <div className="max-w-7xl mx-auto flex items-center gap-2 text-sm text-red-300">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>
                    {ARM_LABEL[side] || side}: the bridge has <b>no cap armed</b>, so
                    nothing is limiting the gripper. Press Limit Force to arm it.
                  </span>
                </div>
              </div>
            );
          }
          if (torqueSeen == null) {
            return (
              <div key={side} className="px-6 py-3 border-b bg-amber-950/50 border-amber-800/60">
                <div className="max-w-7xl mx-auto flex items-center gap-2 text-sm text-amber-300">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>
                    {ARM_LABEL[side] || side}: cap armed at{" "}
                    <span className="font-mono">{armed.toFixed(2)} Nm</span> but the
                    bridge <b>cannot read gripper torque</b>, so it cannot engage. Check
                    its log for “no gripper torque on can…”.
                  </span>
                </div>
              </div>
            );
          }
          if (r.enforced === false) {
            return (
              <div key={side} className="px-6 py-3 border-b bg-amber-950/50 border-amber-800/60">
                <div className="max-w-7xl mx-auto flex items-start gap-2 text-sm text-amber-300">
                  <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                  <span>
                    {ARM_LABEL[side] || side} peaked at{" "}
                    <span className="font-mono font-bold">{r.peak_nm} Nm</span> with the
                    cap armed at <span className="font-mono">{armed.toFixed(2)} Nm</span>.
                    The clamp is running but the gripper outran it — closing speed is
                    what bounds overshoot. Lower{" "}
                    <span className="font-mono text-xs">gripper_capped_close_speed_m_s</span>{" "}
                    (currently 0.15 m/s) and Reset peaks.
                  </span>
                </div>
              </div>
            );
          }
          return null;
        })}

      {error && (
        <div className="px-6 py-3 border-b bg-red-950/40 border-red-800/60">
          <div className="max-w-7xl mx-auto flex items-center gap-2 text-sm text-red-300">
            <AlertTriangle className="w-4 h-4" />
            {error}
          </div>
        </div>
      )}

      <div className="max-w-7xl mx-auto px-6 py-8 space-y-8">
        {sides.length === 0 && !error && (
          <div className="text-sm text-gray-500">Waiting for motor feedback…</div>
        )}

        {sides.map((side) => {
          const arm = data!.arms[side];
          const limit = limits[side];
          const cap = torqueLimits[side];
          const gripper = (arm.motors || []).find((m) => m.joint === "finger");
          return (
            <div key={side} className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-white">
                    {ARM_LABEL[side] || side}
                  </h2>
                  <p className="text-xs text-gray-500 font-mono">{arm.channel}</p>
                </div>
                {gripper && (
                  <div className="flex items-center gap-3">
                    {cap != null && (
                      <span className="text-xs px-2.5 py-1 rounded-full bg-amber-950/60 text-amber-300 border border-amber-900/60 font-medium">
                        Capped at {cap.toFixed(2)} Nm
                        {limit != null && ` · holding ${(limit * 1000).toFixed(1)} mm`}
                      </span>
                    )}
                    {cap == null ? (
                      <>
                        {/* Prefilled with the live torque so "limit it here" is one
                            click while you are actually squeezing the object. */}
                        <div className="flex items-center gap-1.5">
                          <input
                            type="number"
                            step="0.05"
                            min="0.05"
                            max={gripper.t_max_nm ?? 10}
                            value={
                              draftCaps[side] ??
                              (gripper.torque_nm != null ? Math.abs(gripper.torque_nm).toFixed(2) : "")
                            }
                            onChange={(e) =>
                              setDraftCaps((prev) => ({ ...prev, [side]: e.target.value }))
                            }
                            className="w-20 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-amber-500"
                          />
                          <span className="text-xs text-gray-500">Nm</span>
                        </div>
                        <Button
                          onClick={() => {
                            const raw =
                              draftCaps[side] ??
                              (gripper.torque_nm != null
                                ? Math.abs(gripper.torque_nm).toFixed(2)
                                : "");
                            const value = parseFloat(raw);
                            if (!Number.isFinite(value) || value <= 0) {
                              toast({
                                title: "Enter a torque above 0 Nm",
                                variant: "destructive",
                              });
                              return;
                            }
                            setLimit(side, value);
                          }}
                          disabled={busySide === side || !!arm.error || gripper.stale}
                          className="bg-amber-600 hover:bg-amber-500 text-white font-semibold"
                        >
                          <Lock className="w-4 h-4 mr-2" /> Limit Force
                        </Button>
                      </>
                    ) : (
                      <Button
                        onClick={() => clearLimit(side)}
                        disabled={busySide === side}
                        variant="secondary"
                        className="bg-gray-700 hover:bg-gray-600 text-gray-100 font-semibold"
                      >
                        <Unlock className="w-4 h-4 mr-2" /> Release Limit
                      </Button>
                    )}
                  </div>
                )}
              </div>

              {arm.error ? (
                <div className="bg-gray-900/40 border border-gray-800 border-dashed rounded-2xl p-6 text-sm text-gray-500">
                  No CAN feedback on <span className="font-mono">{arm.channel}</span> — {arm.error}
                </div>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
                  {(arm.motors || []).map((m) => {
                    const key = `${side}.${m.joint}`;
                    const peak = peaks.current[key] ?? 0;
                    const tMax = m.t_max_nm || 1;
                    const mag = m.torque_nm == null ? 0 : Math.abs(m.torque_nm);
                    const pct = Math.min(100, (mag / tMax) * 100);
                    const hot = pct >= 80;
                    const warm = pct >= 50 && pct < 80;
                    return (
                      <div
                        key={m.joint}
                        className={`rounded-xl border p-3 space-y-2 ${
                          m.stale
                            ? "bg-gray-900/40 border-gray-800"
                            : hot
                            ? "bg-red-950/30 border-red-800/60"
                            : warm
                            ? "bg-amber-950/30 border-amber-800/50"
                            : "bg-gray-900/60 border-gray-800"
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-semibold text-white">
                            {JOINT_LABEL[m.joint] || m.joint}
                          </span>
                          <span className="text-[10px] text-gray-500 font-mono">
                            {m.t_max_nm}Nm
                          </span>
                        </div>
                        {m.stale ? (
                          <div className="text-xs text-gray-500">no data</div>
                        ) : (
                          <>
                            <div
                              className={`text-lg font-bold font-mono ${
                                hot ? "text-red-300" : warm ? "text-amber-300" : "text-gray-100"
                              }`}
                            >
                              {m.torque_nm?.toFixed(2)}
                            </div>
                            <div className="h-1.5 w-full bg-gray-800 rounded-full overflow-hidden">
                              <div
                                className={`h-full rounded-full ${
                                  hot ? "bg-red-500" : warm ? "bg-amber-500" : "bg-violet-500"
                                }`}
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                            <div className="text-[10px] text-gray-500 font-mono">
                              peak {peak.toFixed(2)}
                            </div>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}

        <div className="bg-blue-950/30 border border-blue-900/40 rounded-xl p-4 space-y-1.5">
          <h4 className="text-xs font-semibold text-blue-300 uppercase tracking-wide">
            How the gripper limit works
          </h4>
          <ul className="text-xs text-gray-400 space-y-1">
            <li>• Type the maximum torque you want (it prefills with the live reading) and press Limit</li>
            <li>• The gripper closes normally until it reaches that torque, then stops squeezing harder</li>
            <li>• Remove the object and it closes again — the hold follows the gripper down
              whenever measured torque is below the cap</li>
            <li>• Closing force is position error × a hardware-fixed gain, so the cap is enforced by
              holding the aperture; there is no effort register to lower at runtime</li>
            <li>• The clamped aperture is what gets recorded as <span className="font-mono">action</span>,
              so the dataset matches what the hardware was actually told to do</li>
            <li>• Release restores the full range. A teleop restart also clears it</li>
          </ul>
        </div>
      </div>
    </div>
  );
};

export default MotorForces;
