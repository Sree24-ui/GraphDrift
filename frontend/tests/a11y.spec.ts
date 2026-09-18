import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import { signIn, TOKEN_KEY } from './session'

/**
 * Zero axe violations on every page an analyst uses.
 *
 * This was a manual audit once; it found a critical unlabelled slider and
 * `nested-interactive` on every queue row. Both are the kind of thing a
 * refactor reintroduces silently, so the scan runs in CI against the real
 * pages with real data — empty tables would pass while proving nothing.
 */



/**
 * Wait for transient animations to finish before scanning. Text part-way
 * through a fade measures as low contrast, which is a property of the frame
 * rather than of the page an analyst reads. Infinite animations (the live
 * pulse) are excluded, or this would never settle.
 */
async function settle(page: Page) {
  await page.waitForFunction(
    () =>
      document
        .getAnimations()
        .filter((animation) => {
          const timing = animation.effect?.getComputedTiming()
          return animation.playState === 'running' && timing?.iterations !== Infinity
        })
        .length === 0,
    null,
    { timeout: 5000 },
  )
}

async function scan(page: Page) {
  await settle(page)
  const { violations } = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'])
    .analyze()
  return violations.map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    nodes: violation.nodes.length,
    target: violation.nodes[0]?.target.join(' '),
  }))
}

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

test('live monitor has no accessibility violations', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Live Monitor' })).toBeVisible()
  await expect(page.getByRole('img', { name: /Transaction network graph/ })).toBeVisible()
  expect(await scan(page)).toEqual([])
})

test('alert queue (by ring) has no accessibility violations', async ({ page }) => {
  await page.goto('/alerts')
  await expect(page.locator('[data-queue-row]').first()).toBeVisible()
  expect(await scan(page)).toEqual([])
})

test('alert queue (by ring, expanded) has no accessibility violations', async ({ page }) => {
  await page.goto('/alerts')
  await page.locator('[data-queue-row] button[aria-expanded]').first().click()
  await expect(page.locator('[aria-expanded="true"]').first()).toBeVisible()
  expect(await scan(page)).toEqual([])
})

test('alert queue (by account) has no accessibility violations', async ({ page }) => {
  await page.goto('/alerts')
  await page.getByRole('button', { name: 'By Account' }).click()
  await expect(page.locator('[data-queue-row]').first()).toBeVisible()
  expect(await scan(page)).toEqual([])
})

test('account detail has no accessibility violations', async ({ page }) => {
  await page.goto('/alerts')
  await page.getByRole('button', { name: 'By Account' }).click()
  const account = page.locator('[data-queue-row] a[href^="/accounts/"]').first()
  await expect(account).toBeVisible()
  await account.click()
  await expect(page).toHaveURL(/\/accounts\//)
  await expect(page.getByRole('complementary', { name: 'Connected entities' })).toBeVisible()
  expect(await scan(page)).toEqual([])
})

test('reports has no accessibility violations', async ({ page }) => {
  await page.goto('/reports')
  await expect(page.getByRole('heading', { name: 'Performance Analytics' })).toBeVisible()
  expect(await scan(page)).toEqual([])
})

test('settings has no accessibility violations', async ({ page }) => {
  await page.goto('/settings')
  // Deliberately not the slider's label: the readiness check must not depend
  // on the thing the scan is here to catch.
  await expect(page.getByRole('heading', { name: 'Detection Sensitivity' })).toBeVisible()
  expect(await scan(page)).toEqual([])
})

test('login has no accessibility violations', async ({ page }) => {
  await page.context().clearCookies()
  await page.addInitScript((key) => window.sessionStorage.removeItem(key), TOKEN_KEY)
  await page.goto('/login')
  await expect(page.getByLabel('Username')).toBeVisible()
  expect(await scan(page)).toEqual([])
})
