import { Refine } from "@refinedev/core";
import { BrowserRouter, Routes, Route, Outlet } from "react-router-dom";
import routerBindings, {
  UnsavedChangesNotifier,
  DocumentTitleHandler,
} from "@refinedev/react-router-v6";
import dataProvider from "@refinedev/simple-rest";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8080";

function App() {
  return (
    <BrowserRouter>
      <Refine
        dataProvider={dataProvider(API_URL)}
        routerProvider={routerBindings}
        resources={[
          {
            name: "dashboard",
            list: "/",
          },
        ]}
        options={{
          syncWithLocation: true,
          warnWhenUnsavedChanges: true,
        }}
      >
        <Routes>
          <Route
            element={
              <div className="min-h-screen bg-gray-100">
                <Outlet />
              </div>
            }
          >
            <Route index element={<DashboardPage />} />
          </Route>
        </Routes>
        <UnsavedChangesNotifier />
        <DocumentTitleHandler />
      </Refine>
    </BrowserRouter>
  );
}

function DashboardPage() {
  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold text-gray-900">MediaHub Admin</h1>
      <p className="mt-2 text-gray-600">Welcome to the admin dashboard.</p>
    </div>
  );
}

export default App;
