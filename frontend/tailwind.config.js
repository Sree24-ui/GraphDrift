/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: '#7dd3fc',
        'on-background': '#e0e8f0',
        'on-surface': '#e0e8f0',
        'on-surface-variant': '#a0b4c4',
        'on-primary': '#001f2e',
        secondary: '#88b4cc',
        tertiary: '#c8a0f0',
        error: '#ff6b6b',
        background: '#0a0e1a',
        surface: '#0f1524',
        'surface-bright': '#1a2438',
        'surface-container': '#141c2e',
        'surface-container-highest': '#202c42',
        'outline-variant': '#2a3a48',
        outline: '#4a6070',
        'surface-container-low': '#111828',
        'surface-container-high': '#1a2438',
        'surface-dim': '#0f1524',
        'surface-variant': '#1a2438',
        'primary-container': '#0e4d6e',
        'error-container': '#3d1414',
        // Legacy aliases (gradual migration)
        charcoal: {
          DEFAULT: '#0f1524',
          light: '#141c2e',
          lighter: '#1a2438',
        },
        teal: {
          muted: '#7dd3fc',
          accent: '#7dd3fc',
        },
        amber: {
          soft: '#c8a0f0',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        headline: ['Inter', 'system-ui', 'sans-serif'],
        body: ['Inter', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        xl: '1rem',
        '2xl': '1.5rem',
      },
      boxShadow: {
        glacier: '0 0 30px rgba(125, 211, 252, 0.05)',
        'glacier-lg': '0 0 40px rgba(125, 211, 252, 0.08)',
        'primary-glow': '0 0 15px rgba(125, 211, 252, 0.15)',
      },
    },
  },
  plugins: [],
}
