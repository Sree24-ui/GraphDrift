/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        charcoal: {
          DEFAULT: '#1a1f24',
          light: '#242b32',
          lighter: '#2d363f',
        },
        teal: {
          muted: '#4a8f8f',
          accent: '#5fa8a8',
        },
        amber: {
          soft: '#d4a574',
        },
      },
    },
  },
  plugins: [],
}
