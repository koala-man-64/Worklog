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
