import React, { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

interface JointGraphProps {
  jointPositions?: Record<string, number>;
  armMode?: string;
}

export const JointGraph: React.FC<JointGraphProps> = ({ jointPositions, armMode = "both" }) => {
  const [history, setHistory] = useState<any[]>([]);

  useEffect(() => {
    if (!jointPositions || Object.keys(jointPositions).length === 0) return;

    setHistory((prev) => {
      const newEntry = { time: Date.now(), ...jointPositions };
      const updated = [...prev, newEntry];
      if (updated.length > 60) {
        return updated.slice(updated.length - 60);
      }
      return updated;
    });
  }, [jointPositions]);

  const leftKeys = Object.keys(jointPositions || {}).filter(k => k.toLowerCase().includes('left'));
  const rightKeys = Object.keys(jointPositions || {}).filter(k => k.toLowerCase().includes('right'));

  const colors = ['#ff6b35', '#ffdd44', '#4ade80', '#60a5fa', '#a78bfa', '#f472b6', '#fb923c'];

  // In single-arm recording modes the other arm has no data — hide its panel
  // so we don't show an empty chart.
  const showLeft = armMode !== "right";
  const showRight = armMode !== "left";
  const gridCols = showLeft && showRight ? "xl:grid-cols-2" : "grid-cols-1";

  return (
    <div className={`grid grid-cols-1 ${gridCols} gap-4 h-full`}>
      {showLeft && (
      <div className="border border-gray-800 rounded-lg p-4 bg-gray-900 flex flex-col min-h-[250px]">
        <h3 className="text-sm text-white font-medium mb-2">Left Arm Joints</h3>
        <div className="flex-1">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="time" hide />
              <YAxis fontSize={12} stroke="#9CA3AF" domain={['auto', 'auto']} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1F2937',
                  border: '1px solid #374151',
                  color: '#fff'
                }}
                labelFormatter={() => ''}
              />
              {leftKeys.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={colors[i % colors.length]}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      )}

      {showRight && (
      <div className="border border-gray-800 rounded-lg p-4 bg-gray-900 flex flex-col min-h-[250px]">
        <h3 className="text-sm text-white font-medium mb-2">Right Arm Joints</h3>
        <div className="flex-1">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="time" hide />
              <YAxis fontSize={12} stroke="#9CA3AF" domain={['auto', 'auto']} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1F2937',
                  border: '1px solid #374151',
                  color: '#fff'
                }}
                labelFormatter={() => ''}
              />
              {rightKeys.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={colors[i % colors.length]}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      )}
    </div>
  );
};
