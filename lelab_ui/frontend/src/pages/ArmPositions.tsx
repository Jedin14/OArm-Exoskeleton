import React, { useState, useEffect } from 'react';
import { useApi } from '@/contexts/ApiContext';
import { useToast } from '@/hooks/use-toast';
import { Trash2, Edit2, Play, Plus, Target, Check, X, ShieldAlert } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

interface ArmPosition {
  id: string;
  name: string;
  joint_values: number[];
  is_default: boolean;
  created_at: string;
}

const ArmPositions: React.FC = () => {
  const { baseUrl } = useApi();
  const { toast } = useToast();
  const navigate = useNavigate();
  
  const [positions, setPositions] = useState<ArmPosition[]>([]);
  const [robotName, setRobotName] = useState<string>('');
  
  // State for recording a new position
  const [isRecording, setIsRecording] = useState(false);
  const [liveValues, setLiveValues] = useState<number[] | null>(null);
  const [newName, setNewName] = useState('');
  
  // State for locking/mirroring
  const [leftLocked, setLeftLocked] = useState(false);
  const [rightLocked, setRightLocked] = useState(false);
  
  // State for editing
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editValues, setEditValues] = useState<number[]>([]);

  useEffect(() => {
    // Try to get the active robot name from localStorage (saved by config process)
    // Or we could list robots and pick the first one with a valid config
    const fetchRobot = async () => {
      try {
        const res = await fetch(`${baseUrl}/robots`);
        const data = await res.json();
        if (data.status === 'success' && data.robots.length > 0) {
          // Find the active robot or just use the first one
          const robot = data.robots[0].name;
          setRobotName(robot);
          fetchPositions(robot);
        } else {
          toast({
            title: "No Robot Configured",
            description: "Please configure a robot first.",
            variant: "destructive"
          });
        }
      } catch (err) {
        console.error(err);
      }
    };
    fetchRobot();
  }, [baseUrl]);

  const fetchPositions = async (robot: string) => {
    try {
      const res = await fetch(`${baseUrl}/robots/${robot}/positions`);
      const data = await res.json();
      if (data.status === 'success') {
        setPositions(data.positions);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleStartRecording = async () => {
    if (!robotName) return;
    try {
      // Send command to unlock both arms
      await fetch(`${baseUrl}/toggle-left-arm-home`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fixed: false })
      });
      await fetch(`${baseUrl}/toggle-right-arm-home`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fixed: false })
      });
      
      setLeftLocked(false);
      setRightLocked(false);
      setIsRecording(true);
      setNewName('New Position');
      
      // Start polling for live values
      const pollInterval = setInterval(async () => {
        try {
          const res = await fetch(`${baseUrl}/robots/${robotName}/positions/capture`, { method: "POST" });
          const data = await res.json();
          if (data.status === 'success') {
            setLiveValues(data.joint_values);
          }
        } catch (e) {
          // ignore polling errors
        }
      }, 500);
      
      return () => clearInterval(pollInterval);
    } catch (err) {
      toast({ title: "Failed to unlock arms", variant: "destructive" });
    }
  };

  const toggleLock = async (side: 'left' | 'right') => {
    const isLocked = side === 'left' ? leftLocked : rightLocked;
    const newLock = !isLocked;
    try {
      await fetch(`${baseUrl}/toggle-${side}-arm-home`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fixed: newLock })
      });
      if (side === 'left') setLeftLocked(newLock);
      else setRightLocked(newLock);
    } catch (e) {
      toast({ title: `Failed to toggle ${side} arm`, variant: "destructive" });
    }
  };

  const handleMirror = async (from: 'left' | 'right') => {
    if (!liveValues || !robotName) return;
    const to = from === 'left' ? 'right' : 'left';
    const fromBaseIdx = from === 'left' ? 0 : 8;
    const toBaseIdx = from === 'left' ? 8 : 0;
    
    const mirroredValues = [...liveValues];
    for (let i = 0; i < 8; i++) {
        // Negate J1 (base pan) for physical mirroring symmetry
        mirroredValues[toBaseIdx + i] = i === 0 ? -mirroredValues[fromBaseIdx + i] : mirroredValues[fromBaseIdx + i];
    }
    
    // Optimistically update the UI to avoid 500ms lag
    setLiveValues(mirroredValues);
    
    try {
        await fetch(`${baseUrl}/robots/${robotName}/positions/set-target`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: "mirror", joint_values: mirroredValues })
        });
        
        await fetch(`${baseUrl}/toggle-${to}-arm-home`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fixed: true })
        });
        if (to === 'left') setLeftLocked(true);
        else setRightLocked(true);
        
        toast({ title: `Mirrored ${from} to ${to}` });
    } catch (e) {
        toast({ title: "Failed to mirror", variant: "destructive" });
    }
  };

  const handleSaveRecording = async () => {
    if (!robotName || !liveValues) return;
    try {
      const res = await fetch(`${baseUrl}/robots/${robotName}/positions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, joint_values: liveValues })
      });
      if (res.ok) {
        toast({ title: "Position saved successfully" });
        setIsRecording(false);
        setLiveValues(null);
        fetchPositions(robotName);
      } else {
        toast({ title: "Failed to save position", variant: "destructive" });
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch(`${baseUrl}/robots/${robotName}/positions/${id}`, { method: "DELETE" });
      if (res.ok) {
        toast({ title: "Position deleted" });
        fetchPositions(robotName);
      } else {
        toast({ title: "Cannot delete position", variant: "destructive" });
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleMoveTo = async (id: string) => {
    try {
      const res = await fetch(`${baseUrl}/robots/${robotName}/positions/${id}/move-to`, { method: "POST" });
      if (res.ok) {
        toast({ title: "Moving to position..." });
      } else {
        toast({ title: "Failed to move to position", variant: "destructive" });
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleEdit = (pos: ArmPosition) => {
    setEditingId(pos.id);
    setEditName(pos.name);
    setEditValues([...pos.joint_values]);
  };

  const handleSaveEdit = async () => {
    try {
      const res = await fetch(`${baseUrl}/robots/${robotName}/positions/${editingId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: editName, joint_values: editValues })
      });
      if (res.ok) {
        toast({ title: "Position updated" });
        setEditingId(null);
        fetchPositions(robotName);
      }
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 p-8">
      <div className="max-w-6xl mx-auto space-y-8">
        
        <div className="flex items-center justify-between border-b border-slate-800 pb-6">
          <div>
            <h1 className="text-3xl font-bold text-white tracking-tight">Arm Positions</h1>
            <p className="text-slate-400 mt-2">Manage saved home positions for {robotName || 'your robot'}</p>
          </div>
          
          <button
            onClick={() => navigate("/")}
            className="px-4 py-2 text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-md transition-colors"
          >
            Back to Home
          </button>
        </div>

        {isRecording && (
          <div className="bg-orange-500/10 border border-orange-500/20 rounded-xl p-6 shadow-xl animate-in fade-in slide-in-from-top-4">
            <div className="flex items-start gap-4">
              <div className="p-3 bg-orange-500/20 rounded-full text-orange-400 mt-1">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <div className="flex-1 space-y-4">
                <div>
                  <h3 className="text-lg font-semibold text-orange-400 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                    Recording New Position
                  </h3>
                  <p className="text-slate-300 mt-1">
                    Both arms are now unlocked. Move the arms to the desired position.
                  </p>
                </div>
                
                <div className="grid grid-cols-2 gap-4 text-xs font-mono bg-slate-900/50 p-4 rounded-lg border border-slate-800">
                  <div>
                    <div className="flex justify-between items-center mb-2">
                      <div className="text-slate-400">LEFT ARM</div>
                      <button 
                        onClick={() => toggleLock('left')}
                        className={`px-2 py-1 rounded text-[10px] font-bold ${leftLocked ? 'bg-red-500/20 text-red-400 border border-red-500/50' : 'bg-green-500/20 text-green-400 border border-green-500/50'}`}
                      >
                        {leftLocked ? 'LOCKED' : 'UNLOCKED'}
                      </button>
                    </div>
                    {liveValues ? liveValues.slice(0, 8).map((v, i) => (
                      <div key={`l-${i}`} className="flex justify-between border-b border-slate-800/50 py-1">
                        <span>Joint {i+1}</span>
                        <span className="text-slate-300">{v.toFixed(4)}</span>
                      </div>
                    )) : <div className="text-slate-500 animate-pulse">Waiting for ROS payload...</div>}
                    
                    <button 
                      onClick={() => handleMirror('left')}
                      className="w-full mt-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 rounded-md border border-blue-500/30 transition-colors"
                    >
                      Mirror L ➔ R
                    </button>
                  </div>
                  <div>
                    <div className="flex justify-between items-center mb-2">
                      <div className="text-slate-400">RIGHT ARM</div>
                      <button 
                        onClick={() => toggleLock('right')}
                        className={`px-2 py-1 rounded text-[10px] font-bold ${rightLocked ? 'bg-red-500/20 text-red-400 border border-red-500/50' : 'bg-green-500/20 text-green-400 border border-green-500/50'}`}
                      >
                        {rightLocked ? 'LOCKED' : 'UNLOCKED'}
                      </button>
                    </div>
                    {liveValues ? liveValues.slice(8, 16).map((v, i) => (
                      <div key={`r-${i}`} className="flex justify-between border-b border-slate-800/50 py-1">
                        <span>Joint {i+1}</span>
                        <span className="text-slate-300">{v.toFixed(4)}</span>
                      </div>
                    )) : <div className="text-slate-500 animate-pulse">Waiting for ROS payload...</div>}
                    
                    <button 
                      onClick={() => handleMirror('right')}
                      className="w-full mt-3 py-1.5 bg-blue-600/20 hover:bg-blue-600/40 text-blue-400 rounded-md border border-blue-500/30 transition-colors"
                    >
                      Mirror R ➔ L
                    </button>
                  </div>
                </div>

                <div className="flex items-center gap-4 pt-2">
                  <input
                    type="text"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2 text-white focus:outline-none focus:border-blue-500"
                    placeholder="Position Name"
                  />
                  <button
                    onClick={handleSaveRecording}
                    disabled={!liveValues}
                    className="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center gap-2"
                  >
                    <Check className="w-4 h-4" /> Save Position
                  </button>
                  <button
                    onClick={() => {
                      setIsRecording(false);
                      setLiveValues(null);
                    }}
                    className="px-6 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-lg font-medium transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="flex justify-between items-center">
          <h2 className="text-xl font-semibold text-slate-200">Saved Positions</h2>
          {!isRecording && (
            <button
              onClick={handleStartRecording}
              className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2 shadow-lg shadow-green-900/20"
            >
              <Target className="w-4 h-4" /> Record Point
            </button>
          )}
        </div>

        <div className="grid gap-4">
          {positions.map((pos) => (
            <div key={pos.id} className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden hover:border-slate-700 transition-colors">
              {editingId === pos.id ? (
                <div className="p-6 space-y-4">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-lg font-semibold text-white">Edit Position</h3>
                    <div className="flex items-center gap-2">
                      <button onClick={handleSaveEdit} className="p-2 bg-blue-600/20 text-blue-400 rounded hover:bg-blue-600/30">
                        <Check className="w-4 h-4" />
                      </button>
                      <button onClick={() => setEditingId(null)} className="p-2 bg-slate-800 text-slate-400 rounded hover:bg-slate-700">
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-2 text-white mb-4"
                  />
                  <div className="grid grid-cols-4 gap-2 font-mono text-xs">
                    {editValues.map((val, idx) => (
                      <div key={idx} className="flex flex-col gap-1">
                        <span className="text-slate-500">J{idx}</span>
                        <input
                          type="number"
                          step="0.0001"
                          value={val}
                          onChange={(e) => {
                            const newVals = [...editValues];
                            newVals[idx] = parseFloat(e.target.value);
                            setEditValues(newVals);
                          }}
                          className="bg-slate-950 border border-slate-800 rounded px-2 py-1 text-slate-300"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="p-6 flex items-center justify-between group">
                  <div className="space-y-1">
                    <div className="flex items-center gap-3">
                      <h3 className="text-lg font-semibold text-white">{pos.name}</h3>
                      {pos.is_default && (
                        <span className="px-2 py-0.5 bg-blue-500/10 text-blue-400 text-xs font-medium rounded border border-blue-500/20">
                          DEFAULT
                        </span>
                      )}
                    </div>
                    <div className="text-xs font-mono text-slate-500 flex gap-4">
                      <span>ID: {pos.id}</span>
                      <span>Left: [{pos.joint_values.slice(0, 4).map(v => v.toFixed(2)).join(', ')}...]</span>
                      <span>Right: [{pos.joint_values.slice(8, 12).map(v => v.toFixed(2)).join(', ')}...]</span>
                    </div>
                  </div>
                  
                  <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => handleMoveTo(pos.id)}
                      title="Move Arm to this Position"
                      className="flex items-center gap-2 px-4 py-2 bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 rounded-lg font-medium transition-colors"
                    >
                      <Play className="w-4 h-4 fill-current" /> Move To
                    </button>
                    
                    {!pos.is_default && (
                      <>
                        <button
                          onClick={() => handleEdit(pos)}
                          className="p-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors"
                        >
                          <Edit2 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => {
                            if (window.confirm("Are you sure you want to delete this position?")) {
                              handleDelete(pos.id);
                            }
                          }}
                          className="p-2 text-red-400 hover:text-red-300 hover:bg-red-950/30 rounded-lg transition-colors"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
          {positions.length === 0 && (
            <div className="text-center py-12 text-slate-500 border border-dashed border-slate-800 rounded-xl">
              No positions saved yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ArmPositions;
