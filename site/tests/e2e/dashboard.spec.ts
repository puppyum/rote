import { expect, test } from '@playwright/test';

test.describe('rote companion dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('acknowledgement banner is above the demo', async ({ page }) => {
    const banner = page.getByRole('heading', { name: /reimplementation of/i });
    await expect(banner).toBeVisible();
    const loopHeading = page.getByRole('heading', { name: /change one line/i });
    await expect(loopHeading).toBeVisible();
    const bannerBox = await banner.boundingBox();
    const loopBox = await loopHeading.boundingBox();
    expect(bannerBox).not.toBeNull();
    expect(loopBox).not.toBeNull();
    expect(bannerBox!.y).toBeLessThan(loopBox!.y);
  });

  test('edit-rerun timeline reacts to the stage picker', async ({ page }) => {
    const trainButton = page.locator('#loop').getByRole('button', { name: 'train' });
    await trainButton.click();
    await expect(page.getByText(/You edited\s+train/i)).toBeVisible();
  });

  test('speed comparison switches between vs-paper and vs-joblib', async ({ page }) => {
    const section = page.locator('#speed');
    await section.scrollIntoViewIfNeeded();
    await section.getByRole('button', { name: /vs joblib/i }).click();
    await expect(section.locator('text=/joblib wins/i').first()).toBeVisible();
    await section.getByRole('button', { name: /vs the 2011 paper/i }).click();
    await expect(section.getByText('~10×')).toBeVisible();
  });

  test('serializer chart toggles read/write', async ({ page }) => {
    const section = page.locator('#serializer');
    await section.scrollIntoViewIfNeeded();
    await section.getByRole('button', { name: /^read/i }).click();
    await expect(
      section.locator('thead').getByText(/rote serializer/i),
    ).toBeVisible();
  });

  test('call graph click invalidates downstream nodes', async ({ page }) => {
    const section = page.locator('#graph');
    await section.scrollIntoViewIfNeeded();
    await section.getByText('aggregate', { exact: true }).first().click();
    await expect(section.getByText(/You edited\s+aggregate/i)).toBeVisible();
  });

  test('AST hash editor: comment edit leaves hash unchanged', async ({ page }) => {
    const section = page.locator('#try');
    await section.scrollIntoViewIfNeeded();
    await expect(section.getByText(/Same as baseline/)).toBeVisible();
    await section.getByRole('button', { name: 'add a comment' }).click();
    await expect(section.getByText(/Same as baseline/)).toBeVisible();
  });

  test('AST hash editor: literal edit changes the hash', async ({ page }) => {
    const section = page.locator('#try');
    await section.scrollIntoViewIfNeeded();
    await section.getByRole('button', { name: 'change a literal' }).click();
    await page.waitForTimeout(200);
    await expect(section.getByText(/Different from baseline/)).toBeVisible();
  });

  test('file-dep inset: touch -r catches the rewind via ctime_ns', async ({ page }) => {
    const section = page.locator('#try');
    await section.scrollIntoViewIfNeeded();
    await section.getByRole('button', { name: /touch -r/i }).click();
    await expect(section.getByText('cache misses').first()).toBeVisible();
  });

  test('discrepancy log surfaces the file-dependency row', async ({ page }) => {
    await expect(
      page.locator('#delta').getByRole('cell', { name: /File-dependency identity/i }),
    ).toBeVisible();
  });
});
