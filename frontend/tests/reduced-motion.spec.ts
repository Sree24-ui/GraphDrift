import { expect, test } from '@playwright/test'

import { signIn } from './session'

/**
 * The animation pass must disappear entirely for anyone who asked their OS for
 * less motion. Two mechanisms have to hold at once, so both are asserted:
 * the CSS gate for hand-written keyframes, and zeroed durations for the motion
 * components (MotionConfig's "user" mode keeps opacity animations by design,
 * which is not what "no animation" means here).
 */

test.describe('with prefers-reduced-motion: reduce', () => {
  test.use({ reducedMotion: 'reduce' })

  test('the browser reports the preference and CSS animations are disabled', async ({ page }) => {
    await signIn(page)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Live Monitor' })).toBeVisible()

    expect(
      await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches),
    ).toBe(true)

    // The live pulse is a CSS keyframe animation; the gate must flatten it.
    const pingDuration = await page.evaluate(() => {
      const dot = document.querySelector('.live-dot')
      if (!dot) return null
      return getComputedStyle(dot, '::after').animationDuration
    })
    expect(pingDuration === null || parseFloat(pingDuration) < 0.01).toBeTruthy()
  })

  test('an expanding queue row lands at its final height immediately', async ({ page }) => {
    await signIn(page)
    await page.goto('/alerts')
    await page.locator('[data-queue-row]').first().waitFor()
    await page.locator('[data-queue-row] button[aria-expanded]').first().click()

    // No settling time: with motion this row would still be part-way open.
    const height = await page
      .locator('[id^="ring-detail-"] > td > div')
      .first()
      .evaluate((el) => el.getBoundingClientRect().height)
    expect(height).toBeGreaterThan(80)
  })
})

test.describe('with motion allowed', () => {
  test.use({ reducedMotion: 'no-preference' })

  test('the same row starts collapsed and grows', async ({ page }) => {
    await signIn(page)
    await page.goto('/alerts')
    await page.locator('[data-queue-row]').first().waitFor()
    const wrapper = page.locator('[id^="ring-detail-"] > td > div').first()

    await page.locator('[data-queue-row] button[aria-expanded]').first().click()
    const early = await wrapper.evaluate((el) => el.getBoundingClientRect().height)
    await expect
      .poll(async () => wrapper.evaluate((el) => el.getBoundingClientRect().height))
      .toBeGreaterThan(80)
    const settled = await wrapper.evaluate((el) => el.getBoundingClientRect().height)

    // The animation is what makes these two differ; without it they are equal.
    expect(settled).toBeGreaterThan(early)
  })
})
