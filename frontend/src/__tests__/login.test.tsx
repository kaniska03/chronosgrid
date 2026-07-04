import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../hooks/useAuth";
import Login from "../pages/Login";

function renderLogin() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <MemoryRouter>
          <Login />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("Login page", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("shows the demo login button with credentials hint", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 401 })));
    renderLogin();
    expect(screen.getByTestId("demo-login")).toBeInTheDocument();
    expect(screen.getByText(/demo@chronosgrid\.dev/)).toBeInTheDocument();
  });

  it("demo button autofills credentials and calls the login API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ access_token: "a", refresh_token: "r" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();
    fireEvent.click(screen.getByTestId("demo-login"));
    await waitFor(() => {
      const loginCall = fetchMock.mock.calls.find(([u]) => String(u).includes("/auth/login"));
      expect(loginCall).toBeTruthy();
      expect(JSON.parse(loginCall![1].body)).toEqual({
        email: "demo@chronosgrid.dev", password: "Demo@1234" });
    });
    expect((screen.getByLabelText("Email") as HTMLInputElement).value)
      .toBe("demo@chronosgrid.dev");
  });
});
