import React from "react";
import { useLocation } from "react-router-dom";
import HfAuthChip from "@/components/landing/HfAuthChip";
import { Button } from "@/components/ui/button";
import { PowerOff } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

const GlobalTopBar: React.FC = () => {
  const { toast } = useToast();
  const location = useLocation();

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

  const isHome = location.pathname === "/";

  return (
    <header className="sticky top-0 z-50 w-full border-b border-gray-800 bg-black/95 backdrop-blur supports-[backdrop-filter]:bg-black/70">
      <div className="mx-auto flex h-[136px] max-w-7xl items-center justify-between px-4">
        <div className="flex items-center gap-2">
          <img
            src="/JedLab.png"
            alt="LeLab"
            className="h-[120px] w-[120px]"
          />
          <span className="text-4xl font-semibold tracking-tight text-white">
            JedLab
          </span>
        </div>
        <div className="flex items-center gap-3">
          {isHome && (
            <Button 
              variant="destructive" 
              size="lg" 
              onClick={handleStopAndHome}
              className="flex items-center gap-2 text-xl px-6 py-6"
            >
              <PowerOff className="h-6 w-6" />
              Stop & Home
            </Button>
          )}
          {isHome && <HfAuthChip />}
        </div>
      </div>
    </header>
  );
};

export default GlobalTopBar;
