import React from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { JobCheckpoint } from "@/lib/checkpointsApi";

interface Props {
  checkpoints: JobCheckpoint[];
  selectedStep: number | null;
  onChange: (step: number) => void;
  disabled?: boolean;
  placeholder?: string;
}

export const CheckpointDropdown: React.FC<Props> = ({
  checkpoints,
  selectedStep,
  onChange,
  disabled,
  placeholder = "Select checkpoint",
}) => {
  const value = selectedStep != null ? String(selectedStep) : undefined;
  return (
    <Select
      value={value}
      onValueChange={(v) => onChange(Number(v))}
      disabled={disabled || checkpoints.length === 0}
    >
      <SelectTrigger
        className="bg-slate-800 border-slate-700 text-white h-8 text-xs px-2 w-auto min-w-[110px]"
        onClick={(e) => e.stopPropagation()}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className="bg-slate-900 border-slate-700 text-white">
        {checkpoints.map((c) => (
          <SelectItem
            key={c.step}
            value={String(c.step)}
            onClick={(e) => e.stopPropagation()}
          >
            step {c.step}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
};

export default CheckpointDropdown;
