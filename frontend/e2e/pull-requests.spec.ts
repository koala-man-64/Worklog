import { expect, test } from "@playwright/test";

test("pull request registry records a submitter, filters and edits it", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  const itemTitle = "Registry work " + suffix;
  const pullRequestTitle = "Tune retry timing " + suffix;
  const firstSubmitter = "Rudy " + suffix;
  const revisedSubmitter = "Ada " + suffix;
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  async function write(path: string, data: unknown) {
    const response = await page.request.post("/api/v1" + path, {
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        Origin: origin,
        "X-CSRF-Token": csrfToken,
      },
      data,
    });
    expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  }

  const item = await write("/work-items", { title: itemTitle });
  const roadmapTitle = "Registry roadmap " + suffix;
  const roadmap = await write("/roadmaps", { title: roadmapTitle });
  let pullRequest: { id: string; revision: number } | undefined;
  try {
    await page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("link", { name: "Pull requests", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Pull requests", exact: true }),
    ).toBeVisible();

    const ownerPicker = page.getByRole("group", {
      name: "Link to work item or roadmap",
    });
    await ownerPicker
      .getByRole("button", { name: "Choose roadmap", exact: true })
      .click();
    await ownerPicker
      .getByRole("textbox", { name: "Roadmap to link search", exact: true })
      .fill(roadmapTitle);
    await ownerPicker
      .getByRole("list", { name: "Roadmap to link matches", exact: true })
      .getByRole("button", { name: new RegExp(roadmapTitle) })
      .click();
    await expect(
      ownerPicker.getByText(roadmapTitle, { exact: true }),
    ).toBeVisible();
    await ownerPicker
      .getByRole("button", { name: "Choose work item", exact: true })
      .click();
    await ownerPicker
      .getByRole("textbox", { name: "Work item to link search", exact: true })
      .fill(itemTitle);
    await ownerPicker
      .getByRole("list", { name: "Work item to link matches", exact: true })
      .getByRole("button", { name: new RegExp(itemTitle) })
      .click();

    await page
      .getByRole("textbox", { name: "Pull request URL", exact: true })
      .fill(`https://github.com/example/worklog/pull/42`);
    await page
      .getByRole("textbox", { name: "Pull request title", exact: true })
      .fill(pullRequestTitle);
    await page.getByRole("textbox", { name: "PR number", exact: true }).fill("42");
    await page
      .getByRole("textbox", { name: "Submitted by", exact: true })
      .last()
      .fill(firstSubmitter);
    const createdResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/github",
    );
    await page
      .getByRole("button", { name: "Add pull request", exact: true })
      .click();
    const created = await (await createdResponse).json();
    pullRequest = { id: created.id, revision: created.revision };

    const row = page.getByRole("article", {
      name: `Pull request ${pullRequestTitle}`,
      exact: true,
    });
    await expect(
      row.getByRole("link", { name: new RegExp(pullRequestTitle) }),
    ).toHaveAttribute("href", "https://github.com/example/worklog/pull/42");
    await expect(row.getByText(itemTitle, { exact: true })).toHaveAttribute(
      "href",
      "#work/" + item.id,
    );
    await expect(
      row.getByText("Submitted by: " + firstSubmitter, { exact: true }),
    ).toBeVisible();
    await expect(
      row.getByRole("button", { name: "Edit submitter", exact: true }),
    ).toBeVisible();

    await page
      .getByRole("textbox", { name: "Filter by submitter", exact: true })
      .fill(firstSubmitter);
    await page.getByRole("button", { name: "Filter", exact: true }).click();
    await expect(row).toBeVisible();
    await row
      .getByRole("button", { name: "Edit submitter", exact: true })
      .click();
    const submitterField = row.getByRole("textbox", {
      name: "Submitted by",
      exact: true,
    });
    await expect(submitterField).toHaveValue(firstSubmitter);
    await submitterField.fill("Discarded edit");
    await row.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(submitterField).toHaveCount(0);
    await expect(
      row.getByText("Submitted by: " + firstSubmitter, { exact: true }),
    ).toBeVisible();
    await row
      .getByRole("button", { name: "Edit submitter", exact: true })
      .click();
    await row
      .getByRole("textbox", { name: "Submitted by", exact: true })
      .fill(revisedSubmitter);
    const updatedResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === `/api/v1/github/${pullRequest!.id}`,
    );
    await row.getByRole("button", { name: "Save submitter", exact: true }).click();
    const updated = await (await updatedResponse).json();
    pullRequest = { id: updated.id, revision: updated.revision };
    await page.getByRole("button", { name: "Clear filter", exact: true }).click();
    await expect(
      row.getByText("Submitted by: " + revisedSubmitter, { exact: true }),
    ).toBeVisible();

    await page
      .getByRole("textbox", { name: "Filter by submitter", exact: true })
      .fill(firstSubmitter);
    await page.getByRole("button", { name: "Filter", exact: true }).click();
    await expect(row).toHaveCount(0);
    await page
      .getByRole("textbox", { name: "Filter by submitter", exact: true })
      .fill(revisedSubmitter.slice(1, 4).toLowerCase());
    await page.getByRole("button", { name: "Filter", exact: true }).click();
    await expect(row).toBeVisible();
    await page.reload();
    const persistedRow = page.getByRole("article", {
      name: `Pull request ${pullRequestTitle}`,
      exact: true,
    });
    await expect(
      persistedRow.getByText("Submitted by: " + revisedSubmitter, {
        exact: true,
      }),
    ).toBeVisible();
    await persistedRow
      .getByRole("button", { name: "Edit submitter", exact: true })
      .click();
    await persistedRow
      .getByRole("textbox", { name: "Submitted by", exact: true })
      .fill("   ");
    const clearedResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        new URL(response.url()).pathname === `/api/v1/github/${pullRequest!.id}`,
    );
    await persistedRow
      .getByRole("button", { name: "Save submitter", exact: true })
      .click();
    const cleared = await (await clearedResponse).json();
    pullRequest = { id: cleared.id, revision: cleared.revision };
    await expect(
      persistedRow.getByText("Submitted by: Unknown", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page
        .getByRole("article", { name: `Pull request ${pullRequestTitle}` })
        .getByText("Submitted by: Unknown", { exact: true }),
    ).toBeVisible();
  } finally {
    if (pullRequest)
      await write(`/github/${pullRequest.id}/archive`, {
        revision: pullRequest.revision,
      });
    await write(`/work-items/${item.id}/archive`, { revision: item.revision });
    await write(`/roadmaps/${roadmap.id}/archive`, {
      revision: roadmap.revision,
    });
  }
});
