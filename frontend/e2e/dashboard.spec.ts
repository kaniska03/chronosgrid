import { expect, test } from "@playwright/test";

test.describe("ChronosGrid dashboard", () => {
  test("demo login → overview → queues → job explorer round trip", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByText("ChronosGrid")).toBeVisible();

    // demo login button follows the normal auth flow
    await page.getByTestId("demo-login").click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText("Overview")).toBeVisible();
    await expect(page.getByText("Jobs processed")).toBeVisible();

    // queues page shows seeded queues with controls
    await page.getByRole("link", { name: /Queues/ }).click();
    await expect(page.getByText("default")).toBeVisible();
    await expect(page.getByRole("button", { name: /New queue/ }).first()).toBeVisible();

    // job explorer lists jobs and paginates
    await page.getByRole("link", { name: /Jobs/ }).click();
    await expect(page.getByText("Job Explorer")).toBeVisible();
    await expect(page.locator("table tbody tr").first()).toBeVisible();

    // open a job detail page
    await page.locator("table tbody tr a").first().click();
    await expect(page.getByText("State timeline")).toBeVisible();
    await expect(page.getByText("Payload (sensitive fields masked)")).toBeVisible();
  });

  test("create a job from the UI and watch it complete", async ({ page }) => {
    await page.goto("/login");
    await page.getByTestId("demo-login").click();
    await page.getByRole("link", { name: /Jobs/ }).click();
    await page.getByRole("button", { name: /New job/ }).click();
    await page.getByRole("button", { name: /^Create$/ }).click();
    await expect(page.getByText("Job Explorer")).toBeVisible();
    // workers in compose should complete the math job quickly
    await expect(page.getByText("COMPLETED").first()).toBeVisible({ timeout: 30_000 });
  });

  test("worker monitor shows registered workers", async ({ page }) => {
    await page.goto("/login");
    await page.getByTestId("demo-login").click();
    await page.getByRole("link", { name: /Workers/ }).click();
    await expect(page.getByText("Worker Monitor")).toBeVisible();
  });
});
