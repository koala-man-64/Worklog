import { expect, test } from "@playwright/test";

test("Repo + Wave execution splits multi-repo leaf, freezes tier 1, and tracks consumer re-pin verification", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID().slice(0, 8);
  const contractsName = "contracts-" + suffix;
  const controlPlaneName = "control-plane-" + suffix;
  const roadmapTitle = "Asset Allocation " + suffix;

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();

  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();

  async function post(path: string, data: unknown) {
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

  // Register Tier 1 contracts and Tier 2 control-plane (depending on contracts), both WIP cap 1.
  const contracts = await post("/repositories", {
    name: contractsName,
    url: `https://github.com/example/${contractsName}`,
    tier: 1,
    wipCap: 1,
  });
  const controlPlane = await post("/repositories", {
    name: controlPlaneName,
    url: `https://github.com/example/${controlPlaneName}`,
    tier: 2,
    wipCap: 1,
    upstreamIds: [contracts.id],
  });

  // Create roadmap with a Phase and one multi-repo implementation leaf plus a cross-repo Readiness gate.
  const phaseId = crypto.randomUUID();
  const multiLeafId = crypto.randomUUID();
  const gateId = crypto.randomUUID();
  const roadmap = await post("/roadmaps", {
    title: roadmapTitle,
    overview: "Repo + Wave E2E verification",
    steps: [
      { id: phaseId, title: "Phase 1: Foundation", position: 0 },
      {
        id: multiLeafId,
        title: "Adopt shared DTO contract",
        parentId: phaseId,
        position: 1,
        workClass: "critical_path",
        repositoryIds: [contracts.id, controlPlane.id],
      },
      {
        id: gateId,
        title: "60-day golden path gate",
        parentId: phaseId,
        position: 2,
        workClass: "gate",
        repositoryIds: [contracts.id, controlPlane.id],
      },
    ],
  });

  // Open Roadmap Execution tab.
  await page.goto(`/#roadmaps/${roadmap.id}/execution`);
  await expect(
    page.getByRole("heading", { name: roadmapTitle, exact: true }),
  ).toBeVisible();

  // Readiness gate is exempt from Multi-repo leaves to split, while multiLeafId is surfaced for splitting.
  const multiSection = page.getByRole("region", { name: "Multi-repo leaves" });
  await expect(multiSection).toContainText("Adopt shared DTO contract");
  await expect(multiSection).not.toContainText("60-day golden path gate");

  // Split the multi-repo leaf into 2 single-repo child steps via the native dialog.
  await multiSection.getByRole("button", { name: "Split by repo" }).click();
  await expect(
    page.getByRole("heading", { name: "Split step by repository" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Create 2 child steps" }).click();
  await expect(multiSection).toHaveCount(0);

  // Both single-repo lanes now display their child steps.
  const contractsLane = page.getByRole("article", {
    name: `Repository lane ${contractsName}`,
  });
  const cpLane = page.getByRole("article", {
    name: `Repository lane ${controlPlaneName}`,
  });
  await expect(contractsLane).toContainText(
    `Adopt shared DTO contract (${contractsName})`,
  );
  await expect(cpLane).toContainText(
    `Adopt shared DTO contract (${controlPlaneName})`,
  );

  // Create Wave 1, add both repos, activate it, and freeze Tier 1 contracts.
  await page.getByRole("button", { name: "New wave", exact: true }).click();
  await page.getByLabel("Wave name").fill("Wave 1 Freeze");
  await page.getByRole("button", { name: "Create wave", exact: true }).click();

  const wavePanel = page.getByRole("region", { name: "Wave Wave 1 Freeze" });
  await expect(wavePanel).toBeVisible();

  // Add the contracts child step into Wave 1 (auto-adds contracts to wave repos), then control-plane child step too.
  await contractsLane
    .getByRole("button", { name: "Add to wave" })
    .first()
    .click();
  await expect(wavePanel).toContainText("0/1 steps done");
  await cpLane.getByRole("button", { name: "Add to wave" }).first().click();
  await expect(wavePanel).toContainText("0/2 steps done");

  // Activate the wave.
  await wavePanel
    .getByRole("combobox", { name: "Wave status" })
    .selectOption("active");
  await expect(wavePanel).toContainText("active");

  // Freeze Tier 1 contracts -> auto-seeds pending re-pin for control-plane!
  const tierPipeline = wavePanel.getByLabel("Dependency tier pipeline");
  await tierPipeline
    .locator(".exec-tier-repo")
    .filter({ hasText: contractsName })
    .getByRole("button", { name: "Freeze / batch" })
    .click();
  await tierPipeline
    .getByRole("combobox", { name: `Freeze state for ${contractsName}` })
    .selectOption("frozen");
  await tierPipeline
    .getByPlaceholder("e.g. v1.4.0 or commit SHA")
    .fill("contracts@v1.0.0");
  await tierPipeline
    .getByRole("button", { name: "Save freeze", exact: true })
    .click();

  // Consumer re-pin row appears for contracts -> control-plane, then human verifies it.
  const repinTable = wavePanel.getByRole("table");
  await expect(repinTable).toContainText(contractsName);
  await expect(repinTable).toContainText(controlPlaneName);
  await expect(repinTable).toContainText("pending");
  await repinTable.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(repinTable).toContainText("verified");

  // Verify Phase Outline preserved the phase hierarchy and shows the split children under the parent.
  await page.getByRole("link", { name: "Outline", exact: true }).click();
  const outline = page.getByRole("region", { name: "Plan outline" });
  await expect(outline).toContainText("Phase 1: Foundation");
  await expect(outline).toContainText(
    `Adopt shared DTO contract (${contractsName})`,
  );
  await expect(outline).toContainText(
    `Adopt shared DTO contract (${controlPlaneName})`,
  );
});
