import { expect, test } from "@playwright/test";

test("the queue shows every claim", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(26);
});

test("the instructions are shown read only", async ({ page }) => {
  await page.goto("/#/instructions");
  await expect(page.getByText("You are the UnderCover Agent")).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
});
