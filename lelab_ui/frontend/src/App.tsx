import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { UrdfProvider } from "@/contexts/UrdfContext";
import { DragAndDropProvider } from "@/contexts/DragAndDropContext";
import { Toaster } from "@/components/ui/toaster";
import Landing from "@/pages/Landing";
import Teleoperation from "@/pages/Teleoperation";
import Calibration from "@/pages/Calibration";
import Recording from "@/pages/Recording";
import CameraSetup from "@/pages/CameraSetup";
import Inference from "@/pages/Inference";
import EditDataset from "@/pages/EditDataset";
import Upload from "@/pages/Upload";
import DatasetPreview from "@/pages/DatasetPreview";
import RecordingCameras from "@/pages/RecordingCameras";
import ArmPositions from "@/pages/ArmPositions";

import NotFound from "@/pages/NotFound";
import SingleTabGuard from "@/components/SingleTabGuard";
import GlobalTopBar from "@/components/GlobalTopBar";
import { TooltipProvider } from "@radix-ui/react-tooltip";
import { ApiProvider } from "./contexts/ApiContext";
import { HfAuthProvider } from "./contexts/HfAuthContext";

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <ThemeProvider>
          <ApiProvider>
            <HfAuthProvider>
              <UrdfProvider>
                <DragAndDropProvider>
                  <BrowserRouter>
                    <Routes>
                      <Route path="/recording-cameras" element={<RecordingCameras />} />
                      <Route path="*" element={
                        <SingleTabGuard>
                          <GlobalTopBar />
                          <Routes>
                            <Route path="/" element={<Landing />} />
                            <Route path="/teleoperation" element={<Teleoperation />} />
                            <Route path="/recording" element={<Recording />} />
                            <Route path="/arm-positions" element={<ArmPositions />} />
                            <Route path="/upload" element={<Upload />} />
                            <Route path="/dataset-preview" element={<DatasetPreview />} />
                            <Route path="/camera-setup" element={<CameraSetup />} />
                            <Route path="/inference" element={<Inference />} />
                            <Route path="/calibration" element={<Calibration />} />
                            <Route path="/edit-dataset" element={<EditDataset />} />
                            <Route path="*" element={<NotFound />} />
                          </Routes>
                        </SingleTabGuard>
                      } />
                    </Routes>
                    <Toaster />
                  </BrowserRouter>
                </DragAndDropProvider>
              </UrdfProvider>
            </HfAuthProvider>
          </ApiProvider>
        </ThemeProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
