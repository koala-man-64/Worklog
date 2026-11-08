import { expect, test } from "@playwright/test";

test("the guide opens from the sidebar and its links jump to its sections", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Guide", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Guide", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "Contents" })
    .getByRole("link", { name: "Back up and export", exact: true })
    .click();
  await expect(page).toHaveURL(/#guide\/back-up-and-export$/);
  await expect(
    page.getByRole("heading", { name: "Back up and export", exact: true }),
  ).toBeInViewport();
  // A link inside the guide opens one of its sections, never another page.
  await page
    .getByRole("article", { name: "Worklog guide" })
    .getByRole("link", { name: "Conflicts", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/#guide\/conflicts$/);
  await expect(
    page.getByRole("heading", { name: "Conflicts", exact: true }),
  ).toBeInViewport();
});

test("the guide shows what agents read in a view of its own, with its own addresses", async ({
  page,
}) => {
  await page.goto("/#guide/back-up-and-export");
  const views = page.getByRole("group", { name: "Show the guide" });
  await views.getByRole("link", { name: "For agents", exact: true }).click();
  await expect(page).toHaveURL(/#agent-guide$/);
  const text = page.getByRole("article", { name: "Agent guide", exact: true });
  await expect(text).toBeVisible();
  // The other guide opens at its top, and stays the sidebar's Guide page.
  expect(await text.evaluate((pane) => pane.scrollTop)).toBe(0);
  await expect(
    page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("link", { name: "Guide", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await page
    .getByRole("navigation", { name: "Contents" })
    .getByRole("link", { name: "8. Errors", exact: true })
    .click();
  await expect(page).toHaveURL(/#agent-guide\/8-errors$/);
  await expect(
    page.getByRole("heading", { name: "8. Errors", exact: true }),
  ).toBeInViewport();
  await views.getByRole("link", { name: "For you", exact: true }).click();
  await expect(page).toHaveURL(/#guide$/);
  const own = page.getByRole("article", { name: "Worklog guide", exact: true });
  await expect(own).toBeVisible();
  expect(await own.evaluate((pane) => pane.scrollTop)).toBe(0);
});
