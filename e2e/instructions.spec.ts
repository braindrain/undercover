import { expect, test } from "@playwright/test";

test("the claim agent's instructions are shown read only", async ({ page }) => {
  await page.goto("/#/instructions");
  await expect(
    page
      .getByRole("region", { name: "Working a claim" })
      .getByText(/You are given one claim/),
  ).toBeVisible();
  await expect(
    page.getByText("The policy wording", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("The claims manual", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
