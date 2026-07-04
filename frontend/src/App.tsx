import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Spinner } from "./components/ui";
import Layout from "./components/Layout";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import Audit from "./pages/Audit";
import Dlq from "./pages/Dlq";
import JobDetail from "./pages/JobDetail";
import Jobs from "./pages/Jobs";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Queues from "./pages/Queues";
import RecurringPage from "./pages/Recurring";
import Settings from "./pages/Settings";
import Workers from "./pages/Workers";
import { WorkflowDetail, WorkflowList } from "./pages/Workflows";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 2000 } },
});

function Protected({ children }: { children: React.ReactNode }) {
  const { authed, loading } = useAuth();
  if (loading) return <Spinner />;
  if (!authed) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<Protected><Layout /></Protected>}>
              <Route path="/" element={<Overview />} />
              <Route path="/queues" element={<Queues />} />
              <Route path="/jobs" element={<Jobs />} />
              <Route path="/jobs/:jobId" element={<JobDetail />} />
              <Route path="/workflows" element={<WorkflowList />} />
              <Route path="/workflows/:workflowId" element={<WorkflowDetail />} />
              <Route path="/workers" element={<Workers />} />
              <Route path="/recurring" element={<RecurringPage />} />
              <Route path="/dlq" element={<Dlq />} />
              <Route path="/audit" element={<Audit />} />
              <Route path="/settings" element={<Settings />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
