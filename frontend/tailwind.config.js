/** @type {import('tailwindcss').Config} */
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: token('bg'),
        surface: token('surface'),
        surface2: token('surface2'),
        line: token('line'),
        fg: token('fg'),
        muted: token('muted'),
        faint: token('faint'),
        primary: token('primary'),
        onprimary: token('onprimary'),
        ok: token('ok'),
        warn: token('warn'),
        bad: token('bad'),
        info: token('info'),
        focus: token('focus'),
      },
      fontFamily: {
        sans: ['Geist', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"Geist Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
        display: ['"Instrument Serif"', 'Georgia', 'serif'],
      },
      keyframes: {
        slide: { '0%': { transform: 'translateX(-100%)' }, '100%': { transform: 'translateX(260%)' } },
        pop: { '0%': { transform: 'scale(.96)' }, '60%': { transform: 'scale(1.03)' }, '100%': { transform: 'scale(1)' } },
        ring: { '0%': { transform: 'scale(1)', opacity: '.7' }, '100%': { transform: 'scale(1.55)', opacity: '0' } },
        blink: { '0%, 49%': { opacity: '1' }, '50%, 100%': { opacity: '0' } },
        rise: { '0%': { transform: 'translateY(6px)', opacity: '0' }, '100%': { transform: 'translateY(0)', opacity: '1' } },
      },
      animation: {
        slide: 'slide 1.1s ease-in-out infinite',
        pop: 'pop .45s ease-out',
        ring: 'ring 1.4s ease-out infinite',
        blink: 'blink 1s steps(1) infinite',
        rise: 'rise .35s ease-out both',
      },
    },
  },
  plugins: [],
};
