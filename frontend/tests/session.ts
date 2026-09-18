import { expect, type Page } from '@playwright/test'

const USERNAME = process.env.A11Y_USERNAME ?? 'local-admin'
const PASSWORD = process.env.A11Y_PASSWORD ?? 'local-development-only'
const TOKEN_KEY = 'graphdrift.sessionToken'

// One login per worker: the backend rate-limits logins (10/minute by default),
// and a suite that signs in per test trips it as soon as it grows.
let cached: Promise<string> | null = null

export async function signIn(page: Page): Promise<void> {
  cached ??= page.request
    .post('/api/auth/login', { data: { username: USERNAME, password: PASSWORD } })
    .then(async (response) => {
      expect(response.ok(), 'seeded backend must accept the test login').toBeTruthy()
      return (await response.json()).access_token as string
    })
  const token = await cached
  await page.addInitScript(
    ([key, value]) => window.sessionStorage.setItem(key, value),
    [TOKEN_KEY, token] as const,
  )
}

export { TOKEN_KEY }
