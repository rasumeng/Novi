/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        base: {
          950: '#131418',
          900: '#191a1f',
          850: '#1e1f25',
          800: '#24262c',
          750: '#2a2c33',
          700: '#32343c',
          600: '#454750',
          500: '#6b6d77',
          400: '#8f919b',
          300: '#b3b5bd',
          200: '#d4d5db',
          100: '#ececf0',
        },
accent: {
          DEFAULT: '#7A6EE0',
          soft: '#9B8EFF',
          muted: '#6358C0',
        },
        secondary: {
          DEFAULT: '#E8C868',
          soft: '#F0D98C',
          muted: '#B89E40',
        },
        ok: '#4A9A7A',
        warn: '#C49B4A',
        err: '#B04A5A',
      },
      fontFamily: {
        sans: ['"Inter"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        xl: '0.875rem',
        '2xl': '1.25rem',
      },
      boxShadow: {
        panel: '0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px rgba(0,0,0,0.35)',
      },
      keyframes: {
        fadeIn: { '0%': { opacity: 0, transform: 'translateY(4px)' }, '100%': { opacity: 1, transform: 'translateY(0)' } },
        shimmer: { '0%': { backgroundPosition: '-400px 0' }, '100%': { backgroundPosition: '400px 0' } },
        glow: { '0%, 100%': { opacity: .6 }, '50%': { opacity: 1 } },
      },
      animation: {
        fadeIn: 'fadeIn 0.25s ease-out',
        shimmer: 'shimmer 1.6s linear infinite',
        glow: 'glow 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
