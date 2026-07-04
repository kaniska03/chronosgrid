import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatCard, StatusBadge, timeAgo } from "../components/ui";

describe("StatusBadge", () => {
  it("renders known states with a colour class", () => {
    render(<StatusBadge state="COMPLETED" />);
    const el = screen.getByTestId("status-badge");
    expect(el).toHaveTextContent("COMPLETED");
    expect(el.className).toContain("emerald");
  });
  it("falls back gracefully for unknown states", () => {
    render(<StatusBadge state="SOMETHING_NEW" />);
    expect(screen.getByTestId("status-badge")).toHaveTextContent("SOMETHING_NEW");
  });
});

describe("StatCard", () => {
  it("shows label, value and sub text", () => {
    render(<StatCard label="Queue depth" value={42} sub="3 scheduled" />);
    expect(screen.getByText("Queue depth")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("3 scheduled")).toBeInTheDocument();
  });
});

describe("timeAgo", () => {
  it("formats recent and older timestamps", () => {
    expect(timeAgo(new Date().toISOString())).toBe("just now");
    expect(timeAgo(new Date(Date.now() - 120_000).toISOString())).toBe("2m ago");
    expect(timeAgo(null)).toBe("—");
  });
});
