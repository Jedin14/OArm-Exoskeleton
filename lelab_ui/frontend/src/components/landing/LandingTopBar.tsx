import React from "react";
import HfAuthChip from "./HfAuthChip";
import { Button } from "@/components/ui/button";
import { PowerOff } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

const LandingTopBar: React.FC = () => {
  const { toast } = useToast();

  const handleStopAndHome = async () => {
    try {
      toast({
        title: "Shutting Down...",
        description: "Moving arms to home and killing background tasks.",
      });
      await fetch("/stop-and-home", { method: "POST" });
    } catch (e) {
      console.error(e);
    }
  };
  return (
    <header className="sticky top-0 z-30 w-full border-b border-gray-800 bg-black/95 backdrop-blur supports-[backdrop-filter]:bg-black/70">
      <div className="mx-auto flex h-12 max-w-7xl items-center justify-between px-4">
        <div className="flex items-center gap-2">
          <img
            src="/lovable-uploads/5e648747-34b7-4d8f-93fd-4dbd00aeeefc.png"
            alt="LeLab"
            className="h-7 w-7"
          />
          <span className="text-base font-semibold tracking-tight text-white">
            LeLab
          </span>
        </div>
        <div className="flex items-center gap-3">
          <Button 
            variant="destructive" 
            size="sm" 
            onClick={handleStopAndHome}
            className="flex items-center gap-2"
          >
            <PowerOff className="h-4 w-4" />
            Stop & Home
          </Button>
          <HfAuthChip />
        </div>
      </div>
    </header>
  );
};

export default LandingTopBar;
