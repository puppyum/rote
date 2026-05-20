import { expect, test } from '@playwright/test';

test.describe('rote companion dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('acknowledgement banner is above the demo', async ({ page }) => {
    const banner = page.getByRole('heading', { level: 1 });
    await expect(banner).toBeVisible();
    const bannerSection = page.locator('header').first();
    await expect(bannerSection).toContainText(/IncPy/);
    await expect(bannerSection).toContainText(/Guo/);
    const loopHeading = page.locator('#loop').getByRole('heading');
    await expect(loopHeading).toBeVisible();
    const bannerBox = await banner.boundingBox();
    const loopBox = await loopHeading.boundingBox();
    expect(bannerBox).not.toBeNull();
    expect(loopBox).not.toBeNull();
    expect(bannerBox?.y ?? 0).toBeLessThan(loopBox?.y ?? 0);
  });

  test('edit-rerun timeline reacts to the stage picker', async ({ page }) => {
    const section = page.locator('#loop');
    await section.scrollIntoViewIfNeeded();
    await section
      .getByRole('group', { name: /Edit which stage/i })
      .getByRole('button', { name: 'train', exact: true })
      .click();
    const status = section.locator('p[aria-live="polite"]');
    await expect(status).toContainText('You edited');
    await expect(status.locator('strong')).toHaveText('train');
  });

  test('speed comparison switches between vs-paper and vs-joblib', async ({ page }) => {
    const section = page.locator('#speed');
    await section.scrollIntoViewIfNeeded();
    await section.getByRole('button', { name: /vs joblib/i }).click();
    await expect(section.locator('text[data-loses="true"]').first()).toBeVisible();
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
    await expect(section.getByText(/Matches the baseline/i)).toBeVisible();
    await section.getByRole('button', { name: 'add a comment' }).click();
    await expect(section.getByText(/Matches the baseline/i)).toBeVisible();
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
