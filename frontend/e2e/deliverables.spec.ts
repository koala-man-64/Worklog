import { expect, test } from "@playwright/test";

test("plans, accepts, and reopens a deliverable with evidence across sub-roadmaps", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  expect(boot.ok(), await boot.text()).toBeTruthy();
  const { csrfToken } = await boot.json();
  async function create(path: string, data: unknown): Promise<{ id: string }> {
    const response = await page.request.post("/api/v1" + path, {
      headers: {
        Origin: origin,
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      data,
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }

  const program = await create("/roadmaps", { title: "Application handoff" });
  const designStep = { id: crypto.randomUUID(), title: "Publish design" };
  const buildStep = { id: crypto.randomUUID(), title: "Run clean build" };
  const design = await create("/roadmaps", {
    title: "Design lane", parentId: program.id, steps: [designStep],
  });
  const build = await create("/roadmaps", {
    title: "Build lane", parentId: program.id, steps: [buildStep],
  });
  await create("/roadmaps/" + design.id + "/deliverables", {
    title: "Reviewed design", stepIds: [designStep.id],
  });

  const programHash = "/#roadmaps/" + program.id;
  const deliverablesHash = programHash + "/deliverables";
  await page.goto(programHash);
  await page.getByRole("link", { name: "View deliverables", exact: true }).click();
  await expect(page.getByRole("link", { name: "Reviewed design", exact: true })).toHaveAttribute(
    "href", new RegExp("#roadmaps/" + design.id + "/deliverables/"),
  );
  const newTitle = page.getByRole("textbox", { name: "New deliverable title" });
  await newTitle.fill("Buildable repository");
  await newTitle.press("Enter");

  const detail = page.getByRole("region", { name: "Deliverable details" });
  await expect(detail.getByRole("heading", { name: "Buildable repository" })).toBeVisible();
  const deliveryHash = new URL(page.url()).hash;
  await detail.getByRole("button", { name: "Edit deliverable", exact: true }).click();
  for (const step of [designStep, buildStep]) {
    await detail.getByRole("textbox", { name: "Find steps to link" }).fill(step.title);
    await detail.locator(".deliverable-option").filter({ hasText: step.title })
      .getByRole("button", { name: "Link step", exact: true }).click();
  }

  const add = detail.getByRole("button", { name: "Add requirement", exact: true });
  await add.click();
  await detail.getByRole("textbox", { name: "Requirement 1", exact: true }).fill("Clean checkout builds");
  await detail.getByRole("checkbox", { name: "Requirement 1 complete" }).check();
  const evidence = detail.getByRole("group", { name: "Evidence for requirement 1" });
  await evidence.getByLabel("Evidence for requirement 1 reference type").selectOption("attachment");
  await evidence.getByLabel("Attach a file", { exact: true }).setInputFiles({
    name: "clean-build.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Clean checkout built successfully.\n"),
  });
  await expect(evidence.getByText("clean-build.txt", { exact: true })).toBeVisible();

  await add.click();
  await detail.getByRole("textbox", { name: "Requirement 2", exact: true }).fill("Setup instructions work");
  await detail.getByRole("textbox", { name: "Requirement 2 verification note" })
    .fill("Walked through the README on a clean checkout.");
  await detail.getByRole("checkbox", { name: "Requirement 2 complete" }).check();
  await detail.getByRole("button", { name: "Save deliverable", exact: true }).click();

  await expect(detail.getByText("2 of 2 requirements checked", { exact: true })).toBeVisible();
  await expect(detail.getByRole("link", { name: buildStep.title, exact: true })).toBeVisible();
  await expect(detail.getByRole("link", { name: designStep.title, exact: true })).toBeVisible();
  await detail.getByRole("button", { name: "Submit for review", exact: true }).click();
  await detail.getByRole("button", { name: "Accept deliverable", exact: true }).click();
  await detail.getByRole("textbox", { name: "Acceptance note", exact: true }).fill("Reviewed the build.");
  await detail.getByRole("button", { name: "Confirm acceptance", exact: true }).click();
  await expect(detail.getByRole("button", { name: "Reopen deliverable", exact: true })).toBeVisible();

  await page.goto("/#roadmaps/" + build.id + "/" + buildStep.id);
  await expect(page.getByRole("link", { name: "Buildable repository", exact: true })).toHaveAttribute("href", deliveryHash);
  await page.getByRole("link", { name: "Buildable repository", exact: true }).click();
  await detail.getByRole("button", { name: "Reopen deliverable", exact: true }).click();
  await detail.getByRole("textbox", { name: "Reason for reopening", exact: true }).fill("Refresh the build instructions.");
  await detail.getByRole("button", { name: "Confirm reopening", exact: true }).click();
  await expect(detail.getByText("0 of 2 requirements checked", { exact: true })).toBeVisible();
  const savedReview = detail.locator("details").filter({ hasText: /Acceptance snapshot · revision/ });
  await savedReview.locator("summary").click();
  await expect(savedReview.getByText(/Reviewed the build\./)).toBeVisible();
  await expect(savedReview.getByText("clean-build.txt", { exact: true })).toBeVisible();

  await detail.getByRole("button", { name: "Archive deliverable", exact: true }).click();
  await expect(detail.getByRole("button", { name: "Recover deliverable", exact: true })).toBeVisible();
  await page.goto(deliverablesHash);
  await expect(page.getByRole("link", { name: "Buildable repository", exact: true })).toHaveCount(0);
  await page.getByRole("checkbox", { name: "Show archived" }).check();
  await page.getByRole("link", { name: "Buildable repository", exact: true }).click();
  await detail.getByRole("button", { name: "Recover deliverable", exact: true }).click();
  await expect(detail.getByRole("button", { name: "Edit deliverable", exact: true })).toBeVisible();
});
