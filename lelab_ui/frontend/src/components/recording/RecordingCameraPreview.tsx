import React, { useEffect, useState, useRef } from 'react';
import { useApi } from '@/contexts/ApiContext';

interface RecordingCameraPreviewProps {
  cameras: string[];
}

export const RecordingCameraPreview: React.FC<RecordingCameraPreviewProps> = ({ cameras }) => {
  if (!cameras || cameras.length === 0) return null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 gap-4">
      {cameras.map(cam => (
        <CameraFeed key={cam} name={cam} />
      ))}
    </div>
  );
};

const CameraFeed: React.FC<{ name: string }> = ({ name }) => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const [imgSrc, setImgSrc] = useState<string | null>(null);
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

  return (
    <div className="border border-gray-800 rounded-lg bg-gray-900 overflow-hidden flex flex-col aspect-video relative">
      <div className="absolute top-2 left-2 bg-black/70 text-white px-2 py-1 text-xs rounded z-10 font-mono">
        {name}
      </div>
      {imgSrc ? (
        <img src={imgSrc} alt={name} className="w-full h-full object-cover" />
      ) : (
        <div className="flex-1 flex items-center justify-center text-gray-500 text-sm">
          Loading {name}...
        </div>
      )}
    </div>
  );
};
