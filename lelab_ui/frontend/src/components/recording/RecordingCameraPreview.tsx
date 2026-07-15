import React, { useEffect, useState, useRef } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import { useApi } from '@/contexts/ApiContext';

interface RecordingCameraPreviewProps {
  cameras: string[];
}

export const RecordingCameraPreview: React.FC<RecordingCameraPreviewProps> = ({ cameras }) => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const [isRefreshing, setIsRefreshing] = useState(false);
  // Name of the camera currently enlarged inline, or null for the normal grid.
  const [expandedCam, setExpandedCam] = useState<string | null>(null);
  const [systemDevices, setSystemDevices] = useState<{id: string, name: string}[]>([]);

  useEffect(() => {
    // Fetch available system devices once on mount
    const fetchDevices = async () => {
      try {
        const res = await fetchWithHeaders(`${baseUrl}/system-video-devices`);
        if (res.ok) {
          const data = await res.json();
          setSystemDevices(data.devices || []);
        }
      } catch (e) {
        console.error("Failed to fetch system devices", e);
      }
    };
    fetchDevices();
  }, [baseUrl, fetchWithHeaders]);

  // Drop the expanded selection if that camera disappears from the config.
  useEffect(() => {
    if (expandedCam && !cameras.includes(expandedCam)) {
      setExpandedCam(null);
    }
  }, [cameras, expandedCam]);

  if (!cameras || cameras.length === 0) return null;

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await fetchWithHeaders(`${baseUrl}/reconnect-cameras`, { method: 'POST' });
    } catch (e) {
      console.error("Failed to reconnect cameras", e);
    } finally {
      setIsRefreshing(false);
    }
  };

  const others = cameras.filter((c) => c !== expandedCam);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-between items-center">
        <h3 className="text-sm font-semibold text-gray-300">Camera Feeds</h3>
        <button
          onClick={handleRefresh}
          disabled={isRefreshing}
          className="bg-blue-600 hover:bg-blue-500 text-white text-xs px-3 py-1 rounded transition-colors disabled:opacity-50"
        >
          {isRefreshing ? 'Reconnecting...' : 'Reconnect Cameras'}
        </button>
      </div>

      {expandedCam ? (
        <div className="flex flex-col gap-3">
          <CameraFeed
            key={expandedCam}
            name={expandedCam}
            expanded
            onToggleExpand={() => setExpandedCam(null)}
            systemDevices={systemDevices}
          />
          {others.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {others.map((cam) => (
                <CameraFeed
                  key={cam}
                  name={cam}
                  onToggleExpand={() => setExpandedCam(cam)}
                  systemDevices={systemDevices}
                />
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 gap-4">
          {cameras.map(cam => (
            <CameraFeed
              key={cam}
              name={cam}
              onToggleExpand={() => setExpandedCam(cam)}
            />
          ))}
        </div>
      )}
    </div>
  );
};

interface CameraFeedProps {
  name: string;
  expanded?: boolean;
  onToggleExpand?: () => void;
  systemDevices?: {id: string, name: string}[];
}

const CameraFeed: React.FC<CameraFeedProps> = ({ name, expanded = false, onToggleExpand, systemDevices = [] }) => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const [imgSrc, setImgSrc] = useState<string | null>(null);
  const [isFrozen, setIsFrozen] = useState(false);
  const [frozenUsbPath, setFrozenUsbPath] = useState<string | null>(null);
  const [selectedDevice, setSelectedDevice] = useState<string>("");
  const isFetching = useRef(false);
  const prevUrl = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    const interval = setInterval(async () => {
      if (!active || isFetching.current) return;
      isFetching.current = true;
      try {
        const res = await fetchWithHeaders(`${baseUrl}/recording-camera/${encodeURIComponent(name)}`);
        if (res.ok && active) {
          const frozenHeader = res.headers.get("X-Camera-Frozen");
          const isF = frozenHeader === "true";
          setIsFrozen(isF);
          if (isF) {
            setFrozenUsbPath(res.headers.get("X-Camera-Usb"));
          } else {
            setFrozenUsbPath(null);
          }

          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          setImgSrc(url);
        }
      } catch (err) {
        // ignore
      } finally {
        isFetching.current = false;
      }
    }, 200);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [baseUrl, fetchWithHeaders, name]);

  useEffect(() => {
    if (prevUrl.current && prevUrl.current !== imgSrc) {
      const toRevoke = prevUrl.current;
      setTimeout(() => URL.revokeObjectURL(toRevoke), 100);
    }
    prevUrl.current = imgSrc;
  }, [imgSrc]);

  // Cleanup last url on unmount
  useEffect(() => {
    return () => {
      if (prevUrl.current) {
        URL.revokeObjectURL(prevUrl.current);
      }
    };
  }, []);

  const handleDeviceApply = async () => {
    if (!selectedDevice) return;
    try {
      await fetchWithHeaders(`${baseUrl}/recording-camera/${encodeURIComponent(name)}/device`, {
        method: 'POST',
        body: JSON.stringify({ device_index_or_path: selectedDevice }),
      });
      // The interval will automatically pick up the un-frozen feed if it works
    } catch (err) {
      console.error("Failed to change device for", name, err);
    }
  };

  return (
    <div className={`border rounded-lg bg-gray-900 overflow-hidden flex flex-col aspect-video relative ${isFrozen ? 'border-red-500' : 'border-gray-800'}`}>
      <div className={`absolute top-2 left-2 bg-black/70 text-white px-2 py-1 rounded z-10 font-mono flex items-center gap-2 ${expanded ? 'text-sm' : 'text-xs'}`}>
        {name}
        {isFrozen && <span className="text-red-500 font-bold animate-pulse">FROZEN</span>}
      </div>
      {onToggleExpand && (
        <button
          type="button"
          onClick={onToggleExpand}
          aria-label={expanded ? `Minimize ${name}` : `Maximize ${name}`}
          title={expanded ? "Minimize" : "Maximize"}
          className="absolute top-2 right-2 z-10 bg-black/70 hover:bg-black/90 text-white p-1.5 rounded transition-colors"
        >
          {expanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
        </button>
      )}
      {isFrozen && systemDevices.length > 0 && (
        <div className="absolute bottom-2 left-2 right-2 z-10 flex flex-col gap-1">
          {frozenUsbPath && (
            <div className="text-[10px] text-red-200 bg-red-950/80 px-2 py-0.5 rounded border border-red-900 truncate">
              Expected USB: {frozenUsbPath}
            </div>
          )}
          <div className="flex items-center gap-1">
            <select 
              value={selectedDevice}
              onChange={(e) => setSelectedDevice(e.target.value)}
              className="flex-1 min-w-0 bg-red-900/80 border border-red-500 text-white text-xs rounded px-2 py-1 outline-none truncate"
            >
              <option value="" disabled>Select device to recover...</option>
              {systemDevices.map(dev => (
                <option key={dev.id} value={dev.id}>{dev.name}</option>
              ))}
            </select>
            <button 
              type="button"
              onClick={handleDeviceApply}
              disabled={!selectedDevice}
              className="shrink-0 bg-blue-600 hover:bg-blue-500 text-white text-xs px-3 py-1 rounded disabled:opacity-50 border border-blue-700"
            >
              Apply
            </button>
          </div>
        </div>
      )}
      {imgSrc ? (
        <img src={imgSrc} alt={name} className={`w-full h-full object-cover ${isFrozen ? 'opacity-50 grayscale' : ''}`} />
      ) : (
        <div className="flex-1 flex items-center justify-center text-gray-500 text-sm">
          Loading {name}...
        </div>
      )}
    </div>
  );
};
